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
    assert sleeps == [2.0]


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
        return httpx.Response(200, json={"total_count": 1000, "items": [{"number": 1}]})

    raw = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    result = GitHubClient("token", client=raw).search_issues("org:Project-HAMi is:issue")
    assert result.capped is True
    assert result.total_count == 1000
    assert len(result.items) == 1


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
