from datetime import UTC, datetime, date

from github_member_activity.collector import collect
from github_member_activity.config import AppConfig
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
