from datetime import UTC, datetime, date
from zoneinfo import ZoneInfo

import pytest

from github_member_activity.collector import DISCOVERY_QUERY, HYDRATE_QUERY, ISSUE_COMMENT_DISCOVERY_QUERY, REVIEWS_QUERY, _commit_snapshot, _record_final_gate_failure, collect
from github_member_activity.config import AppConfig, RepositoryPolicyConfig
from github_member_activity.github_client import SearchPage
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
