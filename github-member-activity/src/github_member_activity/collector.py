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

ISSUE_COMMENTS_QUERY = """
query($login:String!, $after:String) {
  user(login:$login) {
    issueComments(first:100, after:$after, orderBy:{field:UPDATED_AT, direction:DESC}) {
      totalCount edges { cursor node { __typename id author { __typename id } createdAt pullRequest { id } issue { id number } repository { id nameWithOwner visibility owner { id login } } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

REVIEW_CONTRIBUTIONS_QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!, $after:String) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      pullRequestReviewContributions(first:100, after:$after) {
        totalCount edges { cursor node { __typename isRestricted occurredAt pullRequest { id } contributor { __typename id } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

REVIEWS_QUERY = """
query($id:ID!, $after:String) {
  node(id:$id) {
    ... on PullRequest {
      reviews(first:100, after:$after) {
        totalCount edges { cursor node { __typename id state submittedAt author { __typename id } } }
        pageInfo { hasNextPage endCursor }
      }
    }
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
            for source, _, _, _ in source_specs:
                if not any(row.member_id == member.member_id and row.source == source and row.status in {"failed", "partial"} for row in statuses):
                    set_status(member.member_id, source, "complete", None, observed_at)

        # Issue replies: UPDATED_AT is the only server ordering, so every page is
        # consumed before the createdAt and PR-conversation filters are applied.
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
                    if row.get("__typename") != "IssueComment" or not isinstance(author, dict) or author.get("__typename") != "User" or author.get("id") != member.github_node_id or not isinstance(issue, dict) or not isinstance(repo, dict):
                        raise RuntimeError("api_contract_violation")
                    if row.get("pullRequest") is not None:
                        continue
                    created = row.get("createdAt")
                    if not isinstance(created, str) or not (start.astimezone(UTC) <= parse_rfc3339(created) < end.astimezone(UTC)):
                        continue
                    metadata = RepositoryMetadata(str(repo.get("id", "")), str(repo.get("nameWithOwner", "")), str((repo.get("owner") or {}).get("id", "")), str((repo.get("owner") or {}).get("login", "")), str(repo.get("visibility", "")))
                    if not public_and_allowed(metadata, policy):
                        continue
                    eligible.append({"id": row["id"], "issue_id": issue.get("id"), "issue_number": issue.get("number"), "created": created, "repo": metadata})
                digest = tuple(sorted((str(item["id"]), str(item["issue_id"]), item["created"]) for item in eligible))
                snapshots.append(digest)
                snapshot_rows = eligible
            if snapshots[0] != snapshots[1]:
                raise RuntimeError("graphql_snapshot_unstable")
            finished = datetime.now(UTC).replace(microsecond=0)
            for item in snapshot_rows:
                metadata = item["repo"]
                if not isinstance(item["issue_number"], int) or not isinstance(item["issue_id"], str):
                    raise RuntimeError("api_contract_violation")
                events.append(LedgerEvent(member.member_id, member.github_node_id, "issue_replied", str(item["id"]), item["issue_id"], metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), format_z(parse_rfc3339(item["created"])), None, 1, format_z(finished), format_z(finished), "root", f"https://github.com/{metadata.full_name}/issues/{item['issue_number']}"))
            set_status(member.member_id, "issue_replies", "complete", None, finished)
        except Exception as exc:
            set_status(member.member_id, "issue_replies", "partial", getattr(exc, "args", ["graphql_partial_response"])[0])

        # Reviews use contribution nodes only to discover target PRs. The PR's
        # complete reviews connection supplies the canonical submittedAt event.
        try:
            review_snapshots: list[tuple[tuple[str, str, str], ...]] = []
            representative_rows: list[tuple[str, dict[str, Any]]] = []
            variables = {"login": member.github_login, "from": format_z(start.astimezone(UTC)), "to": format_z(end.astimezone(UTC))}
            for _ in range(2):
                contributions = client.connection(REVIEW_CONTRIBUTIONS_QUERY, variables, ("user", "contributionsCollection", "pullRequestReviewContributions"))
                pr_ids: set[str] = set()
                for row in contributions:
                    if row.get("isRestricted") is True:
                        continue
                    contributor = row.get("contributor")
                    pull_request = row.get("pullRequest")
                    if not isinstance(contributor, dict) or contributor.get("__typename") != "User" or contributor.get("id") != member.github_node_id or not isinstance(pull_request, dict) or not isinstance(pull_request.get("id"), str):
                        raise RuntimeError("api_contract_violation")
                    pr_ids.add(pull_request["id"])
                reps: list[tuple[str, dict[str, Any]]] = []
                for pr_id in sorted(pr_ids):
                    reviews = client.connection(REVIEWS_QUERY, {"id": pr_id}, ("node", "reviews"))
                    eligible_reviews: list[dict[str, Any]] = []
                    for review in reviews:
                        author = review.get("author")
                        state = review.get("state")
                        submitted = review.get("submittedAt")
                        if not isinstance(author, dict) or author.get("__typename") != "User" or author.get("id") != member.github_node_id:
                            continue
                        if state == "PENDING":
                            if submitted is not None:
                                raise RuntimeError("api_contract_violation")
                            continue
                        if not isinstance(submitted, str):
                            raise RuntimeError("api_contract_violation")
                        parsed = parse_rfc3339(submitted)
                        if start.astimezone(UTC) <= parsed < end.astimezone(UTC):
                            eligible_reviews.append({"id": review.get("id"), "submitted": submitted})
                    if eligible_reviews:
                        reps.append((pr_id, min(eligible_reviews, key=lambda item: (item["submitted"], item["id"]))))
                digest = tuple(sorted((pr_id, str(review["id"]), review["submitted"]) for pr_id, review in reps))
                review_snapshots.append(digest)
                representative_rows = reps
            if review_snapshots[0] != review_snapshots[1]:
                raise RuntimeError("graphql_snapshot_unstable")
            if representative_rows:
                hydrated = client.graphql(HYDRATE_QUERY, {"ids": [pr_id for pr_id, _ in representative_rows]}).get("nodes")
                by_id = {node.get("id"): node for node in hydrated if isinstance(node, dict)} if isinstance(hydrated, list) else {}
                finished = datetime.now(UTC).replace(microsecond=0)
                for pr_id, review in representative_rows:
                    node = by_id.get(pr_id)
                    repo = node.get("repository") if isinstance(node, dict) else None
                    if not isinstance(node, dict) or node.get("__typename") != "PullRequest" or not isinstance(repo, dict):
                        raise RuntimeError("visibility_unverified")
                    metadata = RepositoryMetadata(str(repo.get("id", "")), str(repo.get("nameWithOwner", "")), str((repo.get("owner") or {}).get("id", "")), str((repo.get("owner") or {}).get("login", "")), str(repo.get("visibility", "")))
                    if not public_and_allowed(metadata, policy) or not isinstance(node.get("number"), int):
                        continue
                    events.append(LedgerEvent(member.member_id, member.github_node_id, "pr_reviewed", str(review["id"]), pr_id, metadata.node_id, metadata.full_name, metadata.owner_node_id, metadata.owner_login.lower(), format_z(parse_rfc3339(review["submitted"])), None, 1, format_z(finished), format_z(finished), "root", f"https://github.com/{metadata.full_name}/pull/{node['number']}"))
                set_status(member.member_id, "prs_reviewed", "complete", None, finished)
            else:
                set_status(member.member_id, "prs_reviewed", "complete", None, observed_at)
        except Exception as exc:
            set_status(member.member_id, "prs_reviewed", "partial", getattr(exc, "args", ["graphql_partial_response"])[0])
    return CollectionResult(events, statuses, sorted(applied_owners), sorted(applied_repos))
