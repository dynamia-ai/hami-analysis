from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .github_client import GitHubClient
from .models import LedgerEvent, SourceStatus
from .period import ReportPeriod, effective_window, format_z


@dataclass(slots=True)
class CollectionResult:
    events: list[LedgerEvent]
    statuses: list[SourceStatus]
    applied_owner_ids: list[str]
    applied_repo_ids: list[str]


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
    """Collection boundary. Source adapters intentionally return no guessed zeros.

    The transport and artifact layers are usable independently for deterministic fixtures;
    live source adapters should only append events after their complete two-pass contracts.
    """
    statuses = empty_statuses(config, period, observed_at=observed_at)
    return CollectionResult([], statuses, [], [])
