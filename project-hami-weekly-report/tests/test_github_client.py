import logging
from collections import Counter

import httpx
import pytest

from hami_github_activity.github_client import GitHubClient, GitHubRequestError


def test_pagination_follows_link_header() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        if page == 1:
            return httpx.Response(
                200,
                json=[{"id": index} for index in range(100)],
                headers={"Link": '<https://api.github.com/items?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[{"id": 100}])

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, sleep=lambda _: None)
    assert len(client.get_paginated("/items")) == 101
    assert calls == [1, 2]


def test_pagination_follows_a_next_link_even_when_the_page_is_short() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        if page == 1:
            return httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"Link": '<https://api.github.com/items?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[{"id": 2}])

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).get_paginated_result("/items")

    assert [item["id"] for item in result.items] == [1, 2]
    assert calls == [1, 2]


def test_pagination_probes_after_a_full_page_without_a_next_link() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        if page == 1:
            return httpx.Response(200, json=[{"id": number} for number in range(100)])
        return httpx.Response(200, json=[{"id": 100}])

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).get_paginated_result("/items")

    assert len(result.items) == 101
    assert calls == [1, 2]


def test_pagination_has_a_safety_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        return httpx.Response(
            200,
            json=[{"id": page}],
            headers={"Link": '<https://api.github.com/items?page=next>; rel="next"'},
        )

    monkeypatch.setattr("hami_github_activity.github_client.MAX_PAGINATED_PAGES", 2)
    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).get_paginated_result("/items")

    assert result.incomplete is True
    assert result.failed_page == 3
    assert calls == [1, 2]


def test_paginated_result_preserves_successful_pages_after_later_failure() -> None:
    calls = Counter()

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json=[{"id": index} for index in range(100)],
                headers={"Link": '<https://api.github.com/items?page=2>; rel="next"'},
            )
        return httpx.Response(500, json={"message": "unavailable"})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, max_attempts=1)
    result = client.get_paginated_result("/items")

    assert len(result.items) == 100
    assert result.incomplete is True
    assert result.failed_page == 2
    assert "500" in (result.partial_error or "")


def test_paginated_result_counts_non_object_entries() -> None:
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{"id": 1}, None])),
    )

    result = GitHubClient("token", client=raw).get_paginated_result("/items")

    assert result.items == [{"id": 1}]
    assert result.malformed_item_count == 1


def test_retries_5xx_and_captures_rate_limit(caplog: pytest.LogCaptureFixture) -> None:
    calls = Counter()

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"ok": True}, headers={"X-RateLimit-Remaining": "42"})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, sleep=lambda _: None)
    caplog.set_level(logging.WARNING)
    assert client.get_json("/status") == {"ok": True}
    assert calls["count"] == 3
    assert client.rate_limit_remaining == 42
    assert "retrying in 1 seconds" in caplog.text
    assert "retrying in 2 seconds" in caplog.text


def test_requests_use_an_explicit_utc_timezone() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Time-Zone"] == "UTC"
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    assert GitHubClient("token", client=raw).get_json("/status") == {"ok": True}


def test_retries_network_timeout() -> None:
    calls = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, max_attempts=2, sleep=lambda _: None)
    assert client.get_json("/status") == {"ok": True}
    assert calls["count"] == 2


def test_shared_request_start_rate_is_paced(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )
    monkeypatch.setattr("hami_github_activity.github_client.time.monotonic", lambda: 100.0)
    client = GitHubClient(
        "token",
        client=raw,
        requests_per_second=10,
        sleep=sleeps.append,
    )
    client.get_json("/first")
    client.get_json("/second")
    assert sleeps == [pytest.approx(0.1)]


def test_retries_429_then_reports_failure() -> None:
    calls = Counter()

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "0"})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, max_attempts=2, sleep=lambda _: None)
    with pytest.raises(GitHubRequestError, match="429"):
        client.get_json("/status")
    assert calls["count"] == 2
    assert client.failed_requests == 1


def test_rate_limit_retry_defers_shared_request_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Counter()
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("hami_github_activity.github_client.time.monotonic", lambda: 100.0)
    client = GitHubClient("token", client=raw, max_attempts=2, sleep=sleeps.append)
    assert client.get_json("/status") == {"ok": True}
    assert sleeps == [pytest.approx(2.0)]


def test_retries_rate_limited_403() -> None:
    calls = Counter()

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                403,
                json={"message": "secondary rate limit"},
                headers={"Retry-After": "0", "X-RateLimit-Remaining": "1"},
            )
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, max_attempts=2, sleep=lambda _: None)
    assert client.get_json("/status") == {"ok": True}
    assert calls["count"] == 2


def test_search_detects_one_thousand_result_cap() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"total_count": 1000, "incomplete_results": False, "items": [{"number": 1}]},
        )

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")
    assert result.capped is True
    assert result.total_count == 1000
    assert len(result.items) == 1


def test_search_counts_non_object_items_instead_of_silently_dropping_them() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 1, "incomplete_results": False, "items": [42]})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")

    assert result.items == []
    assert result.malformed_item_count == 1


@pytest.mark.parametrize(
    ("total_count", "items"),
    [
        (2, [{"number": 1}]),
        (1, []),
    ],
)
def test_search_marks_announced_but_unreturned_candidates_incomplete(
    total_count: int, items: list[dict[str, int]]
) -> None:
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"total_count": total_count, "incomplete_results": False, "items": items},
            )
        ),
    )

    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")

    assert result.incomplete is True
    assert len(result.items) < total_count


@pytest.mark.parametrize(
    "payload",
    [
        {"incomplete_results": False, "items": []},
        {"total_count": -1, "incomplete_results": False, "items": []},
        {"total_count": True, "incomplete_results": False, "items": []},
        {"total_count": 0, "incomplete_results": "false", "items": []},
    ],
)
def test_search_rejects_an_invalid_result_envelope(payload: dict[str, object]) -> None:
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )

    with pytest.raises(GitHubRequestError, match="invalid Search Issues response"):
        GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")


def test_search_rejects_a_nonempty_result_for_a_zero_total() -> None:
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "total_count": 0,
                    "incomplete_results": False,
                    "items": [
                        {
                            "repository_url": "https://api.github.com/repos/Project-HAMi/HAMi",
                            "number": 1,
                        }
                    ],
                },
            )
        ),
    )

    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")

    assert result.incomplete is True
    assert result.unique_item_count == 1


def test_search_counts_duplicate_or_invalid_candidate_identities() -> None:
    valid = {"repository_url": "https://api.github.com/repos/Project-HAMi/HAMi", "number": 1}
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "total_count": 3,
                    "incomplete_results": False,
                    "items": [valid, valid, {"repository_url": "not-a-repository", "number": 2}],
                },
            )
        ),
    )

    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")

    assert result.incomplete is True
    assert result.duplicate_item_count == 1
    assert result.malformed_identity_count == 1


def test_search_allows_a_duplicate_when_unique_candidates_match_a_stable_total() -> None:
    valid = {"repository_url": "https://api.github.com/repos/Project-HAMi/HAMi", "number": 1}
    raw = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"total_count": 1, "incomplete_results": False, "items": [valid, valid]},
            )
        ),
    )

    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")

    assert result.incomplete is False
    assert result.duplicate_item_count == 1
    assert result.unique_item_count == 1


def test_search_rejects_a_duplicate_page_boundary_when_the_declared_total_changes() -> None:
    def item(number: int) -> dict[str, object]:
        return {
            "repository_url": "https://api.github.com/repos/Project-HAMi/HAMi",
            "number": number,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "total_count": 177,
                    "incomplete_results": False,
                    "items": [item(number) for number in range(1, 101)],
                },
            )
        return httpx.Response(
            200,
            json={
                "total_count": 176,
                "incomplete_results": False,
                "items": [item(100), *[item(number) for number in range(101, 177)]],
            },
        )

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:pr")

    assert result.total_count == 177
    assert result.incomplete is True
    assert result.duplicate_item_count == 1
    assert result.unique_item_count == 176


def test_search_preserves_first_page_when_later_page_fails() -> None:
    calls = Counter()

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json={"total_count": 101, "incomplete_results": False, "items": [{"number": n} for n in range(100)]},
            )
        return httpx.Response(500, json={"message": "unavailable"})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw, max_attempts=1, sleep=lambda _: None)
    result = client.search_issues("org:Project-HAMi is:issue")
    assert len(result.items) == 100
    assert result.incomplete is True
    assert "500" in (result.partial_error or "")
    assert client.failed_requests == 1


def test_rate_limits_are_tracked_per_resource() -> None:
    calls = Counter()

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json={"ok": True},
                headers={
                    "X-RateLimit-Remaining": "3",
                    "X-RateLimit-Resource": "search",
                    "X-RateLimit-Reset": "123",
                },
            )
        return httpx.Response(
            200,
            json={"ok": True},
            headers={
                "X-RateLimit-Remaining": "42",
                "X-RateLimit-Resource": "core",
                "X-RateLimit-Reset": "456",
            },
        )

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    client = GitHubClient("token", client=raw)
    client.get_json("/search")
    client.get_json("/core")

    assert client.rate_limits == {"search": (3, 123), "core": (42, 456)}
    assert client.rate_limit_remaining == 3


def test_primary_rate_limit_waits_until_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Counter()
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                403,
                json={"message": "rate limit exceeded"},
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Resource": "core",
                    "X-RateLimit-Reset": "105",
                },
            )
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("hami_github_activity.github_client.time.time", lambda: 100.0)
    client = GitHubClient("token", client=raw, max_attempts=2, sleep=sleeps.append)
    assert client.get_json("/status") == {"ok": True}
    assert sleeps == [pytest.approx(6.0)]
