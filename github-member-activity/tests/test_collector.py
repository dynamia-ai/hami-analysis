from datetime import UTC, datetime, date

import pytest

from github_member_activity.collector import _commit_snapshot, collect
from github_member_activity.config import AppConfig, RepositoryPolicyConfig
from github_member_activity.github_client import SearchPage
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
        has_next = self.always_next or (day == "2026-01-01" and variables["to"][:10] == "2026-01-02")
        count = 2 if has_next else self.terminal_count
        return {"user": {"contributionsCollection": {"commitContributionsByRepository": [{
            "repository": {"id": "R1", "nameWithOwner": "dynamia-ai/demo", "visibility": "PUBLIC", "owner": {"id": "O1", "login": "dynamia-ai"}},
            "contributions": {"totalCount": count, "edges": [{"cursor": "c1", "node": {"__typename": "CreatedCommitContribution", "isRestricted": False, "occurredAt": f"{day}T12:00:00Z", "commitCount": count, "user": {"__typename": "User", "id": "U1"}, "repository": {"id": "R1"}}}], "pageInfo": {"hasNextPage": has_next, "endCursor": "c1"}},
        }]}}}


def test_commit_inner_connection_is_repartitioned_instead_of_truncated():
    client = CommitPartitionGitHub()
    rows = _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC), RepositoryPolicyConfig())
    assert [(row["repo"].node_id, row["day"]) for row in rows] == [("R1", "2026-01-01"), ("R1", "2026-01-02")]
    assert len(client.calls) == 5


def test_commit_inner_connection_at_one_day_is_unavailable():
    client = CommitPartitionGitHub(always_next=True)
    with pytest.raises(RuntimeError, match="commit_context_unavailable"):
        _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig())


def test_commit_aggregate_edge_accepts_total_commit_count_not_edge_count():
    client = CommitPartitionGitHub(terminal_count=5)
    rows = _commit_snapshot(client, "Alice", "U1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), RepositoryPolicyConfig())
    assert [(row["repo"].node_id, row["quantity"]) for row in rows] == [("R1", 5)]


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
