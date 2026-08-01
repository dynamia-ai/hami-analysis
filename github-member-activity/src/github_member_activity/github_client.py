from __future__ import annotations

import time
import json
from dataclasses import dataclass
from typing import Any, Callable

import httpx


class GitHubRequestError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SearchPage:
    items: tuple[dict[str, Any], ...]
    total_count: int
    incomplete_results: bool


class GitHubClient:
    """Small transport with bounded retries and strict GraphQL pagination helpers."""

    def __init__(self, token: str, *, api_version: str = "2026-03-10", client: httpx.Client | None = None,
                 max_attempts: int = 4, sleep: Callable[[float], None] = time.sleep) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url="https://api.github.com", timeout=30)
        self._client.headers.update({"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                                     "X-GitHub-Api-Version": api_version, "User-Agent": "github-member-activity/1.0"})
        self.max_attempts = max_attempts
        self.sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                last = exc
                if attempt + 1 < self.max_attempts:
                    self.sleep(2**attempt)
                    continue
                raise GitHubRequestError("transport_retry_exhausted") from exc
            retryable = response.status_code == 429 or response.status_code >= 500 or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0")
            if retryable and attempt + 1 < self.max_attempts:
                retry_after = response.headers.get("retry-after")
                self.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt)
                continue
            if response.is_error:
                raise GitHubRequestError("rate_limited" if retryable else "authentication_failed" if response.status_code in {401, 403} else "api_contract_violation", status_code=response.status_code)
            return response
        raise GitHubRequestError("transport_retry_exhausted") from last

    def search(self, query: str, *, page: int = 1) -> SearchPage:
        response = self._request("GET", "/search/issues", params={"q": query, "sort": "created", "order": "asc", "per_page": 100, "page": page})
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not isinstance(data.get("total_count"), int) or isinstance(data.get("total_count"), bool) or data["total_count"] < 0:
            raise GitHubRequestError("api_contract_violation")
        safe = []
        if not isinstance(data.get("incomplete_results"), bool):
            raise GitHubRequestError("api_contract_violation")
        for item in data["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int) or not isinstance(item.get("node_id"), str) or not isinstance(item.get("created_at"), str) or not isinstance((item.get("user") or {}).get("node_id"), str):
                raise GitHubRequestError("api_contract_violation")
            safe.append({"id": item["id"], "node_id": item["node_id"], "actor_node_id": item["user"]["node_id"], "created_at": item["created_at"]})
        return SearchPage(tuple(safe), data["total_count"], data["incomplete_results"])

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", "/graphql", json={"query": query, "variables": variables})
        data = response.json()
        if not isinstance(data, dict) or data.get("errors"):
            raise GitHubRequestError("graphql_partial_response")
        if not isinstance(data.get("data"), dict):
            raise GitHubRequestError("api_contract_violation")
        return data["data"]

    def connection(self, query: str, variables: dict[str, Any], path: tuple[str, ...]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_nodes: set[str] = set()
        expected_total: int | None = None
        while True:
            current = dict(variables)
            current["after"] = cursor
            data: Any = self.graphql(query, current)
            for part in path:
                data = data.get(part) if isinstance(data, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get("edges"), list) or not isinstance(data.get("pageInfo"), dict):
                raise GitHubRequestError("api_contract_violation")
            if not isinstance(data.get("totalCount"), int) or isinstance(data.get("totalCount"), bool) or data["totalCount"] < 0:
                raise GitHubRequestError("api_contract_violation")
            expected_total = data["totalCount"] if expected_total is None else expected_total
            if expected_total != data["totalCount"]:
                raise GitHubRequestError("graphql_cardinality_mismatch")
            edges = data["edges"]
            for edge in edges:
                if not isinstance(edge, dict) or not isinstance(edge.get("cursor"), str) or not isinstance(edge.get("node"), dict):
                    raise GitHubRequestError("api_contract_violation")
                edge_cursor = edge["cursor"]
                if edge_cursor in seen_cursors:
                    raise GitHubRequestError("graphql_cardinality_mismatch")
                seen_cursors.add(edge_cursor)
                node_id = edge["node"].get("id")
                stable_node = str(node_id) if isinstance(node_id, str) else json.dumps(edge["node"], sort_keys=True, separators=(",", ":"))
                if stable_node in seen_nodes:
                    raise GitHubRequestError("graphql_cardinality_mismatch")
                seen_nodes.add(stable_node)
                result.append(edge["node"])
            info = data["pageInfo"]
            has_next = info.get("hasNextPage")
            next_cursor = info.get("endCursor")
            if not isinstance(has_next, bool):
                raise GitHubRequestError("api_contract_violation")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise GitHubRequestError("api_contract_violation")
            if not has_next:
                if expected_total is not None and len(result) != expected_total:
                    raise GitHubRequestError("graphql_cardinality_mismatch")
                return result
            if not isinstance(next_cursor, str) or not next_cursor or (cursor is not None and next_cursor == cursor):
                raise GitHubRequestError("cursor_invalid")
            cursor = next_cursor
