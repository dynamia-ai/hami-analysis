from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import CORE_SOURCES, EVENT_SOURCE, LedgerEvent


METRICS = ("prs_opened", "issues_opened", "issue_replies_created", "issues_replied_to", "prs_reviewed", "authored_prs_merged", "repositories_touched", "owners_touched", "external_repositories_touched", "repositories_accepting_prs", "commit_contributions", "commit_days", "repositories_with_commits")


def aggregate(events: list[LedgerEvent], member_ids: list[str], logins: dict[str, str], first_party_owners: set[str], commit_available: dict[str, bool] | None = None) -> dict[str, Any]:
    by_member: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in events:
        by_member[event.member_id].append(event)
    members = []
    for member_id in sorted(member_ids):
        rows = by_member.get(member_id, [])
        core = [row for row in rows if row.event_kind != "commit_day"]
        def count(kind: str) -> int:
            return sum(1 for row in core if row.event_kind == kind)
        replies = [row for row in core if row.event_kind == "issue_replied"]
        merged = [row for row in core if row.event_kind == "pr_merged"]
        repos = {row.repo_node_id for row in core}
        owners = {row.owner_node_id for row in core}
        metrics = {
            "prs_opened": count("pr_opened"), "issues_opened": count("issue_opened"),
            "issue_replies_created": len(replies), "issues_replied_to": len({row.subject_node_id for row in replies}),
            "prs_reviewed": count("pr_reviewed"), "authored_prs_merged": len(merged),
            "repositories_touched": len(repos), "owners_touched": len(owners),
            "external_repositories_touched": len({row.repo_node_id for row in core if row.owner_login.lower() not in first_party_owners}),
            "repositories_accepting_prs": len({row.repo_node_id for row in merged}),
            "commit_contributions": sum(row.quantity for row in rows if row.event_kind == "commit_day") if (commit_available or {}).get(member_id, False) else None,
            "commit_days": len({(row.repo_node_id, row.contribution_day) for row in rows if row.event_kind == "commit_day"}) if (commit_available or {}).get(member_id, False) else None,
            "repositories_with_commits": len({row.repo_node_id for row in rows if row.event_kind == "commit_day"}) if (commit_available or {}).get(member_id, False) else None,
        }
        members.append({"member_id": member_id, "github_login": logins[member_id], "metrics": metrics})
    dimensions = ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged")
    team = {"by_dimension": {}}
    kind_for = {"prs_opened": "pr_opened", "issues_opened": "issue_opened", "issue_replies": "issue_replied", "prs_reviewed": "pr_reviewed", "authored_prs_merged": "pr_merged"}
    for dimension in dimensions:
        selected = [row for row in events if row.event_kind == kind_for[dimension]]
        subject = [row.subject_node_id for row in selected]
        team["by_dimension"][dimension] = {"event_count": len(selected), "member_artifact_interactions": len({(row.member_id, row.subject_node_id) for row in selected}), "unique_artifacts": len(set(subject))}
    return {"members": members, "team": team}
