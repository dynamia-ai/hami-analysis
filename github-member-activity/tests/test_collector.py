from datetime import UTC, datetime, date
from zoneinfo import ZoneInfo

import pytest

from github_member_activity.collector import DISCOVERY_QUERY, HYDRATE_QUERY, ISSUE_COMMENT_DISCOVERY_QUERY, REVIEWS_QUERY, _commit_snapshot, _record_final_gate_failure, collect
from github_member_activity.config import AppConfig, RepositoryPolicyConfig
from github_member_activity.github_client import GitHubRequestError, SearchPage
from github_member_activity.manifest import _validate_status
from github_member_activity.models import SourceStatus
from github_member_activity.period import build_period


class EmptyGitHub:
    def graphql(self, query, variables):
        if "user(login" in query and "issueComments" not in query and "pullRequestReviewContributions" not in query:
            return {"user": {"__typename": "User", "id": "U_1", "login": "Alice"}}
        raise AssertionError("unexpected graphql call")

    def search(self, query, *, page=1):
        return SearchPage((), 0, False)

    def connection(self, query, variables, path):
        return []


class SearchActorMismatchGitHub:
    def graphql(self, query, variables):
        if "user(login" in query:
            return {"user": {"__typename": "User", "id": "U_1", "login": "Alice"}}
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": []}}}

    def search(self, query, *, page=1):
        return SearchPage(({"node_id": "N1", "actor_node_id": "OTHER", "created_at": "2026-01-02T00:00:00Z"},), 1, False)

    def connection(self, query, variables, path):
        return []


class SearchSnapshotChangingGitHub(SearchActorMismatchGitHub):
    def __init__(self):
        self.search_calls = 0

    def search(self, query, *, page=1):
        self.search_calls += 1
        created_at = "2026-01-02T00:00:00Z" if self.search_calls % 2 else "2026-01-03T00:00:00Z"
        return SearchPage(({"node_id": "N1", "actor_node_id": "U_1", "created_at": created_at},), 1, False)


class EmptyHydrationGitHub:
    def graphql(self, query, variables):
        if "user(login" in query:
            return {"user": {"__typename": "User", "id": "U_1", "login": "Alice"}}
        if "nodes(ids:$ids)" in query:
            return {"nodes": []}
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": []}}}

    def search(self, query, *, page=1):
        return SearchPage(({"node_id": "N1", "actor_node_id": "U_1", "created_at": "2026-01-02T00:00:00Z"},), 1, False)

    def connection(self, query, variables, path):
        return []


class DiscoveryGraphQLFailureGitHub(EmptyHydrationGitHub):
    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query:
            raise GitHubRequestError("graphql_partial_response")
        return super().graphql(query, variables)


class HydrationGraphQLFailureGitHub(EmptyHydrationGitHub):
    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query and "repository { id visibility owner { id } }" in query:
            return {"nodes": [{"__typename": "PullRequest", "id": "N1", "author": {"__typename": "User", "id": "U_1"}, "createdAt": "2026-01-02T00:00:00Z", "mergedAt": "2026-01-02T00:00:00Z", "repository": {"id": "R1", "visibility": "PUBLIC", "owner": {"id": "O1"}}}]}
        if "nodes(ids:$ids)" in query:
            raise GitHubRequestError("graphql_partial_response")
        return super().graphql(query, variables)


def _discovery_node(visibility="PUBLIC", created_at="2026-01-02T00:00:00Z"):
    return {"__typename": "PullRequest", "id": "N1", "author": {"__typename": "User", "id": "U_1"}, "createdAt": created_at, "mergedAt": created_at, "repository": {"id": "R1", "visibility": visibility, "owner": {"id": "O1"}}}


class UnstableDiscoveryGitHub(EmptyHydrationGitHub):
    def __init__(self):
        self.discovery_calls = 0

    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query:
            self.discovery_calls += 1
            return {"nodes": [_discovery_node(created_at="2026-01-02T00:00:00Z" if self.discovery_calls == 1 else "2026-01-03T00:00:00Z")]}
        return super().graphql(query, variables)


class PrivateDiscoveryGitHub(EmptyHydrationGitHub):
    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query:
            return {"nodes": [_discovery_node(visibility="PRIVATE")]}
        return super().graphql(query, variables)


class HydrationCardinalityGitHub(EmptyHydrationGitHub):
    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query and "nameWithOwner" in query:
            return {"nodes": []}
        if "nodes(ids:$ids)" in query:
            return {"nodes": [_discovery_node()]}
        return super().graphql(query, variables)


class IssueSnapshotChangingGitHub(EmptyHydrationGitHub):
    def __init__(self):
        self.issue_connection_calls = 0
        self.current_created = "2026-01-02T00:00:00Z"

    def connection(self, query, variables, path):
        if path == ("user", "issueComments"):
            self.issue_connection_calls += 1
            self.current_created = "2026-01-02T00:00:00Z" if self.issue_connection_calls == 1 else "2026-01-03T00:00:00Z"
            return [{"__typename": "IssueComment", "id": "C1", "author": {"__typename": "User", "id": "U_1"}, "createdAt": self.current_created, "updatedAt": self.current_created, "pullRequest": None, "issue": {"id": "I1"}, "repository": {"id": "R1", "visibility": "PUBLIC", "owner": {"id": "O1"}}}]
        return []

    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query and "IssueComment" in query:
            return {"nodes": [{"__typename": "IssueComment", "id": "C1", "author": {"__typename": "User", "id": "U_1"}, "createdAt": self.current_created, "updatedAt": self.current_created, "pullRequest": None, "issue": {"id": "I1"}, "repository": {"id": "R1", "visibility": "PUBLIC", "owner": {"id": "O1"}}}]}
        if "nodes(ids:$ids)" in query and "Issue" in query:
            return {"nodes": [{"__typename": "Issue", "id": "I1", "number": 1, "repository": {"id": "R1", "nameWithOwner": "owner/repo", "visibility": "PUBLIC", "owner": {"id": "O1", "login": "owner"}}}]}
        return super().graphql(query, variables)


class IssueLateValidationFailureGitHub(IssueSnapshotChangingGitHub):
    def __init__(self):
        super().__init__()
        self.current_created = "2026-01-02T00:00:00Z"

    def connection(self, query, variables, path):
        if path == ("user", "issueComments"):
            return [{"__typename": "IssueComment", "id": "C1", "author": {"__typename": "User", "id": "U_1"}, "createdAt": self.current_created, "updatedAt": self.current_created, "pullRequest": None, "issue": {"id": "I1"}, "repository": {"id": "R1", "visibility": "PUBLIC", "owner": {"id": "O1"}}}]
        return []

    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query and "Issue" in query and "IssueComment" not in query:
            return {"nodes": [{"__typename": "Issue", "id": "I1", "number": 1, "repository": {"id": "R1", "nameWithOwner": "invalid", "visibility": "PUBLIC", "owner": {"id": "O1", "login": "owner"}}}]}
        return super().graphql(query, variables)


class ReviewSnapshotChangingGitHub(EmptyHydrationGitHub):
    def __init__(self):
        self.review_calls = 0
        self.current_submitted = "2026-01-02T00:00:00Z"

    def connection(self, query, variables, path):
        if path == ("user", "contributionsCollection", "pullRequestReviewContributions"):
            return [{"isRestricted": False, "user": {"__typename": "User", "id": "U_1"}, "pullRequest": {"id": "P1"}}]
        if path == ("node", "reviews"):
            self.review_calls += 1
            self.current_submitted = "2026-01-02T00:00:00Z" if self.review_calls == 1 else "2026-01-03T00:00:00Z"
            return [{"__typename": "PullRequestReview", "id": "RV1", "author": {"__typename": "User", "id": "U_1"}, "state": "APPROVED", "submittedAt": self.current_submitted}]
        return []

    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query:
            return {"nodes": [{"__typename": "PullRequest", "id": "P1", "repository": {"id": "R1", "visibility": "PUBLIC", "owner": {"id": "O1"}}}]}
        return super().graphql(query, variables)


class ReviewLateVisibilityFailureGitHub(ReviewSnapshotChangingGitHub):
    def __init__(self):
        super().__init__()
        self.current_submitted = "2026-01-02T00:00:00Z"

    def connection(self, query, variables, path):
        if path == ("user", "contributionsCollection", "pullRequestReviewContributions"):
            return [{"isRestricted": False, "user": {"__typename": "User", "id": "U_1"}, "pullRequest": {"id": "P1"}}]
        if path == ("node", "reviews"):
            return [{"__typename": "PullRequestReview", "id": "RV1", "author": {"__typename": "User", "id": "U_1"}, "state": "APPROVED", "submittedAt": "2026-01-02T00:00:00Z"}]
        return []

    def graphql(self, query, variables):
        if "nodes(ids:$ids)" in query and "nameWithOwner" in query:
            return {"nodes": [{"__typename": "PullRequest", "id": "P1", "number": 1, "repository": {"id": "R1", "nameWithOwner": "owner/repo", "visibility": "PRIVATE", "owner": {"id": "O1", "login": "owner"}}}]}
        return super().graphql(query, variables)


class CommitPartitionGitHub:
    def __init__(self, *, always_next=False, terminal_count=1):
        self.calls = []
        self.always_next = always_next
        self.terminal_count = terminal_count

    def graphql(self, query, variables):
        self.calls.append(variables)
        if "nameWithOwner" in query and "commitContributionsByRepository" not in query:
            return {"nodes": [{"__typename": "Repository", "id": "R1", "nameWithOwner": "dynamia-ai/demo", "visibility": "PUBLIC", "owner": {"id": "O1", "login": "dynamia-ai"}}]}
        day = variables["from"][:10]
        after = variables.get("after")
        has_next = self.always_next or (after is None and day == "2026-01-01" and variables["to"][:10] == "2026-01-02")
        count = 2 if day == "2026-01-01" and variables["to"][:10] == "2026-01-02" else self.terminal_count
        edge_day = day if after is None else "2026-01-02"
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": [{
            "repository": {"id": "R1", "nameWithOwner": "dynamia-ai/demo", "visibility": "PUBLIC", "owner": {"id": "O1", "login": "dynamia-ai"}},
            "contributions": {"totalCount": count, "edges": [{"cursor": "c1" if after is None else "c2", "node": {"__typename": "CreatedCommitContribution", "isRestricted": False, "occurredAt": f"{edge_day}T12:00:00Z", "commitCount": 1 if after is not None else count if not has_next else 1, "user": {"__typename": "User", "id": "U1"}, "repository": {"id": "R1"}}}], "pageInfo": {"hasNextPage": has_next, "endCursor": "c1" if has_next else None}},
        }]}}}


class RestrictedCommitGitHub:
    def graphql(self, query, variables):
        if "nameWithOwner" in query and "commitContributionsByRepository" not in query:
            raise AssertionError("restricted contribution must not be hydrated")
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": [{
            "repository": {"id": "R-private", "visibility": "PRIVATE", "owner": {"id": "O-private"}},
            "contributions": {"totalCount": 999, "edges": [{"cursor": "c1", "node": {"__typename": "CreatedCommitContribution", "isRestricted": True}}], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }]}}}


class DuplicateCommitDayGitHub:
    def graphql(self, query, variables):
        if "nameWithOwner" in query and "commitContributionsByRepository" not in query:
            raise AssertionError("duplicate day must fail before hydration")
        node = {"__typename": "CreatedCommitContribution", "isRestricted": False, "occurredAt": "2026-01-01T12:00:00Z", "user": {"__typename": "User", "id": "U1"}, "repository": {"id": "R1"}}
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": [{
            "repository": {"id": "R1", "visibility": "PUBLIC", "owner": {"id": "O1"}},
            "contributions": {"totalCount": 3, "edges": [{"cursor": "c1", "node": {**node, "commitCount": 1}}, {"cursor": "c2", "node": {**node, "commitCount": 2}}], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }]}}}


class EmptyCommitGroupGitHub:
    def graphql(self, query, variables):
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": [{
            "repository": {"id": "R-empty", "visibility": "PUBLIC", "owner": {"id": "O-empty"}},
            "contributions": {"totalCount": 0, "edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }]}}}


class MultiRepositoryCommitGitHub:
    def graphql(self, query, variables):
        if "nameWithOwner" in query and "commitContributionsByRepository" not in query:
            return {"nodes": [{"__typename": "Repository", "id": repo_id, "nameWithOwner": f"owner/{repo_id.lower()}", "visibility": "PUBLIC", "owner": {"id": f"O-{repo_id}", "login": "owner"}} for repo_id in ("R1", "R2")]}
        after = variables.get("after")
        target = after.rsplit("-", 1)[0] if after else None
        groups = []
        for repo_id in ("R1", "R2"):
            terminal = target == repo_id
            edge = {"cursor": f"{repo_id}-c2" if terminal else f"{repo_id}-c1", "node": {"__typename": "CreatedCommitContribution", "isRestricted": False, "occurredAt": f"2026-01-02T12:00:00Z" if terminal else "2026-01-01T12:00:00Z", "commitCount": 1, "user": {"__typename": "User", "id": "U1"}, "repository": {"id": repo_id}}}
            groups.append({"repository": {"id": repo_id, "visibility": "PUBLIC", "owner": {"id": f"O-{repo_id}"}}, "contributions": {"totalCount": 2, "edges": [edge], "pageInfo": {"hasNextPage": not terminal, "endCursor": None if terminal else f"{repo_id}-c1"}}})
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": groups}}}


def test_commit_inner_connection_is_repartitioned_instead_of_truncated():
    client = CommitPartitionGitHub()
    rows = _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC), RepositoryPolicyConfig())
    assert [(row["repo"].node_id, row["day"]) for row in rows] == [("R1", "2026-01-01"), ("R1", "2026-01-02")]
    assert len(client.calls) == 3


def test_commit_inner_connection_at_one_day_is_unavailable():
    client = CommitPartitionGitHub(always_next=True)
    with pytest.raises(RuntimeError, match="commit_context_unavailable"):
        _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig())


def test_commit_aggregate_edge_accepts_total_commit_count_not_edge_count():
    client = CommitPartitionGitHub(terminal_count=5)
    rows = _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig())
    assert [(row["repo"].node_id, row["quantity"]) for row in rows] == [("R1", 5)]


def test_commit_envelope_uses_effective_local_dates_for_non_utc_zone():
    client = CommitPartitionGitHub()
    _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai")), datetime(2026, 1, 3, tzinfo=ZoneInfo("Asia/Shanghai")), RepositoryPolicyConfig())
    assert client.calls[0]["from"] == "2026-01-01T00:00:00Z"
    assert client.calls[0]["to"] == "2026-01-02T23:59:59Z"


def test_restricted_commit_contribution_is_not_counted_or_hydrated():
    assert _commit_snapshot(RestrictedCommitGitHub(), "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig()) == []


def test_duplicate_commit_day_fails_before_hydration():
    with pytest.raises(RuntimeError, match="graphql_cardinality_mismatch"):
        _commit_snapshot(DuplicateCommitDayGitHub(), "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig())


def test_empty_commit_group_is_not_accepted_as_complete_zero_context():
    with pytest.raises(RuntimeError, match="commit_context_unavailable"):
        _commit_snapshot(EmptyCommitGroupGitHub(), "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig())


def test_multi_repository_commit_connections_page_independently():
    rows = _commit_snapshot(MultiRepositoryCommitGitHub(), "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC), RepositoryPolicyConfig())
    assert {(row["repo"].node_id, row["day"]) for row in rows} == {(repo, day) for repo in ("R1", "R2") for day in ("2026-01-01", "2026-01-02")}


def test_empty_public_snapshot_is_complete_not_zero_guess_for_core_sources():
    config = AppConfig.model_validate({
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN"}, "period": {"timezone": "UTC"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2020, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": []}, "output": {"directory": "./output"},
    })
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-08T00:00:00Z")
    result = collect(config, period, EmptyGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    assert all(row.status == "complete" for row in result.statuses if row.criticality == "core")
    assert result.events == []


def test_search_actor_mismatch_is_failed_conflict_not_partial_snapshot():
    config = AppConfig.model_validate({
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN"}, "period": {"timezone": "UTC"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2020, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": []}, "output": {"directory": "./output"},
    })
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-08T00:00:00Z")
    result = collect(config, period, SearchActorMismatchGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        assert (rows[source].status, rows[source].reason) == ("failed", "search_candidate_conflict")


def test_search_proof_survives_failed_discovery_without_hydration_overwrite():
    config = AppConfig.model_validate({
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN"}, "period": {"timezone": "UTC"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2020, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": []}, "output": {"directory": "./output"},
    })
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-08T00:00:00Z")
    result = collect(config, period, EmptyHydrationGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason) == ("failed", "visibility_unverified")
        assert (row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == (True, True, False, False, None)


def test_discovery_graphql_failure_is_source_partial_and_collection_continues():
    config = AppConfig.model_validate({
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN"}, "period": {"timezone": "UTC"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2020, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": []}, "output": {"directory": "./output"},
    })
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-08T00:00:00Z")
    result = collect(config, period, DiscoveryGraphQLFailureGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete) == ("partial", "graphql_partial_response", True, True, False, None)
    assert rows["issue_replies"].status == "complete"
    assert rows["prs_reviewed"].status == "complete"


def test_hydration_graphql_failure_preserves_stable_snapshot_proof():
    config = AppConfig.model_validate({
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN"}, "period": {"timezone": "UTC"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2020, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": []}, "output": {"directory": "./output"},
    })
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-08T00:00:00Z")
    result = collect(config, period, HydrationGraphQLFailureGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete) == ("partial", "graphql_partial_response", True, True, True, False)
        assert row.snapshot_completed_at is not None


def _proof_fixture():
    config = AppConfig.model_validate({
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN"}, "period": {"timezone": "UTC"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2020, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": []}, "output": {"directory": "./output"},
    })
    return config, build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-08T00:00:00Z")


def test_actor_mismatch_retains_completed_search_proof():
    config, period = _proof_fixture()
    result = collect(config, period, SearchActorMismatchGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == ("failed", "search_candidate_conflict", True, True, False, None, None)


def test_search_snapshot_mismatch_retains_completed_pagination_proof():
    config, period = _proof_fixture()
    result = collect(config, period, SearchSnapshotChangingGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == ("partial", "search_snapshot_unstable", True, True, False, None, None)


def test_discovery_snapshot_instability_keeps_visibility_unattempted():
    config, period = _proof_fixture()
    result = collect(config, period, UnstableDiscoveryGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == ("partial", "graphql_snapshot_unstable", True, True, False, None, None)


def test_private_discovery_retains_stable_snapshot_timestamp():
    config, period = _proof_fixture()
    result = collect(config, period, PrivateDiscoveryGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete) == ("failed", "visibility_unverified", True, True, True, False)
        assert row.snapshot_completed_at is not None


def test_hydration_cardinality_failure_retains_stable_snapshot_timestamp():
    config, period = _proof_fixture()
    result = collect(config, period, HydrationCardinalityGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    rows = {row.source: row for row in result.statuses if row.member_id == "alice"}
    for source in ("prs_opened", "issues_opened", "authored_prs_merged"):
        row = rows[source]
        assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete) == ("failed", "visibility_unverified", True, True, True, False)
        assert row.snapshot_completed_at is not None


def test_issue_snapshot_mismatch_retains_completed_connection_proof():
    config, period = _proof_fixture()
    result = collect(config, period, IssueSnapshotChangingGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    row = next(row for row in result.statuses if row.member_id == "alice" and row.source == "issue_replies")
    assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == ("partial", "graphql_snapshot_unstable", True, None, False, None, None)


def test_issue_late_validation_failure_retains_snapshot_proof():
    config, period = _proof_fixture()
    result = collect(config, period, IssueLateValidationFailureGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    row = next(row for row in result.statuses if row.member_id == "alice" and row.source == "issue_replies")
    assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete) == ("failed", "api_contract_violation", True, None, True, False)
    assert row.snapshot_completed_at is not None


def test_review_snapshot_mismatch_retains_completed_connection_proof():
    config, period = _proof_fixture()
    result = collect(config, period, ReviewSnapshotChangingGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    row = next(row for row in result.statuses if row.member_id == "alice" and row.source == "prs_reviewed")
    assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == ("partial", "graphql_snapshot_unstable", True, None, False, None, None)


def test_review_late_visibility_failure_retains_snapshot_proof():
    config, period = _proof_fixture()
    result = collect(config, period, ReviewLateVisibilityFailureGitHub(), observed_at=datetime(2026, 1, 10, tzinfo=UTC))
    row = next(row for row in result.statuses if row.member_id == "alice" and row.source == "prs_reviewed")
    assert (row.status, row.reason, row.pagination_complete, row.partition_complete, row.snapshot_complete, row.visibility_complete) == ("failed", "visibility_unverified", True, None, True, False)
    assert row.snapshot_completed_at is not None


def test_final_visibility_gate_preserves_completed_source_proof_on_graphql_partial():
    sources = ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")
    statuses = [SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2026-01-02T00:00:00Z") for source in sources]
    _record_final_gate_failure(statuses, "alice", "issue_replies", "graphql_partial_response")
    row = next(row for row in statuses if row.source == "issue_replies")
    assert (row.status, row.pagination_complete, row.snapshot_complete, row.visibility_complete, row.snapshot_completed_at) == ("partial", True, True, False, "2026-01-02T00:00:00Z")
    _validate_status({"schema_version": "1.0", "rows": [row.to_dict() for row in statuses]})


def test_graphql_actor_author_uses_user_fragment_for_schema_compatibility():
    for query in (DISCOVERY_QUERY, HYDRATE_QUERY, ISSUE_COMMENT_DISCOVERY_QUERY, REVIEWS_QUERY):
        assert "author { __typename id }" not in query
        assert "... on User { id }" in query
