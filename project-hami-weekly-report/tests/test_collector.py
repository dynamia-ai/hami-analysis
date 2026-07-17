import logging
from datetime import UTC, datetime
from threading import Lock
from time import sleep
from typing import Any

from hami_github_activity.collector import ActivityCollector
from hami_github_activity.date_range import build_scan_period
from hami_github_activity.github_client import GitHubRequestError, SearchResult


PERIOD = build_scan_period(
    days=7,
    timezone="Asia/Shanghai",
    now=datetime(2026, 7, 16, 12, tzinfo=UTC),
)


def candidate(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "repository_url": "https://api.github.com/repos/Project-HAMi/HAMi",
        "html_url": f"https://github.com/Project-HAMi/HAMi/issues/{number}",
    }


def issue_detail(number: int, **updates: Any) -> dict[str, Any]:
    base = {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://github.com/Project-HAMi/HAMi/issues/{number}",
        "state": "open",
        "state_reason": None,
        "user": {"login": "reporter", "type": "User"},
        "author_association": "NONE",
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-15T00:00:00Z",
        "closed_at": None,
        "labels": [{"name": "bug"}],
        "assignees": [],
        "milestone": None,
        "body": "details",
    }
    base.update(updates)
    return base


def pr_detail(number: int, **updates: Any) -> dict[str, Any]:
    base = issue_detail(number)
    base.update(
        {
            "html_url": f"https://github.com/Project-HAMi/HAMi/pull/{number}",
            "draft": False,
            "merged": False,
            "merged_at": None,
            "base": {"ref": "master"},
            "head": {"ref": "fix"},
            "requested_reviewers": [],
            "mergeable": True,
            "additions": 10,
            "deletions": 2,
            "changed_files": 1,
        }
    )
    base.update(updates)
    return base


class FakeClient:
    def __init__(self, issue_candidates: list[dict[str, Any]], pr_candidates: list[dict[str, Any]]) -> None:
        self.issue_candidates = issue_candidates
        self.pr_candidates = pr_candidates
        self.details: dict[str, dict[str, Any]] = {}
        self.lists: dict[str, list[dict[str, Any]] | Exception] = {}
        self.list_calls: list[tuple[str, dict[str, Any]]] = []
        self.search_queries: list[str] = []
        self.failed_requests = 0
        self.rate_limit_remaining = 99

    def search_issues(self, query: str) -> SearchResult:
        self.search_queries.append(query)
        items = self.pr_candidates if "is:pr" in query else self.issue_candidates
        return SearchResult(items=items, total_count=len(items), capped=False)

    def get_json(self, path: str, **_: Any) -> dict[str, Any]:
        value = self.details.get(path)
        if value is None:
            self.failed_requests += 1
            raise GitHubRequestError("missing fixture", url=path)
        return value

    def get_paginated(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append((path, kwargs))
        value = self.lists.get(path, [])
        if isinstance(value, Exception):
            self.failed_requests += 1
            raise value
        return value


def test_issue_classification_and_activity_counts() -> None:
    client = FakeClient([candidate(1)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/1"] = issue_detail(
        1, created_at="2026-07-10T00:00:00Z", closed_at="2026-07-15T00:00:00Z", state="closed"
    )
    client.lists["/repos/Project-HAMi/HAMi/issues/1/comments"] = [
        {
            "user": {"login": "maintainer", "type": "User"},
            "author_association": "MEMBER",
            "body": "human",
            "created_at": "2026-07-14T00:00:00Z",
            "updated_at": "2026-07-14T00:00:00Z",
            "html_url": "https://example.test/comment/1",
        },
        {
            "user": {"login": "dependabot[bot]", "type": "Bot"},
            "author_association": "NONE",
            "body": "bot",
            "created_at": "2026-07-15T00:00:00Z",
            "updated_at": "2026-07-15T00:00:00Z",
        },
    ]
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    issue = result.issues[0]
    assert issue.created_in_period and issue.closed_in_period
    assert issue.period_human_activity_count == 1
    assert issue.comments[0].maintainer is True
    assert issue.comments[1].bot is True


def test_pull_request_open_closed_and_merged_classification() -> None:
    candidates = [candidate(2), candidate(3), candidate(4)]
    client = FakeClient([], candidates)
    client.details["/repos/Project-HAMi/HAMi/pulls/2"] = pr_detail(2)
    client.details["/repos/Project-HAMi/HAMi/pulls/3"] = pr_detail(
        3, state="closed", closed_at="2026-07-14T00:00:00Z"
    )
    client.details["/repos/Project-HAMi/HAMi/pulls/4"] = pr_detail(
        4,
        state="closed",
        merged=True,
        closed_at="2026-07-14T00:00:00Z",
        merged_at="2026-07-14T00:00:00Z",
    )
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    by_number = {item.number: item for item in result.pull_requests}
    assert by_number[2].state == "open"
    assert by_number[3].closed_unmerged_in_period is True
    assert by_number[4].merged_in_period is True
    assert by_number[4].closed_unmerged_in_period is False


def test_review_and_review_comment_period_detection() -> None:
    client = FakeClient([], [candidate(5)])
    client.details["/repos/Project-HAMi/HAMi/pulls/5"] = pr_detail(5)
    client.lists["/repos/Project-HAMi/HAMi/pulls/5/reviews"] = [
        {
            "user": {"login": "reviewer", "type": "User"},
            "author_association": "COLLABORATOR",
            "state": "APPROVED",
            "submitted_at": "2026-07-13T00:00:00Z",
            "body": "looks good",
            "html_url": "https://example.test/review/1",
        }
    ]
    client.lists["/repos/Project-HAMi/HAMi/pulls/5/comments"] = [
        {
            "user": {"login": "reviewer", "type": "User"},
            "author_association": "COLLABORATOR",
            "created_at": "2026-07-13T01:00:00Z",
            "updated_at": "2026-07-13T01:00:00Z",
            "body": "nit",
            "path": "pkg/a.go",
            "line": 10,
        }
    ]
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    pr = result.pull_requests[0]
    assert pr.reviews[0].in_period is True
    assert pr.review_comments[0].in_period is True
    assert pr.period_human_activity_count == 2
    since = "2026-07-09T16:00:00Z"
    calls = dict(client.list_calls)
    assert calls["/repos/Project-HAMi/HAMi/issues/5/comments"] == {"params": {"since": since}}
    assert calls["/repos/Project-HAMi/HAMi/pulls/5/reviews"] == {}
    assert calls["/repos/Project-HAMi/HAMi/pulls/5/comments"] == {"params": {"since": since}}


def test_single_endpoint_failure_is_warning_and_collection_continues() -> None:
    client = FakeClient([candidate(6), candidate(7)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/6"] = issue_detail(6)
    client.details["/repos/Project-HAMi/HAMi/issues/7"] = issue_detail(7)
    client.lists["/repos/Project-HAMi/HAMi/issues/6/comments"] = GitHubRequestError(
        "forbidden", url="https://api.github.test/comments"
    )
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert [item.number for item in result.issues] == [6, 7]
    assert result.failed_requests == 1
    assert len(result.warnings) == 1
    assert "Could not collect comment data" in result.issues[0].data_gaps[0]


def test_empty_results_and_outside_period_item() -> None:
    client = FakeClient([candidate(8)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/8"] = issue_detail(
        8,
        created_at="2026-06-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
    )
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert result.issues == []
    assert result.pull_requests == []


def test_issue_with_only_unexplained_updated_at_match_is_excluded() -> None:
    client = FakeClient([candidate(9)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/9"] = issue_detail(
        9,
        created_at="2026-06-01T00:00:00Z",
    )
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert result.issues == []
    assert result.unexplained_updated_excluded_count == 1


def test_pull_request_with_only_unexplained_updated_at_match_is_excluded() -> None:
    client = FakeClient([], [candidate(18)])
    client.details["/repos/Project-HAMi/HAMi/pulls/18"] = pr_detail(
        18,
        created_at="2026-04-19T18:23:08Z",
        updated_at="2026-07-12T21:24:39Z",
        state="closed",
        merged=True,
        merged_at="2026-04-20T06:37:40Z",
        closed_at="2026-04-20T06:37:41Z",
    )
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert result.pull_requests == []
    assert result.unexplained_updated_excluded_count == 1


def test_activity_endpoint_failure_has_separate_exclusion_count() -> None:
    client = FakeClient([candidate(19)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/19"] = issue_detail(
        19,
        created_at="2026-06-01T00:00:00Z",
    )
    client.lists["/repos/Project-HAMi/HAMi/issues/19/comments"] = GitHubRequestError(
        "service unavailable",
        url="https://api.github.test/issues/19/comments",
    )
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert result.issues == []
    assert result.unexplained_updated_excluded_count == 0
    assert result.activity_failure_excluded_count == 1
    assert result.warnings[0].scope == "issue:Project-HAMi/HAMi#19"


def test_collection_logs_search_and_item_progress(caplog: object) -> None:
    client = FakeClient([candidate(10)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/10"] = issue_detail(10)
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]
    ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    messages = [record.getMessage() for record in caplog.records]  # type: ignore[attr-defined]
    assert (
        "Searching GitHub for issue candidates: "
        "org:Project-HAMi is:issue updated:2026-07-10..2026-07-16"
    ) in messages
    assert "Found 1 issue candidates" in messages
    assert "Collecting issue 1/1: Project-HAMi/HAMi#10" in messages
    assert "Collection finished: 1 issues and 0 pull requests retained" in messages


def test_issue_comments_are_scoped_to_utc_plus_eight_period() -> None:
    client = FakeClient([candidate(11)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/11"] = issue_detail(11)
    ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert client.list_calls == [
        (
            "/repos/Project-HAMi/HAMi/issues/11/comments",
            {"params": {"since": "2026-07-09T16:00:00Z"}},
        )
    ]


def test_existing_comment_updated_in_period_is_retained_as_activity() -> None:
    client = FakeClient([candidate(16)], [])
    client.details["/repos/Project-HAMi/HAMi/issues/16"] = issue_detail(16)
    client.lists["/repos/Project-HAMi/HAMi/issues/16/comments"] = [
        {
            "user": {"login": "maintainer", "type": "User"},
            "author_association": "MEMBER",
            "body": "edited old comment",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-13T00:00:00Z",
        }
    ]
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    comment = result.issues[0].comments[0]
    assert comment.in_period is False
    assert comment.updated_in_period is True
    assert comment.active_in_period is True
    assert result.issues[0].period_human_activity_count == 1


def test_existing_review_comment_updated_in_period_is_retained_as_activity() -> None:
    client = FakeClient([], [candidate(17)])
    client.details["/repos/Project-HAMi/HAMi/pulls/17"] = pr_detail(17)
    client.lists["/repos/Project-HAMi/HAMi/pulls/17/comments"] = [
        {
            "user": {"login": "reviewer", "type": "User"},
            "author_association": "COLLABORATOR",
            "body": "updated suggestion",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-13T00:00:00Z",
        }
    ]
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    comment = result.pull_requests[0].review_comments[0]
    assert comment.active_in_period is True
    assert result.pull_requests[0].period_human_activity_count == 1


def test_candidates_are_processed_with_bounded_parallelism() -> None:
    class ConcurrentClient(FakeClient):
        def __init__(self) -> None:
            super().__init__([candidate(12), candidate(13)], [])
            self.details["/repos/Project-HAMi/HAMi/issues/12"] = issue_detail(12)
            self.details["/repos/Project-HAMi/HAMi/issues/13"] = issue_detail(13)
            self.active = 0
            self.max_active = 0
            self.lock = Lock()

        def get_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            sleep(0.03)
            try:
                return super().get_json(path, **kwargs)
            finally:
                with self.lock:
                    self.active -= 1

    client = ConcurrentClient()
    result = ActivityCollector(client, PERIOD, workers=2).collect("Project-HAMi")  # type: ignore[arg-type]
    assert client.max_active == 2
    assert [item.number for item in result.issues] == [12, 13]


def test_old_candidate_is_pruned_before_detail_request() -> None:
    old = candidate(14) | {"updated_at": "2026-07-09T15:59:59Z"}
    client = FakeClient([old], [])
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert result.issues == []
    assert client.failed_requests == 0


def test_issue_search_payload_avoids_redundant_detail_and_empty_comments_requests() -> None:
    complete = candidate(15) | issue_detail(15, comments=0)
    client = FakeClient([complete], [])
    result = ActivityCollector(client, PERIOD).collect("Project-HAMi")  # type: ignore[arg-type]
    assert [item.number for item in result.issues] == [15]
    assert client.failed_requests == 0
    assert client.list_calls == []
