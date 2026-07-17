from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
KNOWN_BOTS = frozenset({
    "dependabot",
    "github-actions",
    "renovate",
    "codecov",
    "mergify",
})


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def actor_login(user: dict[str, Any] | None) -> str:
    return str((user or {}).get("login") or "unknown")


def is_bot(user: dict[str, Any] | None) -> bool:
    user = user or {}
    login = str(user.get("login") or "").lower()
    base_login = login.removesuffix("[bot]")
    return user.get("type") == "Bot" or login.endswith("[bot]") or base_login in KNOWN_BOTS


def is_maintainer(association: str | None) -> bool:
    return (association or "").upper() in MAINTAINER_ASSOCIATIONS


@dataclass(slots=True)
class Activity:
    kind: Literal["comment", "review", "review_comment"]
    author: str
    author_association: str | None
    body: str
    occurred_at: datetime | None
    updated_at: datetime | None = None
    url: str | None = None
    bot: bool = False
    in_period: bool = False
    updated_in_period: bool = False
    state: str | None = None
    path: str | None = None
    line: int | None = None
    original_line: int | None = None
    side: str | None = None

    @property
    def maintainer(self) -> bool:
        return is_maintainer(self.author_association)

    @property
    def active_in_period(self) -> bool:
        return self.in_period or self.updated_in_period


@dataclass(slots=True)
class IssueEvidence:
    repository: str
    number: int
    title: str
    url: str
    state: str
    state_reason: str | None
    author: str
    author_association: str | None
    created_at: datetime | None
    updated_at: datetime | None
    closed_at: datetime | None
    labels: list[str]
    assignees: list[str]
    milestone: str | None
    body: str
    comments: list[Activity]
    created_in_period: bool
    closed_in_period: bool
    exact_activity_unknown: bool
    data_gaps: list[str] = field(default_factory=list)

    @property
    def item_id(self) -> str:
        return f"{self.repository}#{self.number}"

    @property
    def period_comments(self) -> list[Activity]:
        return [item for item in self.comments if item.in_period]

    @property
    def period_human_activity_count(self) -> int:
        return sum(not item.bot and item.active_in_period for item in self.comments)


@dataclass(slots=True)
class PullRequestEvidence:
    repository: str
    number: int
    title: str
    url: str
    state: str
    draft: bool
    merged: bool
    merged_at: datetime | None
    closed_at: datetime | None
    author: str
    author_association: str | None
    created_at: datetime | None
    updated_at: datetime | None
    base_branch: str | None
    head_branch: str | None
    labels: list[str]
    assignees: list[str]
    requested_reviewers: list[str]
    milestone: str | None
    body: str
    mergeable: bool | None
    additions: int | None
    deletions: int | None
    changed_files: int | None
    conversation_comments: list[Activity]
    reviews: list[Activity]
    review_comments: list[Activity]
    created_in_period: bool
    merged_in_period: bool
    closed_unmerged_in_period: bool
    exact_activity_unknown: bool
    data_gaps: list[str] = field(default_factory=list)

    @property
    def item_id(self) -> str:
        return f"{self.repository}#{self.number}"

    @property
    def all_activity(self) -> list[Activity]:
        return self.conversation_comments + self.reviews + self.review_comments

    @property
    def period_human_activity_count(self) -> int:
        return sum(not item.bot and item.active_in_period for item in self.all_activity)


@dataclass(slots=True)
class CollectionWarning:
    scope: str
    message: str
    url: str | None = None


@dataclass(slots=True)
class CollectionResult:
    issues: list[IssueEvidence] = field(default_factory=list)
    pull_requests: list[PullRequestEvidence] = field(default_factory=list)
    warnings: list[CollectionWarning] = field(default_factory=list)
    failed_requests: int = 0
    rate_limit_remaining: int | None = None
    unexplained_updated_excluded_count: int = 0
    activity_failure_excluded_count: int = 0
