from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .github_client import GitHubClient
from .models import LedgerEvent, SourceStatus
from .period import ReportPeriod, effective_window, format_z, parse_rfc3339
from .search import stable_search
from .repository_policy import RepositoryMetadata, public_and_allowed


@dataclass(slots=True)
class CollectionResult:
    events: list[LedgerEvent]
    statuses: list[SourceStatus]
    applied_owner_ids: list[str]
    applied_repo_ids: list[str]


IDENTITY_QUERY = """
query($login:String!) { user(login:$login) { __typename id login } }
"""

HYDRATE_QUERY = """
query($ids:[ID!]!) {
  nodes(ids:$ids) {
    __typename id
    ... on PullRequest { number author { __typename id } createdAt mergedAt repository { id nameWithOwner visibility owner { id login } } }
    ... on Issue { number author { __typename id } createdAt repository { id nameWithOwner visibility owner { id login } } }
  }
}
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

    def set_status(member_id: str, source: str, status: str, reason: str | None, finished: datetime | None = None) -> None:
        for index, row in enumerate(statuses):
            if row.member_id == member_id and row.source == source:
                complete = status == "complete"
                statuses[index] = SourceStatus(member_id, source, row.criticality, status, reason, True if complete else False, True if complete else False, True if complete else False, True if complete else False, format_z(finished or observed_at) if complete else None)
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
            if reason not in {"identity_type_mismatch", "identity_node_mismatch", "identity_login_mismatch"}:
                reason = "identity_resolution_failed"
            for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged"):
                set_status(member.member_id, source, "failed", reason)
            continue

        start, end = window
        source_specs = (("prs_opened", "is:pr is:public author:" + member.github_login + "", "pr_opened", "createdAt"), ("issues_opened", "is:issue is:public author:" + member.github_login + "", "issue_opened", "createdAt"), ("authored_prs_merged", "is:pr is:public author:" + member.github_login + " is:merged", "pr_merged", "mergedAt"))
        candidate_rows: dict[str, tuple[str, str, list[Any]]] = {}
        for source, base, kind, time_field in source_specs:
            try:
                candidates = stable_search(client, base, start, end)
                candidate_rows[source] = (kind, time_field, candidates)
            except Exception as exc:
                set_status(member.member_id, source, "partial", getattr(exc, "reason", "search_snapshot_unstable"))

        all_candidates = [(source, kind, time_field, candidate) for source, (kind, time_field, candidates) in candidate_rows.items() for candidate in candidates]
        if all_candidates:
            data = client.graphql(HYDRATE_QUERY, {"ids": list(dict.fromkeys(candidate.node_id for _, _, _, candidate in all_candidates))})
            nodes = data.get("nodes") if isinstance(data, dict) else None
            if not isinstance(nodes, list) or len(nodes) != len(set(candidate.node_id for _, _, _, candidate in all_candidates)):
                for source, _, _, _ in all_candidates:
                    set_status(member.member_id, source, "failed", "visibility_unverified")
                continue
            by_id = {node.get("id"): node for node in nodes if isinstance(node, dict)}
            finished = datetime.now(UTC).replace(microsecond=0)
            for source, kind, time_field, candidate in all_candidates:
                node = by_id.get(candidate.node_id)
                if not isinstance(node, dict) or node.get("__typename") not in {"PullRequest", "Issue"}:
                    set_status(member.member_id, source, "failed", "api_contract_violation")
                    continue
                author = node.get("author")
                repo = node.get("repository")
                if not isinstance(author, dict) or author.get("id") != member.github_node_id or author.get("__typename") != "User" or not isinstance(repo, dict):
                    set_status(member.member_id, source, "failed", "api_contract_violation")
                    continue
                metadata = RepositoryMetadata(str(repo.get("id", "")), str(repo.get("nameWithOwner", "")), str((repo.get("owner") or {}).get("id", "")), str((repo.get("owner") or {}).get("login", "")), str(repo.get("visibility", "")))
                if not metadata.node_id or not metadata.full_name or not public_and_allowed(metadata, policy):
                    if metadata.node_id and metadata.visibility == "PUBLIC" and metadata.node_id in policy.excluded_repo_ids:
                        applied_repos.add(metadata.node_id)
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
                if not (start.astimezone(UTC) <= occurred < end.astimezone(UTC)):
                    continue
                number = node.get("number")
                if not isinstance(number, int) or number <= 0:
                    set_status(member.member_id, source, "failed", "api_contract_violation")
                    continue
                url_kind = "pull" if node["__typename"] == "PullRequest" else "issues"
                events.append(LedgerEvent(member.member_id, member.github_node_id, kind, candidate.node_id, candidate.node_id, metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), format_z(occurred), None, 1, format_z(finished), format_z(finished), f"search-{source}-root", f"https://github.com/{metadata.full_name}/{url_kind}/{number}"))
                applied_owners.add(metadata.owner_node_id)
            for source, _, _, _ in all_candidates:
                if not any(row.member_id == member.member_id and row.source == source and row.status in {"failed", "partial"} for row in statuses):
                    set_status(member.member_id, source, "complete", None, finished)
        else:
            for source, _, _ in source_specs:
                if not any(row.member_id == member.member_id and row.source == source and row.status in {"failed", "partial"} for row in statuses):
                    set_status(member.member_id, source, "complete", None, observed_at)
    return CollectionResult(events, statuses, sorted(applied_owners), sorted(applied_repos))
