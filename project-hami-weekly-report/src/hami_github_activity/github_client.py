from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx


logger = logging.getLogger(__name__)
MAX_PAGINATED_PAGES = 100


def parse_repository_url(repository_url: object) -> str | None:
    if not isinstance(repository_url, str):
        return None
    marker = "/repos/"
    if marker not in repository_url:
        return None
    repository = repository_url.split(marker, 1)[1].rstrip("/")
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name or "/" in name or "?" in name or "#" in name:
        return None
    return f"{owner}/{name}"


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: list[dict[str, Any]]
    total_count: int
    capped: bool
    incomplete: bool = False
    partial_error: str | None = None
    partial_error_url: str | None = None
    malformed_item_count: int = 0
    malformed_identity_count: int = 0
    duplicate_item_count: int = 0
    unique_item_count: int = 0


@dataclass(frozen=True, slots=True)
class PaginatedResult:
    items: list[dict[str, Any]]
    incomplete: bool = False
    failed_page: int | None = None
    partial_error: str | None = None
    partial_error_url: str | None = None
    malformed_item_count: int = 0


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
            "Time-Zone": "UTC",
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
        self.rate_limits: dict[str, tuple[int, int | None]] = {}

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

    def _record_rate_limit(self, headers: httpx.Headers) -> None:
        remaining_value = headers.get("x-ratelimit-remaining")
        if remaining_value is None:
            return
        try:
            remaining = int(remaining_value)
        except ValueError:
            return
        resource = headers.get("x-ratelimit-resource", "core")
        reset_value = headers.get("x-ratelimit-reset")
        try:
            reset = int(reset_value) if reset_value is not None else None
        except ValueError:
            reset = None
        with self._state_lock:
            self.rate_limits[resource] = (remaining, reset)
            # Keep the historical scalar for evidence consumers, but never use it
            # for retry decisions: Search and core are independent quotas.
            self.rate_limit_remaining = min(limit[0] for limit in self.rate_limits.values())

    def _rate_limit_exhausted(self, resource: str | None = None) -> bool:
        with self._state_lock:
            if resource is not None and resource in self.rate_limits:
                return self.rate_limits[resource][0] == 0
            return any(remaining == 0 for remaining, _ in self.rate_limits.values())

    @staticmethod
    def _rate_limit_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = response.headers.get("x-ratelimit-reset")
            try:
                return max(1.0, float(int(reset) - time.time() + 1))
            except (TypeError, ValueError):
                return 60.0
        # GitHub recommends a one-minute wait before exponential backoff for a
        # secondary limit without Retry-After.
        return 60.0 * (2 ** (attempt - 1))

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

            self._record_rate_limit(response.headers)

            rate_limited = response.status_code == 403 and (
                response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
            )
            if response.status_code == 429 or rate_limited or 500 <= response.status_code < 600:
                if attempt < self._max_attempts:
                    delay = (
                        self._rate_limit_delay(response, attempt)
                        if response.status_code == 429 or rate_limited
                        else 2 ** (attempt - 1)
                    )
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
                if response.status_code == 403 and self._rate_limit_exhausted(
                    response.headers.get("x-ratelimit-resource")
                ):
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

    def get_paginated_result(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> PaginatedResult:
        collected: list[dict[str, Any]] = []
        malformed_item_count = 0
        page = 1
        while True:
            page_params = dict(params or {})
            page_params.update({"per_page": 100, "page": page})
            try:
                response = self._request(path, params=page_params)
            except GitHubRequestError as exc:
                if not collected:
                    raise
                return PaginatedResult(
                    items=collected,
                    incomplete=True,
                    failed_page=page,
                    partial_error=str(exc),
                    partial_error_url=exc.url,
                    malformed_item_count=malformed_item_count,
                )
            try:
                data = response.json()
            except ValueError as exc:
                self._record_failure()
                error = GitHubRequestError("response was not valid JSON", url=str(response.request.url))
                if collected:
                    return PaginatedResult(
                        items=collected,
                        incomplete=True,
                        failed_page=page,
                        partial_error=str(error),
                        partial_error_url=error.url,
                        malformed_item_count=malformed_item_count,
                    )
                raise error from exc
            if not isinstance(data, list):
                self._record_failure()
                error = GitHubRequestError("expected a paginated JSON array", url=str(response.request.url))
                if collected:
                    return PaginatedResult(
                        items=collected,
                        incomplete=True,
                        failed_page=page,
                        partial_error=str(error),
                        partial_error_url=error.url,
                        malformed_item_count=malformed_item_count,
                    )
                raise error
            malformed_item_count += sum(not isinstance(item, dict) for item in data)
            collected.extend(item for item in data if isinstance(item, dict))
            logger.info(
                "Fetched page %d from %s: %d records (%d total)",
                page,
                path,
                len(data),
                len(collected),
            )
            has_next = 'rel="next"' in response.headers.get("link", "")
            # A full page without a Link header is ambiguous: an intermediary
            # may have stripped the header. Probe one more page; only a short
            # (including empty) page confirms the end of the collection.
            if not has_next and len(data) < 100:
                break
            if page >= MAX_PAGINATED_PAGES:
                return PaginatedResult(
                    items=collected,
                    incomplete=True,
                    failed_page=page + 1,
                    partial_error=f"pagination exceeded the {MAX_PAGINATED_PAGES}-page safety limit",
                    partial_error_url=str(response.request.url),
                    malformed_item_count=malformed_item_count,
                )
            page += 1
        return PaginatedResult(items=collected, malformed_item_count=malformed_item_count)

    def get_paginated(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Backward-compatible list-only pagination API.

        Collector code uses :meth:`get_paginated_result` to retain partial pages
        and mark evidence incomplete.  Keeping this helper avoids breaking other
        consumers that only need a list.
        """
        return self.get_paginated_result(path, params=params).items

    def list_org_repositories(self, org: str) -> PaginatedResult:
        """Discover the repositories visible to this token for the target org."""
        return self.get_paginated_result(
            f"/orgs/{org}/repos",
            params={"type": "all", "sort": "full_name", "direction": "asc"},
        )

    def search_issues(self, query: str) -> SearchResult:
        collected: list[dict[str, Any]] = []
        total_count = 0
        stable_total_count: int | None = None
        reached_cap = False
        incomplete = False
        malformed_item_count = 0
        malformed_identity_count = 0
        duplicate_item_count = 0
        seen_identities: set[tuple[str, int]] = set()
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
                    capped=reached_cap,
                    incomplete=True,
                    partial_error=str(exc),
                    partial_error_url=exc.url,
                    malformed_item_count=malformed_item_count,
                    malformed_identity_count=malformed_identity_count,
                    duplicate_item_count=duplicate_item_count,
                    unique_item_count=len(seen_identities),
                )
            try:
                data = response.json()
            except ValueError as exc:
                self._record_failure()
                raise GitHubRequestError("response was not valid JSON", url=str(response.request.url)) from exc
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                self._record_failure()
                raise GitHubRequestError("invalid Search Issues response", url=str(response.request.url))
            response_total_count = data.get("total_count")
            response_incomplete = data.get("incomplete_results")
            if (
                not isinstance(response_total_count, int)
                or isinstance(response_total_count, bool)
                or response_total_count < 0
                or not isinstance(response_incomplete, bool)
            ):
                self._record_failure()
                raise GitHubRequestError("invalid Search Issues response", url=str(response.request.url))
            if stable_total_count is None:
                stable_total_count = response_total_count
            elif response_total_count != stable_total_count:
                # Search has no server-side snapshot cursor. A changing
                # declaration cannot certify one complete candidate set.
                incomplete = True
            total_count = stable_total_count
            reached_cap = reached_cap or response_total_count >= 1000
            incomplete = incomplete or response_incomplete
            raw_items = data["items"]
            malformed_item_count += sum(not isinstance(item, dict) for item in raw_items)
            batch = [item for item in raw_items if isinstance(item, dict)]
            for item in batch:
                identity = self._search_item_identity(item)
                if identity is None:
                    malformed_identity_count += 1
                elif identity in seen_identities:
                    duplicate_item_count += 1
                else:
                    seen_identities.add(identity)
            collected.extend(batch)
            logger.info(
                "Fetched GitHub Search page %d: %d records (%d/%d collected)",
                page,
                len(batch),
                len(collected),
                min(total_count, 1000),
            )
            expected_count = min(total_count, 1000)
            # GitHub Search does not provide a Link header.  Continue through a
            # short-lived duplicate boundary until a short page is returned;
            # do not stop solely because raw rows happen to reach total_count.
            if not batch or len(batch) < 100 or page >= 10:
                if len(seen_identities) != expected_count:
                    # Search's announced total and the returned pages disagree.
                    # Do not turn a truncated candidate set into a complete
                    # evidence collection merely because the page is short.
                    incomplete = True
                break
            page += 1
        return SearchResult(
            items=collected[:1000],
            total_count=total_count,
            capped=reached_cap,
            incomplete=incomplete,
            malformed_item_count=malformed_item_count,
            malformed_identity_count=malformed_identity_count,
            duplicate_item_count=duplicate_item_count,
            unique_item_count=len(seen_identities),
        )

    @staticmethod
    def _search_item_identity(item: dict[str, Any]) -> tuple[str, int] | None:
        repository_url = item.get("repository_url")
        number = item.get("number")
        repository = parse_repository_url(repository_url)
        if repository is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
            return None
        return repository, number
