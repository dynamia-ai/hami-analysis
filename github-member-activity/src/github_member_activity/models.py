from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any

from .canonical import sha256_json
from .period import parse_rfc3339

SCHEMA_VERSION = "1.0"
EVENT_KINDS = ("pr_opened", "issue_opened", "issue_replied", "pr_reviewed", "pr_merged", "commit_day")
SOURCE_ORDER = ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")
CORE_SOURCES = frozenset(SOURCE_ORDER[:-1])
EVENT_SOURCE = {
    "pr_opened": "prs_opened",
    "issue_opened": "issues_opened",
    "issue_replied": "issue_replies",
    "pr_reviewed": "prs_reviewed",
    "pr_merged": "authored_prs_merged",
    "commit_day": "commit_context",
}

LEDGER_FIELDS = (
    "schema_version", "member_id", "actor_node_id", "event_kind", "event_key", "event_node_id", "subject_node_id",
    "repo_node_id", "repo_full_name", "owner_node_id", "owner_login", "occurred_at", "contribution_day", "quantity",
    "visibility_verified_at", "collected_at", "source", "query_partition", "evidence_url", "normalized_row_digest",
)


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    member_id: str
    actor_node_id: str
    event_kind: str
    event_node_id: str | None
    subject_node_id: str
    repo_node_id: str
    repo_full_name: str
    owner_node_id: str
    owner_login: str
    occurred_at: str | None
    contribution_day: str | None
    quantity: int
    visibility_verified_at: str
    collected_at: str
    query_partition: str
    evidence_url: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in (self.member_id, self.actor_node_id, self.subject_node_id, self.repo_node_id, self.repo_full_name, self.owner_node_id, self.owner_login, self.visibility_verified_at, self.collected_at, self.query_partition, self.evidence_url)):
            raise ValueError("ledger identifiers must be non-empty strings")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError("quantity must be an integer")
        if self.event_kind not in EVENT_KINDS or EVENT_SOURCE[self.event_kind] not in SOURCE_ORDER:
            raise ValueError("invalid event kind")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.event_kind != "commit_day" and (not self.event_node_id or not self.occurred_at or self.contribution_day is not None):
            raise ValueError("invalid ordinary event shape")
        if self.event_kind == "commit_day" and (self.event_node_id is not None or self.occurred_at is not None or not self.contribution_day):
            raise ValueError("invalid commit event shape")
        if not re.fullmatch(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", self.repo_full_name) or self.owner_login != self.repo_full_name.split("/", 1)[0].lower():
            raise ValueError("invalid repository identity")
        parse_rfc3339(self.visibility_verified_at)
        parse_rfc3339(self.collected_at)
        if self.occurred_at is not None:
            parse_rfc3339(self.occurred_at)
        if self.contribution_day is not None:
            date.fromisoformat(self.contribution_day)
        if self.event_kind in {"pr_opened", "issue_opened", "pr_merged"} and self.subject_node_id != self.event_node_id:
            raise ValueError("event subject mismatch")
        if self.event_kind == "commit_day" and self.subject_node_id != self.repo_node_id:
            raise ValueError("commit subject mismatch")
        if not re.fullmatch(r"(?:root|search-(?:prs_opened|issues_opened|authored_prs_merged)-[0-9]{8}t[0-9]{6}z--[0-9]{8}t[0-9]{6}z|commit-root-[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{4}-[0-9]{2}-[0-9]{2})", self.query_partition):
            raise ValueError("invalid query partition")

    @property
    def source(self) -> str:
        return EVENT_SOURCE[self.event_kind]

    @property
    def event_key(self) -> str:
        if self.event_kind == "commit_day":
            return sha256_json(["commit_context", self.member_id, self.repo_node_id, self.contribution_day])
        return sha256_json([self.event_kind, self.event_node_id])

    @property
    def normalized_row_digest(self) -> str:
        return sha256_json({key: self._base_dict()[key] for key in (
            "schema_version", "member_id", "actor_node_id", "event_kind", "event_key", "event_node_id", "subject_node_id",
            "repo_node_id", "repo_full_name", "owner_node_id", "owner_login", "occurred_at", "contribution_day", "quantity",
            "source", "evidence_url",
        )})

    def _base_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "member_id": self.member_id, "actor_node_id": self.actor_node_id,
            "event_kind": self.event_kind, "event_key": self.event_key, "event_node_id": self.event_node_id,
            "subject_node_id": self.subject_node_id, "repo_node_id": self.repo_node_id, "repo_full_name": self.repo_full_name,
            "owner_node_id": self.owner_node_id, "owner_login": self.owner_login, "occurred_at": self.occurred_at,
            "contribution_day": self.contribution_day, "quantity": self.quantity,
            "source": self.source, "evidence_url": self.evidence_url,
        }

    def to_dict(self) -> dict[str, Any]:
        value = self._base_dict()
        value.update({"visibility_verified_at": self.visibility_verified_at, "collected_at": self.collected_at, "query_partition": self.query_partition, "normalized_row_digest": self.normalized_row_digest})
        return {key: value[key] for key in LEDGER_FIELDS}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LedgerEvent":
        if set(value) != set(LEDGER_FIELDS):
            raise ValueError("ledger schema mismatch")
        event = cls(
            member_id=value["member_id"], actor_node_id=value["actor_node_id"], event_kind=value["event_kind"],
            event_node_id=value["event_node_id"], subject_node_id=value["subject_node_id"], repo_node_id=value["repo_node_id"],
            repo_full_name=value["repo_full_name"], owner_node_id=value["owner_node_id"], owner_login=value["owner_login"],
            occurred_at=value["occurred_at"], contribution_day=value["contribution_day"], quantity=value["quantity"],
            visibility_verified_at=value["visibility_verified_at"], collected_at=value["collected_at"],
            query_partition=value["query_partition"], evidence_url=value["evidence_url"],
        )
        if value["schema_version"] != SCHEMA_VERSION or value["source"] != event.source or value["event_key"] != event.event_key or value["normalized_row_digest"] != event.normalized_row_digest:
            raise ValueError("ledger digest or source mismatch")
        return event


@dataclass(frozen=True, slots=True)
class SourceStatus:
    member_id: str
    source: str
    criticality: str
    status: str
    reason: str | None = None
    pagination_complete: bool | None = None
    partition_complete: bool | None = None
    snapshot_complete: bool | None = None
    visibility_complete: bool | None = None
    snapshot_completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__ if hasattr(self, "__dict__") else {
            "member_id": self.member_id, "source": self.source, "criticality": self.criticality,
            "status": self.status, "reason": self.reason, "pagination_complete": self.pagination_complete,
            "partition_complete": self.partition_complete, "snapshot_complete": self.snapshot_complete,
            "visibility_complete": self.visibility_complete, "snapshot_completed_at": self.snapshot_completed_at,
        }
