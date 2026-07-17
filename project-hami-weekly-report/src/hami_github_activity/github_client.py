from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: list[dict[str, Any]]
    total_count: int
    capped: bool
    incomplete: bool = False
    partial_error: str | None = None
    partial_error_url: str | None = None


class GitHubRequestError(RuntimeError):
    def __init__(self, message: str, *, url: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        max_attempts: int = 4,
        max_connections: int = 8,
        requests_per_second: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_connections < 1:
            raise ValueError("max_connections must be positive")
        if requests_per_second is not None and requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hami-github-activity/0.1",
        }
        self._client = client or httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
        )
        if client is not None:
            self._client.headers.update(headers)
        self._owns_client = client is None
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._request_interval = 1.0 / requests_per_second if requests_per_second else None
        self._pacing_lock = Lock()
        self._next_request_at = 0.0
        self._state_lock = Lock()
        self.failed_requests = 0
        self.rate_limit_remaining: int | None = None

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _wait_for_request_slot(self) -> None:
        with self._pacing_lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            if delay:
                self._sleep(delay)
            self._next_request_at = max(now, self._next_request_at) + (self._request_interval or 0.0)

    def _defer_all_requests(self, delay: float) -> None:
        with self._pacing_lock:
            self._next_request_at = max(self._next_request_at, time.monotonic() + delay)

    def _record_failure(self) -> None:
        with self._state_lock:
            self.failed_requests += 1

    def _record_rate_limit(self, value: str) -> None:
        try:
            remaining = int(value)
        except ValueError:
            return
        with self._state_lock:
            if self.rate_limit_remaining is None:
                self.rate_limit_remaining = remaining
            else:
                self.rate_limit_remaining = min(self.rate_limit_remaining, remaining)

    def _rate_limit_exhausted(self) -> bool:
        with self._state_lock:
            return self.rate_limit_remaining == 0

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_request_slot()
            try:
                logger.info("GitHub GET %s (attempt %d/%d)", path, attempt, self._max_attempts)
                response = self._client.get(path, params=params)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self._max_attempts:
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        "GitHub request failed for %s (attempt %d/%d); retrying in %s seconds: %s",
                        exc.request.url,
                        attempt,
                        self._max_attempts,
                        delay,
                        exc,
                    )
                    self._sleep(delay)
                    continue
                self._record_failure()
                raise GitHubRequestError(
                    f"network error after {attempt} attempts: {exc}", url=str(exc.request.url)
                ) from exc

            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining is not None:
                self._record_rate_limit(remaining)

            rate_limited = response.status_code == 403 and (
                response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
            )
            if response.status_code == 429 or rate_limited or 500 <= response.status_code < 600:
                if attempt < self._max_attempts:
                    retry_after = response.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt - 1)
                    logger.warning(
                        "GitHub API returned %d for %s (attempt %d/%d); retrying in %s seconds",
                        response.status_code,
                        response.request.url,
                        attempt,
                        self._max_attempts,
                        delay,
                    )
                    if response.status_code == 429 or rate_limited:
                        self._defer_all_requests(delay)
                    else:
                        self._sleep(delay)
                    continue

            if response.is_error:
                self._record_failure()
                try:
                    api_message = response.json().get("message")
                except (ValueError, AttributeError):
                    api_message = response.text[:500]
                detail = api_message or response.reason_phrase
                if response.status_code == 403 and self._rate_limit_exhausted():
                    detail = f"GitHub API rate limit exhausted: {detail}"
                raise GitHubRequestError(
                    f"GitHub API returned {response.status_code}: {detail}",
                    url=str(response.request.url),
                    status_code=response.status_code,
                )
            return response

        self._record_failure()
        raise GitHubRequestError(f"request failed: {last_error}", url=path)

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request(path, params=params)
        try:
            data = response.json()
        except ValueError as exc:
            self._record_failure()
            raise GitHubRequestError("response was not valid JSON", url=str(response.request.url)) from exc
        if not isinstance(data, dict):
            self._record_failure()
            raise GitHubRequestError("expected a JSON object", url=str(response.request.url))
        return data

    def get_paginated(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1
        while True:
            page_params = dict(params or {})
            page_params.update({"per_page": 100, "page": page})
            response = self._request(path, params=page_params)
            try:
                data = response.json()
            except ValueError as exc:
                self._record_failure()
                raise GitHubRequestError("response was not valid JSON", url=str(response.request.url)) from exc
            if not isinstance(data, list):
                self._record_failure()
                raise GitHubRequestError("expected a paginated JSON array", url=str(response.request.url))
            collected.extend(item for item in data if isinstance(item, dict))
            logger.info(
                "Fetched page %d from %s: %d records (%d total)",
                page,
                path,
                len(data),
                len(collected),
            )
            if len(data) < 100 or 'rel="next"' not in response.headers.get("link", ""):
                break
            page += 1
        return collected

    def search_issues(self, query: str) -> SearchResult:
        collected: list[dict[str, Any]] = []
        total_count = 0
        incomplete = False
        page = 1
        while True:
            try:
                response = self._request(
                    "/search/issues",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": 100, "page": page},
                )
            except GitHubRequestError as exc:
                if not collected:
                    raise
                return SearchResult(
                    items=collected[:1000],
                    total_count=total_count,
                    capped=total_count >= 1000,
                    incomplete=True,
                    partial_error=str(exc),
                    partial_error_url=exc.url,
                )
            try:
                data = response.json()
            except ValueError as exc:
                self._record_failure()
                raise GitHubRequestError("response was not valid JSON", url=str(response.request.url)) from exc
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                self._record_failure()
                raise GitHubRequestError("invalid Search Issues response", url=str(response.request.url))
            total_count = int(data.get("total_count") or 0)
            incomplete = incomplete or bool(data.get("incomplete_results"))
            batch = [item for item in data["items"] if isinstance(item, dict)]
            collected.extend(batch)
            logger.info(
                "Fetched GitHub Search page %d: %d records (%d/%d collected)",
                page,
                len(batch),
                len(collected),
                min(total_count, 1000),
            )
            if not batch or len(batch) < 100 or len(collected) >= min(total_count, 1000):
                break
            page += 1
        return SearchResult(
            items=collected[:1000],
            total_count=total_count,
            capped=total_count >= 1000,
            incomplete=incomplete,
        )
