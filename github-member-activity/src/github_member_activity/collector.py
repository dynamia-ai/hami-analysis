from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
import re
from typing import Any

from .config import AppConfig
from .github_client import GitHubClient
from .models import LedgerEvent, SourceStatus
from .period import ReportPeriod, basic_utc, effective_window, format_z, parse_rfc3339
from .search import stable_search
from .repository_policy import RepositoryMetadata, public_and_allowed


_FAILED_REASONS = frozenset({
    "identity_resolution_failed", "identity_node_mismatch", "identity_type_mismatch", "identity_login_mismatch",
    "authentication_failed", "search_candidate_conflict", "graphql_cardinality_mismatch", "cursor_invalid",
    "api_contract_violation", "visibility_unverified", "repository_binding_changed",
})
_IDENTITY_REASONS = frozenset({"identity_resolution_failed", "identity_node_mismatch", "identity_type_mismatch", "identity_login_mismatch", "authentication_failed"})
_PARTIAL_REASONS = frozenset({
    "search_capped", "search_incomplete_results", "search_cardinality_mismatch", "search_snapshot_unstable",
    "graphql_partial_response", "graphql_snapshot_unstable", "pagination_incomplete", "rate_limited",
    "transport_retry_exhausted", "commit_context_unavailable",
})
_NOT_APPLICABLE_REASONS = frozenset({"member_window_empty", "commit_period_not_day_aligned"})
_NOT_RUN_REASONS = frozenset({"stability_gap_not_met", "run_aborted"})


def _canonical_status(status: str, reason: str | None) -> str:
    if reason in _FAILED_REASONS:
        return "failed"
    if reason in _PARTIAL_REASONS:
        return "partial"
    if reason in _NOT_APPLICABLE_REASONS:
        return "not_applicable"
    if reason in _NOT_RUN_REASONS:
        return "not_run"
    return status


def _record_final_gate_failure(statuses: list[SourceStatus], member_id: str, source: str, reason: str) -> None:
    status = _canonical_status("failed", reason)
    for index, row in enumerate(statuses):
        if row.member_id != member_id or row.source != source:
            continue
        if row.status == "complete":
            statuses[index] = SourceStatus(
                row.member_id, row.source, row.criticality, status, reason,
                row.pagination_complete, row.partition_complete, row.snapshot_complete,
                False, row.snapshot_completed_at,
            )
        else:
            statuses[index] = SourceStatus(row.member_id, row.source, row.criticality, status, reason)
        return


def _record_optional_commit_gate_failure(statuses: list[SourceStatus], member_id: str) -> None:
    for index, row in enumerate(statuses):
        if row.member_id == member_id and row.source == "commit_context" and row.status == "complete":
            statuses[index] = SourceStatus(row.member_id, row.source, row.criticality, "partial", "commit_context_unavailable")
            return


def _record_final_gate_auth_failure(statuses: list[SourceStatus]) -> None:
    applicable_members = {row.member_id for row in statuses if row.status != "not_applicable"}
    for index, row in enumerate(statuses):
        if row.member_id in applicable_members:
            statuses[index] = SourceStatus(row.member_id, row.source, row.criticality, "failed", "authentication_failed")


def _exception_reason(exc: Exception, fallback: str) -> str:
    reason = getattr(exc, "reason", None)
    if not isinstance(reason, str) and getattr(exc, "args", None):
        reason = exc.args[0]
    return reason if isinstance(reason, str) and reason else fallback


def _gate_repository(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    return node if node.get("__typename") == "Repository" else node.get("repository") if isinstance(node.get("repository"), dict) else None


@dataclass(slots=True)
class CollectionResult:
    events: list[LedgerEvent]
    statuses: list[SourceStatus]
    applied_owner_ids: list[str]
    applied_repo_ids: list[str]
    publish_visibility_verified_at: str | None = None


def _ordered_node_map(nodes: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(nodes, list) or len(nodes) != len(ids):
        raise RuntimeError("api_contract_violation")
    result: dict[str, dict[str, Any]] = {}
    for expected_id, node in zip(ids, nodes, strict=True):
        if not isinstance(node, dict) or node.get("id") != expected_id or expected_id in result:
            raise RuntimeError("api_contract_violation")
        result[expected_id] = node
    return result


def _commit_group_pages(client: GitHubClient, login: str, start_day, end_day) -> tuple[list[dict[str, Any]], bool]:
    variables = {"login": login, "from": f"{start_day.isoformat()}T00:00:00Z", "to": f"{end_day.isoformat()}T23:59:59Z"}

    def read_groups(response: Any) -> list[dict[str, Any]]:
        groups = response.get("user", {}).get("contributionsCollection", {}).get("commitContributionsByRepository") if isinstance(response, dict) else None
        if not isinstance(groups, list) or len(groups) > 100:
            raise RuntimeError("commit_context_unavailable")
        page_repo_ids: set[str] = set()
        for group in groups:
            repo = group.get("repository") if isinstance(group, dict) else None
            connection = group.get("contributions") if isinstance(group, dict) else None
            if not isinstance(repo, dict) or not isinstance(repo.get("id"), str) or not repo["id"] or repo["id"] in page_repo_ids or not isinstance(connection, dict) or not isinstance(connection.get("totalCount"), int) or isinstance(connection.get("totalCount"), bool) or connection["totalCount"] < 0 or not isinstance(connection.get("edges"), list) or not isinstance(connection.get("pageInfo"), dict):
                raise RuntimeError("api_contract_violation")
            if connection["totalCount"] == 0 and not connection["edges"]:
                raise RuntimeError("commit_context_unavailable")
            page_repo_ids.add(repo["id"])
            page_info = connection["pageInfo"]
            if not isinstance(page_info.get("hasNextPage"), bool):
                raise RuntimeError("api_contract_violation")
            end_cursor = page_info.get("endCursor")
            if end_cursor is not None and not isinstance(end_cursor, str):
                raise RuntimeError("api_contract_violation")
            if page_info["hasNextPage"] and not end_cursor:
                raise RuntimeError("cursor_invalid")
        return groups

    def merge_page(groups_by_repo: dict[str, dict[str, Any]], group: dict[str, Any]) -> None:
        repo = group["repository"]
        connection = group["contributions"]
        repo_id = repo["id"]
        previous = groups_by_repo.get(repo_id)
        if previous is None:
            groups_by_repo[repo_id] = {"repository": repo, "contributions": {"totalCount": connection["totalCount"], "edges": list(connection["edges"]), "pageInfo": dict(connection["pageInfo"])}}
            return
        if previous["repository"] != repo or previous["contributions"]["totalCount"] != connection["totalCount"]:
            raise RuntimeError("api_contract_violation")
        previous["contributions"]["edges"].extend(connection["edges"])
        previous["contributions"]["pageInfo"] = dict(connection["pageInfo"])

    initial_groups = read_groups(client.graphql(COMMIT_GROUPS_QUERY, {**variables, "after": None}))
    outer_capped = len(initial_groups) == 100
    groups_by_repo: dict[str, dict[str, Any]] = {}
    pending: dict[str, str] = {}
    seen_cursors: dict[str, set[str]] = {}
    for group in initial_groups:
        merge_page(groups_by_repo, group)
        repo_id = group["repository"]["id"]
        page_info = group["contributions"]["pageInfo"]
        if page_info["hasNextPage"]:
            pending[repo_id] = page_info["endCursor"]
            seen_cursors[repo_id] = {page_info["endCursor"]}
    while pending:
        repo_id, cursor = pending.popitem()
        groups = read_groups(client.graphql(COMMIT_GROUPS_QUERY, {**variables, "after": cursor}))
        matching = [group for group in groups if group["repository"]["id"] == repo_id]
        if len(matching) != 1:
            raise RuntimeError("commit_context_unavailable")
        group = matching[0]
        merge_page(groups_by_repo, group)
        page_info = group["contributions"]["pageInfo"]
        if page_info["hasNextPage"]:
            next_cursor = page_info["endCursor"]
            if next_cursor in seen_cursors[repo_id]:
                raise RuntimeError("commit_context_unavailable")
            seen_cursors[repo_id].add(next_cursor)
            pending[repo_id] = next_cursor
    return list(groups_by_repo.values()), outer_capped


def _commit_snapshot(client: GitHubClient, login: str, member_node_id: str, start: datetime, end: datetime, policy) -> list[dict[str, Any]]:
    start_day = start.date()
    end_day = (end - timedelta(seconds=1)).date()
    if end_day < start_day:
        return []
    groups, outer_capped = _commit_group_pages(client, login, start_day, end_day)
    repo_ids: set[str] = set()
    needs_partition = outer_capped
    for group in groups:
        repo = group.get("repository") if isinstance(group, dict) else None
        connection = group.get("contributions") if isinstance(group, dict) else None
        if not isinstance(repo, dict) or not isinstance(repo.get("id"), str) or not repo["id"] or repo["id"] in repo_ids:
            raise RuntimeError("api_contract_violation")
        repo_ids.add(repo["id"])
        if not isinstance(connection, dict) or not isinstance(connection.get("pageInfo"), dict) or not isinstance(connection.get("edges"), list) or not isinstance(connection.get("totalCount"), int) or isinstance(connection.get("totalCount"), bool) or connection["totalCount"] < 0:
            raise RuntimeError("api_contract_violation")
        page_info = connection["pageInfo"]
        if not isinstance(page_info.get("hasNextPage"), bool):
            raise RuntimeError("api_contract_violation")
        if page_info.get("endCursor") is not None and not isinstance(page_info.get("endCursor"), str):
            raise RuntimeError("api_contract_violation")
        if page_info["hasNextPage"] and not page_info.get("endCursor"):
            raise RuntimeError("cursor_invalid")
        if page_info["hasNextPage"]:
            raise RuntimeError("commit_context_unavailable")
    if needs_partition:
        day_count = (end_day - start_day).days + 1
        if day_count <= 1:
            raise RuntimeError("commit_context_unavailable")
        midpoint_day = start_day + timedelta(days=day_count // 2)
        left_start = datetime.combine(start_day, datetime.min.time(), tzinfo=start.tzinfo)
        left_end = datetime.combine(midpoint_day, datetime.min.time(), tzinfo=start.tzinfo)
        right_start = left_end
        right_end = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=end.tzinfo)
        left = _commit_snapshot(client, login, member_node_id, left_start, left_end, policy)
        right = _commit_snapshot(client, login, member_node_id, right_start, right_end, policy)
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for row in left + right:
            key = (row["repo"].node_id, row["day"])
            if key in merged and merged[key]["quantity"] != row["quantity"]:
                raise RuntimeError("commit_context_unavailable")
            merged[key] = row
        return list(merged.values())
    result: list[dict[str, Any]] = []
    for group in groups:
        repo = group.get("repository") if isinstance(group, dict) else None
        connection = group.get("contributions") if isinstance(group, dict) else None
        if not isinstance(repo, dict) or not isinstance(connection, dict) or not isinstance(connection.get("totalCount"), int) or not isinstance(connection.get("edges"), list) or not isinstance(connection.get("pageInfo"), dict):
            raise RuntimeError("api_contract_violation")
        if connection["pageInfo"].get("hasNextPage") is not False:
            raise RuntimeError("commit_context_unavailable")
        repo_id = repo.get("id")
        owner = repo.get("owner")
        if not isinstance(repo_id, str) or not isinstance(owner, dict) or not isinstance(owner.get("id"), str) or repo.get("visibility") not in {"PUBLIC", "PRIVATE", "INTERNAL"}:
            raise RuntimeError("api_contract_violation")
        contribution_total = 0
        restricted_seen = False
        seen_cursors: set[str] = set()
        seen_days: set[tuple[str, str]] = set()
        for edge in connection["edges"]:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(edge, dict) or not isinstance(edge.get("cursor"), str) or not edge["cursor"] or edge["cursor"] in seen_cursors or not isinstance(node, dict) or node.get("__typename") != "CreatedCommitContribution" or not isinstance(node.get("isRestricted"), bool):
                raise RuntimeError("api_contract_violation")
            seen_cursors.add(edge["cursor"])
            if node["isRestricted"]:
                restricted_seen = True
                continue
            user = node.get("user")
            if not isinstance(node.get("occurredAt"), str) or not isinstance(node.get("commitCount"), int) or isinstance(node.get("commitCount"), bool) or node["commitCount"] <= 0 or not isinstance(user, dict) or user.get("__typename") != "User" or user.get("id") != member_node_id or not isinstance(node.get("repository"), dict) or node["repository"].get("id") != repo_id:
                raise RuntimeError("api_contract_violation")
            parse_rfc3339(node["occurredAt"])
            day = node["occurredAt"][:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise RuntimeError("api_contract_violation")
            try:
                date.fromisoformat(day)
            except ValueError:
                raise RuntimeError("api_contract_violation") from None
            day_key = (repo_id, day)
            if day_key in seen_days:
                raise RuntimeError("graphql_cardinality_mismatch")
            seen_days.add(day_key)
            if repo.get("visibility") == "PUBLIC" and start_day.isoformat() <= day <= end_day.isoformat():
                result.append({"repo_id": repo_id, "day": day, "quantity": node["commitCount"]})
            contribution_total += node["commitCount"]
        if not restricted_seen and contribution_total != connection["totalCount"]:
            raise RuntimeError("api_contract_violation")
    public_repo_ids = list(dict.fromkeys(row["repo_id"] for row in result))
    if not public_repo_ids:
        return []
    hydrated_data = client.graphql(COMMIT_REPOSITORY_HYDRATION_QUERY, {"ids": public_repo_ids})
    hydrated = hydrated_data.get("nodes") if isinstance(hydrated_data, dict) else None
    if not isinstance(hydrated, list) or len(hydrated) != len(public_repo_ids):
        raise RuntimeError("visibility_unverified")
    by_id = _ordered_node_map(hydrated, public_repo_ids)
    metadata_by_id: dict[str, RepositoryMetadata] = {}
    for repo_id in public_repo_ids:
        node = by_id[repo_id]
        owner = node.get("owner") if isinstance(node, dict) else None
        if node.get("__typename") != "Repository" or node.get("visibility") != "PUBLIC" or not isinstance(node.get("nameWithOwner"), str) or not isinstance(owner, dict) or not isinstance(owner.get("id"), str) or not isinstance(owner.get("login"), str):
            raise RuntimeError("visibility_unverified")
        metadata_by_id[repo_id] = RepositoryMetadata(repo_id, node["nameWithOwner"], owner["id"], owner["login"], "PUBLIC")
    return [{"repo": metadata_by_id[row["repo_id"]], "day": row["day"], "quantity": row["quantity"]} for row in result]


IDENTITY_QUERY = """
query($login:String!) { user(login:$login) { __typename id login } }
"""

HYDRATE_QUERY = """
query($ids:[ID!]!) {
  nodes(ids:$ids) {
    __typename id
    ... on PullRequest { number author { __typename ... on User { id } } createdAt mergedAt repository { id nameWithOwner visibility owner { id login } } }
    ... on Issue { number author { __typename ... on User { id } } createdAt repository { id nameWithOwner visibility owner { id login } } }
  }
}
"""

DISCOVERY_QUERY = """
query($ids:[ID!]!) {
  nodes(ids:$ids) {
    __typename id
    ... on PullRequest { author { __typename ... on User { id } } createdAt mergedAt repository { id visibility owner { id } } }
    ... on Issue { author { __typename ... on User { id } } createdAt repository { id visibility owner { id } } }
  }
}
"""

ISSUE_COMMENTS_QUERY = """
query($login:String!, $after:String) {
  user(login:$login) {
    issueComments(first:100, after:$after, orderBy:{field:UPDATED_AT, direction:DESC}) {
      totalCount edges { cursor node { __typename id author { __typename ... on User { id } } createdAt updatedAt pullRequest { id } issue { id } repository { id visibility owner { id } } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

ISSUE_COMMENT_DISCOVERY_QUERY = """
query($ids:[ID!]!) { nodes(ids:$ids) {
__typename id
  ... on IssueComment { author { __typename ... on User { id } } createdAt updatedAt pullRequest { id } issue { id } repository { id visibility owner { id } } }
} }
"""

ISSUE_COMMENT_HYDRATION_QUERY = """
query($ids:[ID!]!) { nodes(ids:$ids) {
  __typename id
  ... on Issue { number repository { id nameWithOwner visibility owner { id login } } }
} }
"""

ISSUE_COMMENT_ISSUE_DISCOVERY_QUERY = """
query($ids:[ID!]!) { nodes(ids:$ids) {
  __typename id
  ... on Issue { repository { id visibility owner { id } } }
} }
"""

REVIEW_CONTRIBUTIONS_QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!, $after:String) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
        pullRequestReviewContributions(first:100, after:$after) {
        totalCount edges { cursor node { __typename isRestricted occurredAt pullRequest { id } user { __typename id } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

REVIEW_PR_DISCOVERY_QUERY = """
query($ids:[ID!]!) { nodes(ids:$ids) {
  __typename id
  ... on PullRequest { repository { id visibility owner { id } } }
} }
"""

REVIEWS_QUERY = """
query($id:ID!, $after:String) {
  node(id:$id) {
    ... on PullRequest {
      reviews(first:100, after:$after) {
        totalCount edges { cursor node { __typename id state submittedAt author { __typename ... on User { id } } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

COMMIT_GROUPS_QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!, $after:String) {
  user(login:$login) { contributionsCollection(from:$from, to:$to) {
    commitContributionsByRepository(maxRepositories:100) {
      repository { id visibility owner { id } }
      contributions(first:100, after:$after) { totalCount edges { cursor node { __typename isRestricted occurredAt commitCount user { __typename id } repository { id } } } pageInfo { hasNextPage endCursor } }
    }
  } }
}
"""

COMMIT_REPOSITORY_HYDRATION_QUERY = """
query($ids:[ID!]!) { nodes(ids:$ids) {
  __typename id
  ... on Repository { nameWithOwner visibility owner { id login } }
} }
"""


def empty_statuses(config: AppConfig, period: ReportPeriod, *, observed_at: datetime) -> list[SourceStatus]:
    rows: list[SourceStatus] = []
    for member in sorted(config.members, key=lambda item: item.member_id):
        applicable = effective_window(period, member.active_from, member.active_until) is not None
        for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context"):
            criticality = "optional" if source == "commit_context" else "core"
            if not applicable:
                rows.append(SourceStatus(member.member_id, source, criticality, "not_applicable", "member_window_empty"))
            else:
                rows.append(SourceStatus(member.member_id, source, criticality, "not_run", "run_aborted"))
    return rows


def collect(config: AppConfig, period: ReportPeriod, client: GitHubClient, *, observed_at: datetime) -> CollectionResult:
    """Collect the REST-Search/GraphQL-discovery core sources.

    A source is only marked complete after both stable Search snapshots and every
    public hydration node have passed validation. Unsupported or incomplete sources
    remain ``not_run``/``partial``; they are never converted into zeroes.
    """
    statuses = empty_statuses(config, period, observed_at=observed_at)
    events: list[LedgerEvent] = []
    policy = config.repository_policy
    applied_owners: set[str] = set()
    applied_repos: set[str] = set()
    search_proofs: dict[tuple[str, str], str] = {}
    discovery_proofs: dict[tuple[str, str], datetime] = {}
    allowed_reasons = {"identity_resolution_failed", "identity_node_mismatch", "identity_type_mismatch", "identity_login_mismatch", "authentication_failed", "stability_gap_not_met", "run_aborted", "search_capped", "search_incomplete_results", "search_cardinality_mismatch", "search_snapshot_unstable", "search_candidate_conflict", "graphql_partial_response", "graphql_snapshot_unstable", "graphql_cardinality_mismatch", "pagination_incomplete", "cursor_invalid", "rate_limited", "transport_retry_exhausted", "api_contract_violation", "visibility_unverified", "repository_binding_changed", "commit_context_unavailable", "commit_period_not_day_aligned", "member_window_empty"}

    def set_status(member_id: str, source: str, status: str, reason: str | None, finished: datetime | None = None, proof_override: tuple[bool | None, bool | None, bool | None, bool | None] | None = None) -> None:
        if reason is not None and reason not in allowed_reasons:
            reason = "api_contract_violation"
        status = _canonical_status(status, reason)
        for index, row in enumerate(statuses):
            if row.member_id == member_id and row.source == source:
                complete = status == "complete"
                if complete:
                    pagination = True
                    partition = None if source in {"issue_replies", "prs_reviewed"} else True
                    snapshot = visibility = True
                    timestamp = format_z(finished or observed_at)
                else:
                    timestamp = None
                    if proof_override is not None:
                        pagination, partition, snapshot, visibility = proof_override
                        timestamp = format_z(finished) if snapshot and finished is not None else None
                    elif (member_id, source) in discovery_proofs and source != "commit_context" and reason not in _IDENTITY_REASONS:
                        pagination, partition, snapshot, visibility = True, True, True, False
                        timestamp = format_z(finished or discovery_proofs[(member_id, source)])
                    elif (member_id, source) in search_proofs and source != "commit_context" and reason not in _IDENTITY_REASONS:
                        pagination, partition, snapshot, visibility = True, True, False, False
                    elif status in {"not_applicable", "not_run"} or reason in _IDENTITY_REASONS or source == "commit_context":
                        pagination = partition = snapshot = visibility = None
                    else:
                        pagination = False
                        partition = None if source in {"issue_replies", "prs_reviewed"} else False
                        snapshot = False
                        visibility = None
                statuses[index] = SourceStatus(member_id, source, row.criticality, status, reason, pagination, partition, snapshot, visibility, timestamp)
                return

    for member in sorted(config.members, key=lambda item: item.member_id):
        window = effective_window(period, member.active_from, member.active_until)
        if window is None:
            continue
        try:
            identity = client.graphql(IDENTITY_QUERY, {"login": member.github_login}).get("user")
            if not isinstance(identity, dict) or identity.get("__typename") != "User":
                raise RuntimeError("identity_type_mismatch")
            if identity.get("id") != member.github_node_id:
                raise RuntimeError("identity_node_mismatch")
            if str(identity.get("login", "")).lower() != member.github_login.lower():
                raise RuntimeError("identity_login_mismatch")
        except Exception as exc:
            reason = getattr(exc, "args", ["identity_resolution_failed"])[0]
            if reason not in {"identity_type_mismatch", "identity_node_mismatch", "identity_login_mismatch", "authentication_failed"}:
                reason = "identity_resolution_failed"
            for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context"):
                for index, row in enumerate(statuses):
                    if row.member_id == member.member_id and row.source == source:
                        statuses[index] = SourceStatus(member.member_id, source, row.criticality, "failed", reason)
            if reason == "authentication_failed":
                applicable_ids = {item.member_id for item in config.members if effective_window(period, item.active_from, item.active_until) is not None}
                for index, row in enumerate(statuses):
                    if row.member_id in applicable_ids:
                        statuses[index] = SourceStatus(row.member_id, row.source, row.criticality, "failed", "authentication_failed")
                break
            continue

        start, end = window
        source_specs = (("prs_opened", "is:pr is:public author:" + member.github_login + "", "pr_opened", "createdAt"), ("issues_opened", "is:issue is:public author:" + member.github_login + "", "issue_opened", "createdAt"), ("authored_prs_merged", "is:pr is:public author:" + member.github_login + " is:merged", "pr_merged", "mergedAt"))
        candidate_rows: dict[str, tuple[str, str, list[Any]]] = {}
        for source, base, kind, time_field in source_specs:
            try:
                candidates = stable_search(client, base, start, end)
                search_proofs[(member.member_id, source)] = format_z(datetime.now(UTC).replace(microsecond=0))
                if any(candidate.actor_node_id != member.github_node_id for candidate in candidates):
                    set_status(member.member_id, source, "failed", "search_candidate_conflict", proof_override=(True, True, False, None))
                    continue
                candidate_rows[source] = (kind, time_field, candidates)
            except Exception as exc:
                reason = _exception_reason(exc, "search_snapshot_unstable")
                proof = (True, True, False, None) if reason == "search_snapshot_unstable" else None
                set_status(member.member_id, source, "partial", reason, proof_override=proof)

        all_candidates = [(source, kind, time_field, candidate) for source, (kind, time_field, candidates) in candidate_rows.items() for candidate in candidates]
        rest_ready = True
        if all_candidates:
            candidate_ids = list(dict.fromkeys(candidate.node_id for _, _, _, candidate in all_candidates))
            discovery_snapshots: list[tuple[tuple[Any, ...], ...]] = []
            discovery_nodes: list[dict[str, Any]] = []
            for _ in range(2):
                try:
                    discovery_data = client.graphql(DISCOVERY_QUERY, {"ids": candidate_ids})
                except Exception as exc:
                    for source, _, _, _ in all_candidates:
                        set_status(member.member_id, source, "partial", _exception_reason(exc, "graphql_partial_response"), proof_override=(True, True, False, None))
                    discovery_nodes = []
                    rest_ready = False
                    break
                raw_discovery = discovery_data.get("nodes") if isinstance(discovery_data, dict) else None
                try:
                    discovery_map = _ordered_node_map(raw_discovery, candidate_ids)
                except RuntimeError:
                    for source, _, _, _ in all_candidates:
                        set_status(member.member_id, source, "failed", "visibility_unverified")
                    discovery_nodes = []
                    rest_ready = False
                    break
                discovery_nodes = list(discovery_map.values())
                discovery_snapshots.append(tuple(sorted((node.get("id"), node.get("__typename"), (node.get("author") or {}).get("id"), node.get("createdAt"), node.get("mergedAt"), (node.get("repository") or {}).get("id"), (node.get("repository") or {}).get("visibility"), ((node.get("repository") or {}).get("owner") or {}).get("id")) for node in discovery_nodes)))
            if rest_ready and (not discovery_snapshots or discovery_snapshots[0] != discovery_snapshots[1]):
                for source, _, _, _ in all_candidates:
                    set_status(member.member_id, source, "partial", "graphql_snapshot_unstable", proof_override=(True, True, False, None))
                rest_ready = False
            if rest_ready:
                discovery_finished = datetime.now(UTC).replace(microsecond=0)
                for source, _, _, _ in all_candidates:
                    discovery_proofs[(member.member_id, source)] = discovery_finished
            if rest_ready and any((node.get("repository") or {}).get("visibility") != "PUBLIC" for node in discovery_nodes):
                for source, _, _, _ in all_candidates:
                    set_status(member.member_id, source, "failed", "visibility_unverified")
                rest_ready = False
            data = {"nodes": []}
            if rest_ready:
                try:
                    data = client.graphql(HYDRATE_QUERY, {"ids": candidate_ids})
                except Exception as exc:
                    finished = datetime.now(UTC).replace(microsecond=0)
                    for source, _, _, _ in all_candidates:
                        set_status(member.member_id, source, "partial", _exception_reason(exc, "graphql_partial_response"), finished, proof_override=(True, True, True, False))
                    rest_ready = False
            nodes = data.get("nodes") if isinstance(data, dict) else None
            if rest_ready and (not isinstance(nodes, list) or len(nodes) != len(candidate_ids)):
                for source, _, _, _ in all_candidates:
                    set_status(member.member_id, source, "failed", "visibility_unverified")
                rest_ready = False
            by_id: dict[str, dict[str, Any]] = {}
            if rest_ready:
                try:
                    by_id = _ordered_node_map(nodes, candidate_ids)
                except RuntimeError:
                    for source, _, _, _ in all_candidates:
                        set_status(member.member_id, source, "failed", "api_contract_violation")
                    rest_ready = False
            finished = datetime.now(UTC).replace(microsecond=0)
            if rest_ready:
                for source, kind, time_field, candidate in all_candidates:
                    node = by_id.get(candidate.node_id)
                    expected_type = "PullRequest" if kind in {"pr_opened", "pr_merged"} else "Issue"
                    if not isinstance(node, dict) or node.get("__typename") != expected_type:
                        set_status(member.member_id, source, "failed", "api_contract_violation")
                        continue
                    author = node.get("author")
                    repo = node.get("repository")
                    if not isinstance(author, dict) or author.get("id") != member.github_node_id or author.get("__typename") != "User" or not isinstance(repo, dict):
                        set_status(member.member_id, source, "failed", "api_contract_violation")
                        continue
                    metadata = RepositoryMetadata(str(repo.get("id", "")), str(repo.get("nameWithOwner", "")), str((repo.get("owner") or {}).get("id", "")), str((repo.get("owner") or {}).get("login", "")), str(repo.get("visibility", "")))
                    if not metadata.node_id or not metadata.full_name:
                        set_status(member.member_id, source, "failed", "visibility_unverified")
                        continue
                    if metadata.visibility != "PUBLIC":
                        set_status(member.member_id, source, "failed", "visibility_unverified")
                        continue
                    raw_time = node.get(time_field)
                    if not isinstance(raw_time, str):
                        set_status(member.member_id, source, "failed", "api_contract_violation")
                        continue
                    try:
                        occurred = parse_rfc3339(raw_time)
                    except ValueError:
                        set_status(member.member_id, source, "failed", "api_contract_violation")
                        continue
                    try:
                        parse_rfc3339(candidate.created_at)
                    except ValueError:
                        set_status(member.member_id, source, "failed", "search_candidate_conflict")
                        continue
                    if node.get("createdAt") != candidate.created_at:
                        set_status(member.member_id, source, "failed", "search_candidate_conflict")
                        continue
                    if not (start.astimezone(UTC) <= occurred < end.astimezone(UTC)):
                        continue
                    number = node.get("number")
                    if not isinstance(number, int) or number <= 0:
                        set_status(member.member_id, source, "failed", "api_contract_violation")
                        continue
                    url_kind = "pull" if node["__typename"] == "PullRequest" else "issues"
                    events.append(LedgerEvent(member.member_id, member.github_node_id, kind, candidate.node_id, candidate.node_id, metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), format_z(occurred), None, 1, format_z(finished), format_z(finished), f"search-{source}-{basic_utc(start)}--{basic_utc(end)}", f"https://github.com/{metadata.full_name}/{url_kind}/{number}"))
            for source, _, _, _ in source_specs:
                if not any(row.member_id == member.member_id and row.source == source and row.status in {"failed", "partial"} for row in statuses):
                    set_status(member.member_id, source, "complete", None, finished)
        else:
            for source, _, _, _ in source_specs:
                if not any(row.member_id == member.member_id and row.source == source and row.status in {"failed", "partial"} for row in statuses):
                    set_status(member.member_id, source, "complete", None, observed_at)

        # Issue replies: UPDATED_AT is the only server ordering, so every page is
        # consumed before the createdAt and PR-conversation filters are applied.
        issue_snapshot_at: datetime | None = None
        try:
            snapshots: list[tuple[tuple[str, str, str], ...]] = []
            snapshot_rows: list[dict[str, Any]] = []
            for _ in range(2):
                rows = client.connection(ISSUE_COMMENTS_QUERY, {"login": member.github_login}, ("user", "issueComments"))
                eligible: list[dict[str, Any]] = []
                for row in rows:
                    author = row.get("author")
                    issue = row.get("issue")
                    repo = row.get("repository")
                    if row.get("__typename") != "IssueComment" or not isinstance(row.get("id"), str) or not row.get("id") or not isinstance(author, dict) or author.get("__typename") != "User" or author.get("id") != member.github_node_id or not isinstance(issue, dict) or not isinstance(issue.get("id"), str) or not issue.get("id") or not isinstance(repo, dict) or not isinstance(repo.get("id"), str) or not repo.get("id") or not isinstance((repo.get("owner") or {}).get("id"), str):
                        raise RuntimeError("api_contract_violation")
                    if row.get("pullRequest") is not None:
                        continue
                    created = row.get("createdAt")
                    if not isinstance(created, str) or not (start.astimezone(UTC) <= parse_rfc3339(created) < end.astimezone(UTC)):
                        continue
                    eligible.append({"id": row["id"], "issue_id": issue.get("id"), "created": created})
                comment_ids = list(dict.fromkeys(item["id"] for item in eligible))
                discovered_data = client.graphql(ISSUE_COMMENT_DISCOVERY_QUERY, {"ids": comment_ids}) if comment_ids else {"nodes": []}
                discovered = discovered_data.get("nodes") if isinstance(discovered_data, dict) else None
                if not isinstance(discovered, list) or len(discovered) != len(comment_ids):
                    raise RuntimeError("visibility_unverified")
                discovered_by_id = _ordered_node_map(discovered, comment_ids)
                public_eligible: list[dict[str, Any]] = []
                for item in eligible:
                    comment = discovered_by_id[item["id"]]
                    comment_repo = comment.get("repository") if isinstance(comment, dict) else None
                    comment_issue = comment.get("issue") if isinstance(comment, dict) else None
                    if not isinstance(comment, dict) or comment.get("__typename") != "IssueComment" or comment.get("createdAt") != item["created"] or not isinstance(comment.get("updatedAt"), str) or comment.get("pullRequest") is not None or not isinstance(comment_issue, dict) or comment_issue.get("id") != item["issue_id"] or not isinstance(comment_repo, dict) or comment_repo.get("visibility") not in {"PUBLIC", "PRIVATE", "INTERNAL"}:
                        raise RuntimeError("api_contract_violation")
                    if comment_repo.get("visibility") == "PUBLIC":
                        public_eligible.append(item)
                issue_ids = list(dict.fromkeys(item["issue_id"] for item in public_eligible))
                issue_discovery_data = client.graphql(ISSUE_COMMENT_ISSUE_DISCOVERY_QUERY, {"ids": issue_ids}) if issue_ids else {"nodes": []}
                issue_discovery_nodes = issue_discovery_data.get("nodes") if isinstance(issue_discovery_data, dict) else None
                if not isinstance(issue_discovery_nodes, list) or len(issue_discovery_nodes) != len(issue_ids):
                    raise RuntimeError("visibility_unverified")
                issue_discovery = _ordered_node_map(issue_discovery_nodes, issue_ids)
                public_issue_ids: list[str] = []
                for issue_id in issue_ids:
                    node = issue_discovery[issue_id]
                    repo = node.get("repository") if isinstance(node, dict) else None
                    if node.get("__typename") != "Issue" or not isinstance(repo, dict) or repo.get("visibility") not in {"PUBLIC", "PRIVATE", "INTERNAL"}:
                        raise RuntimeError("api_contract_violation")
                    if repo.get("visibility") == "PUBLIC":
                        public_issue_ids.append(issue_id)
                public_eligible = [item for item in public_eligible if item["issue_id"] in set(public_issue_ids)]
                hydrated_data = client.graphql(ISSUE_COMMENT_HYDRATION_QUERY, {"ids": public_issue_ids}) if public_issue_ids else {"nodes": []}
                hydrated = hydrated_data.get("nodes") if isinstance(hydrated_data, dict) else None
                if not isinstance(hydrated, list) or len(hydrated) != len(public_issue_ids):
                    raise RuntimeError("visibility_unverified")
                issue_by_id = _ordered_node_map(hydrated, public_issue_ids)
                final_rows: list[dict[str, Any]] = []
                for item in public_eligible:
                    comment = discovered_by_id.get(item["id"])
                    issue_node = issue_by_id.get(item["issue_id"])
                    comment_issue = comment.get("issue") if isinstance(comment, dict) else None
                    comment_repo = comment.get("repository") if isinstance(comment, dict) else None
                    repo = issue_node.get("repository") if isinstance(issue_node, dict) else None
                    if not isinstance(comment, dict) or comment.get("__typename") != "IssueComment" or not isinstance(comment.get("author"), dict) or comment["author"].get("__typename") != "User" or comment["author"].get("id") != member.github_node_id or comment.get("createdAt") != item["created"] or not isinstance(comment.get("updatedAt"), str) or comment.get("pullRequest") is not None or not isinstance(comment_issue, dict) or comment_issue.get("id") != item["issue_id"] or not isinstance(comment_repo, dict) or comment_repo.get("visibility") != "PUBLIC" or not isinstance(comment_repo.get("id"), str) or not isinstance((comment_repo.get("owner") or {}).get("id"), str) or not isinstance(issue_node, dict) or issue_node.get("__typename") != "Issue" or not isinstance(issue_node.get("number"), int) or not isinstance(repo, dict):
                        raise RuntimeError("api_contract_violation")
                    metadata = RepositoryMetadata(str(repo.get("id", "")), str(repo.get("nameWithOwner", "")), str((repo.get("owner") or {}).get("id", "")), str((repo.get("owner") or {}).get("login", "")), str(repo.get("visibility", "")))
                    if metadata.visibility != "PUBLIC" or comment_repo.get("id") != metadata.node_id or comment_repo.get("visibility") != metadata.visibility or comment_repo.get("owner", {}).get("id") != metadata.owner_node_id:
                        raise RuntimeError("visibility_unverified")
                    final_rows.append({**item, "issue_number": issue_node["number"], "repo": metadata})
                eligible = final_rows
                digest = tuple(sorted((str(item["id"]), str(item["issue_id"]), item["created"], item["repo"].node_id, item["repo"].full_name, item["repo"].owner_node_id) for item in eligible))
                snapshots.append(digest)
                snapshot_rows = eligible
            if snapshots[0] != snapshots[1]:
                raise RuntimeError("graphql_snapshot_unstable")
            finished = datetime.now(UTC).replace(microsecond=0)
            issue_snapshot_at = finished
            for item in snapshot_rows:
                metadata = item["repo"]
                if not isinstance(item["issue_number"], int) or not isinstance(item["issue_id"], str):
                    raise RuntimeError("api_contract_violation")
                events.append(LedgerEvent(member.member_id, member.github_node_id, "issue_replied", str(item["id"]), item["issue_id"], metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), format_z(parse_rfc3339(item["created"])), None, 1, format_z(finished), format_z(finished), "root", f"https://github.com/{metadata.full_name}/issues/{item['issue_number']}"))
            set_status(member.member_id, "issue_replies", "complete", None, finished)
        except Exception as exc:
            reason = _exception_reason(exc, "graphql_partial_response")
            proof = (True, None, True, False) if issue_snapshot_at is not None else (True, None, False, None) if reason == "graphql_snapshot_unstable" else None
            set_status(member.member_id, "issue_replies", "partial", reason, finished=issue_snapshot_at, proof_override=proof)

        # Reviews use contribution nodes only to discover target PRs. The PR's
        # complete reviews connection supplies the canonical submittedAt event.
        review_snapshot_at: datetime | None = None
        try:
            review_snapshots: list[tuple[tuple[str, str, str], ...]] = []
            representative_rows: list[tuple[str, dict[str, Any]]] = []
            review_discovery: dict[str, tuple[str, str]] = {}
            variables = {"login": member.github_login, "from": format_z(start.astimezone(UTC)), "to": format_z(end.astimezone(UTC))}
            for _ in range(2):
                contributions = client.connection(REVIEW_CONTRIBUTIONS_QUERY, variables, ("user", "contributionsCollection", "pullRequestReviewContributions"))
                pr_ids: set[str] = set()
                for row in contributions:
                    if not isinstance(row.get("isRestricted"), bool):
                        raise RuntimeError("api_contract_violation")
                    if row.get("isRestricted") is True:
                        continue
                    contributor = row.get("user")
                    pull_request = row.get("pullRequest")
                    if not isinstance(contributor, dict) or contributor.get("__typename") != "User" or contributor.get("id") != member.github_node_id or not isinstance(pull_request, dict) or not isinstance(pull_request.get("id"), str):
                        raise RuntimeError("api_contract_violation")
                    pr_ids.add(pull_request["id"])
                discovery_data = client.graphql(REVIEW_PR_DISCOVERY_QUERY, {"ids": sorted(pr_ids)}) if pr_ids else {"nodes": []}
                discovered = discovery_data.get("nodes") if isinstance(discovery_data, dict) else None
                if not isinstance(discovered, list) or len(discovered) != len(pr_ids):
                    raise RuntimeError("visibility_unverified")
                discovered_by_id = _ordered_node_map(discovered, sorted(pr_ids))
                for pr_id in sorted(pr_ids):
                    node = discovered_by_id[pr_id]
                    repo = node.get("repository") if isinstance(node, dict) else None
                    if node.get("__typename") != "PullRequest" or not isinstance(repo, dict) or repo.get("visibility") != "PUBLIC" or not isinstance(repo.get("id"), str) or not isinstance((repo.get("owner") or {}).get("id"), str):
                        raise RuntimeError("visibility_unverified")
                    review_discovery[pr_id] = (repo["id"], repo["owner"]["id"])
                reps: list[tuple[str, dict[str, Any]]] = []
                for pr_id in sorted(pr_ids):
                    reviews = client.connection(REVIEWS_QUERY, {"id": pr_id}, ("node", "reviews"))
                    eligible_reviews: list[dict[str, Any]] = []
                    for review in reviews:
                        if not isinstance(review, dict) or review.get("__typename") != "PullRequestReview" or not isinstance(review.get("id"), str) or not review.get("id"):
                            raise RuntimeError("api_contract_violation")
                        author = review.get("author")
                        if not isinstance(author, dict) or author.get("__typename") != "User" or author.get("id") != member.github_node_id:
                            continue
                        state = review.get("state")
                        submitted = review.get("submittedAt")
                        if state == "PENDING":
                            if submitted is not None:
                                raise RuntimeError("api_contract_violation")
                            continue
                        if not isinstance(submitted, str):
                            raise RuntimeError("api_contract_violation")
                        parsed = parse_rfc3339(submitted)
                        if start.astimezone(UTC) <= parsed < end.astimezone(UTC):
                            eligible_reviews.append({"id": review.get("id"), "submitted": submitted})
                    if not eligible_reviews:
                        raise RuntimeError("api_contract_violation")
                    if eligible_reviews:
                        reps.append((pr_id, min(eligible_reviews, key=lambda item: (parse_rfc3339(item["submitted"]), item["id"]))))
                digest = tuple(sorted((pr_id, str(review["id"]), review["submitted"]) for pr_id, review in reps))
                review_snapshots.append(digest)
                representative_rows = reps
            if review_snapshots[0] != review_snapshots[1]:
                raise RuntimeError("graphql_snapshot_unstable")
            review_snapshot_at = datetime.now(UTC).replace(microsecond=0)
            if representative_rows:
                hydrated = client.graphql(HYDRATE_QUERY, {"ids": [pr_id for pr_id, _ in representative_rows]}).get("nodes")
                by_id = _ordered_node_map(hydrated, [pr_id for pr_id, _ in representative_rows])
                finished = datetime.now(UTC).replace(microsecond=0)
                for pr_id, review in representative_rows:
                    node = by_id.get(pr_id)
                    repo = node.get("repository") if isinstance(node, dict) else None
                    if not isinstance(node, dict) or node.get("__typename") != "PullRequest" or not isinstance(repo, dict):
                        raise RuntimeError("visibility_unverified")
                    metadata = RepositoryMetadata(str(repo.get("id", "")), str(repo.get("nameWithOwner", "")), str((repo.get("owner") or {}).get("id", "")), str((repo.get("owner") or {}).get("login", "")), str(repo.get("visibility", "")))
                    if metadata.visibility != "PUBLIC" or review_discovery.get(pr_id) != (metadata.node_id, metadata.owner_node_id):
                        raise RuntimeError("visibility_unverified")
                    if not isinstance(node.get("number"), int) or node["number"] <= 0:
                        raise RuntimeError("api_contract_violation")
                    events.append(LedgerEvent(member.member_id, member.github_node_id, "pr_reviewed", str(review["id"]), pr_id, metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), format_z(parse_rfc3339(review["submitted"])), None, 1, format_z(finished), format_z(finished), "root", f"https://github.com/{metadata.full_name}/pull/{node['number']}"))
                set_status(member.member_id, "prs_reviewed", "complete", None, finished)
            else:
                set_status(member.member_id, "prs_reviewed", "complete", None, observed_at)
        except Exception as exc:
            reason = _exception_reason(exc, "graphql_partial_response")
            proof = (True, None, True, False) if review_snapshot_at is not None else (True, None, False, None) if reason == "graphql_snapshot_unstable" else None
            set_status(member.member_id, "prs_reviewed", "partial", reason, finished=review_snapshot_at, proof_override=proof)

        # Commit context is optional, but a complete day-aligned empty result is
        # distinct from unavailable context. The outer API has no totalCount, so
        # exactly 100 groups is conservatively downgraded.
        if start.hour or start.minute or start.second or end.hour or end.minute or end.second:
            set_status(member.member_id, "commit_context", "not_applicable", "commit_period_not_day_aligned")
        else:
            try:
                commit_snapshots: list[tuple[tuple[str, str, int], ...]] = []
                last_commit_rows: list[dict[str, Any]] = []
                for _ in range(2):
                    current = _commit_snapshot(client, member.github_login, member.github_node_id, start, end, policy)
                    digest = tuple(sorted((item["repo"].node_id, item["day"], item["quantity"]) for item in current))
                    commit_snapshots.append(digest)
                    last_commit_rows = current
                if commit_snapshots[0] != commit_snapshots[1]:
                    raise RuntimeError("commit_context_unavailable")
                finished = datetime.now(UTC).replace(microsecond=0)
                for item in last_commit_rows:
                    metadata = item["repo"]
                    next_day = (datetime.fromisoformat(item["day"]) + timedelta(days=1)).date().isoformat()
                    events.append(LedgerEvent(member.member_id, member.github_node_id, "commit_day", None, metadata.node_id, metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), None, item["day"], item["quantity"], format_z(finished), format_z(finished), f"commit-root-{start.date().isoformat()}--{end.date().isoformat()}", f"https://github.com/{metadata.full_name}/commits?author={member.github_login}&since={item['day']}T00%3A00%3A00Z&until={next_day}T00%3A00%3A00Z"))
                set_status(member.member_id, "commit_context", "complete", None, finished)
            except Exception as exc:
                set_status(member.member_id, "commit_context", "partial", "commit_context_unavailable")
    gate_at: datetime | None = None
    if events:
        gate_discovery_query = """
        query($ids:[ID!]!) { nodes(ids:$ids) {
          __typename id
          ... on PullRequest { repository { id visibility owner { id } } }
          ... on Issue { repository { id visibility owner { id } } }
          ... on Repository { visibility owner { id } }
        } }
        """
        gate_hydration_query = """
        query($ids:[ID!]!) { nodes(ids:$ids) {
          __typename id
          ... on PullRequest { number repository { id visibility nameWithOwner owner { id login } } }
          ... on Issue { number repository { id visibility nameWithOwner owner { id login } } }
          ... on Repository { nameWithOwner visibility owner { id login } }
        } }
        """
        gate_event_query = """
        query($ids:[ID!]!) { nodes(ids:$ids) {
          __typename id
          ... on IssueComment { issue { id repository { id visibility owner { id } } } }
          ... on PullRequestReview { pullRequest { id repository { id visibility owner { id } } } }
        } }
        """
        verified_events: list[LedgerEvent] = []
        for member_id, source in sorted({(event.member_id, event.source) for event in events}):
            source_events = [event for event in events if event.member_id == member_id and event.source == source]
            try:
                subject_ids = sorted({event.subject_node_id for event in source_events})
                response = client.graphql(gate_discovery_query, {"ids": subject_ids})
                discovery_nodes = response.get("nodes") if isinstance(response, dict) else None
                discovery = _ordered_node_map(discovery_nodes, subject_ids)
                for event in source_events:
                    node = discovery.get(event.subject_node_id)
                    repo = _gate_repository(node)
                    expected_type = "Repository" if event.event_kind == "commit_day" else "Issue" if event.event_kind in {"issue_opened", "issue_replied"} else "PullRequest"
                    if not isinstance(node, dict) or node.get("id") != event.subject_node_id or node.get("__typename") != expected_type or not isinstance(repo, dict) or repo.get("id") != event.repo_node_id or repo.get("visibility") != "PUBLIC" or (repo.get("owner") or {}).get("id") != event.owner_node_id:
                        raise RuntimeError("repository_binding_changed")
                event_ids = sorted({event.event_node_id for event in source_events if event.event_node_id is not None and event.event_kind in {"issue_replied", "pr_reviewed"}})
                if event_ids:
                    response = client.graphql(gate_event_query, {"ids": event_ids})
                    event_nodes = response.get("nodes") if isinstance(response, dict) else None
                    event_map = _ordered_node_map(event_nodes, event_ids)
                    for event in source_events:
                        if event.event_node_id is None or event.event_kind not in {"issue_replied", "pr_reviewed"}:
                            continue
                        event_node = event_map.get(event.event_node_id)
                        target = event_node.get("issue") if event.event_kind == "issue_replied" and isinstance(event_node, dict) and event_node.get("__typename") == "IssueComment" else event_node.get("pullRequest") if event.event_kind == "pr_reviewed" and isinstance(event_node, dict) and event_node.get("__typename") == "PullRequestReview" else None
                        target_repo = target.get("repository") if isinstance(target, dict) else None
                        if not isinstance(target, dict) or target.get("id") != event.subject_node_id or not isinstance(target_repo, dict) or target_repo.get("id") != event.repo_node_id or target_repo.get("visibility") != "PUBLIC" or (target_repo.get("owner") or {}).get("id") != event.owner_node_id:
                            raise RuntimeError("repository_binding_changed")
                response = client.graphql(gate_hydration_query, {"ids": subject_ids})
                hydration_nodes = response.get("nodes") if isinstance(response, dict) else None
                hydration = _ordered_node_map(hydration_nodes, subject_ids)
                for event in source_events:
                    node = hydration.get(event.subject_node_id)
                    repo = _gate_repository(node)
                    if not isinstance(node, dict) or not isinstance(repo, dict) or repo.get("id") != event.repo_node_id or repo.get("visibility") != "PUBLIC" or repo.get("nameWithOwner") != event.repo_full_name or (repo.get("owner") or {}).get("id") != event.owner_node_id:
                        raise RuntimeError("repository_binding_changed")
                for event in source_events:
                    metadata = RepositoryMetadata(event.repo_node_id, event.repo_full_name, event.owner_node_id, event.owner_login, "PUBLIC")
                    if public_and_allowed(metadata, policy):
                        verified_events.append(event)
                    else:
                        if event.owner_node_id in policy.excluded_owner_ids:
                            applied_owners.add(event.owner_node_id)
                        if event.repo_node_id in policy.excluded_repo_ids:
                            applied_repos.add(event.repo_node_id)
            except Exception as exc:
                reason = _exception_reason(exc, "visibility_unverified")
                if reason == "authentication_failed":
                    _record_final_gate_auth_failure(statuses)
                elif source == "commit_context" and reason != "repository_binding_changed":
                    _record_optional_commit_gate_failure(statuses, member_id)
                else:
                    for row in list(statuses):
                        if row.status == "complete" and row.member_id == member_id and row.source == source:
                            _record_final_gate_failure(statuses, row.member_id, row.source, reason)
        gate_at = datetime.now(UTC).replace(microsecond=0)
        events = [replace(event, visibility_verified_at=format_z(gate_at)) for event in verified_events]
    if gate_at is None:
        gate_at = datetime.now(UTC).replace(microsecond=0)
    return CollectionResult(events, statuses, sorted(applied_owners), sorted(applied_repos), format_z(gate_at))
