from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .github_client import GitHubClient, GitHubRequestError


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    node_id: str
    actor_node_id: str
    created_at: str


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _query(base: str, start: datetime, end: datetime) -> str:
    return f"{base} created:{_stamp(start)}..{_stamp(end)}"


def _leaf(client: GitHubClient, base: str, start: datetime, end: datetime, first_page=None) -> list[SearchCandidate]:
    page_number = 1
    rows: list[SearchCandidate] = []
    total: int | None = None
    seen: set[str] = set()
    while True:
        page = first_page if page_number == 1 and first_page is not None else client.search(_query(base, start, end), page=page_number)
        if page.incomplete_results:
            raise GitHubRequestError("search_incomplete_results")
        total = page.total_count if total is None else total
        if page.total_count != total:
            raise GitHubRequestError("search_cardinality_mismatch")
        expected = 0 if total == 0 else min(100, total - len(rows))
        if len(page.items) != expected:
            raise GitHubRequestError("search_cardinality_mismatch")
        for item in page.items:
            node_id = item.get("node_id")
            created_at = item.get("created_at")
            actor_node_id = item.get("actor_node_id")
            if not isinstance(node_id, str) or not isinstance(created_at, str):
                raise GitHubRequestError("api_contract_violation")
            if node_id in seen:
                raise GitHubRequestError("search_cardinality_mismatch")
            seen.add(node_id)
            rows.append(SearchCandidate(node_id, actor_node_id if isinstance(actor_node_id, str) else "", created_at))
        if len(rows) == total:
            return rows
        page_number += 1


def _collect_once(client: GitHubClient, base: str, start: datetime, end: datetime) -> list[SearchCandidate]:
    # GitHub Search's range endpoints are inclusive. Adjacent leaves share a second;
    # identity and timestamp must agree before the union is deduplicated.
    if end - start < timedelta(seconds=1):
        raise GitHubRequestError("search_capped")
    first = client.search(_query(base, start, end), page=1)
    if first.total_count < 1000 and not first.incomplete_results:
        # Reuse the first page through a tiny deterministic adapter.
        return _leaf(client, base, start, end, first)
    midpoint = start + (end - start) // 2
    if midpoint <= start or midpoint >= end:
        raise GitHubRequestError("search_capped")
    left = _collect_once(client, base, start, midpoint)
    right = _collect_once(client, base, midpoint, end)
    merged: dict[tuple[str, str], SearchCandidate] = {}
    for candidate in left + right:
        key = (candidate.node_id, candidate.created_at)
        previous = merged.get(key)
        if previous and previous != candidate:
            raise GitHubRequestError("search_candidate_conflict")
        merged[key] = candidate
    return list(merged.values())


def stable_search(client: GitHubClient, base: str, start: datetime, end: datetime) -> list[SearchCandidate]:
    snapshots: list[tuple[tuple[str, str, str], ...]] = []
    for _ in range(2):
        rows = _collect_once(client, base, start, end)
        snapshot = tuple(sorted((row.node_id, row.actor_node_id, row.created_at) for row in rows))
        snapshots.append(snapshot)
    if snapshots[0] != snapshots[1]:
        raise GitHubRequestError("search_snapshot_unstable")
    return [SearchCandidate(*row) for row in snapshots[0]]
