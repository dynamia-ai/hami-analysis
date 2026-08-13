from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlsplit

from .models import LedgerEvent

LOGIN_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,39}(?<!-)$")
NUMBER_RE = re.compile(r"^[1-9][0-9]{0,9}$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def validate_evidence_url(event: LedgerEvent, login: str) -> None:
    parts = urlsplit(event.evidence_url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid evidence URL") from exc
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.username or port:
        raise ValueError("invalid evidence URL")
    repo_parts = event.repo_full_name.split("/")
    if len(repo_parts) != 2 or not LOGIN_RE.fullmatch(repo_parts[0]) or not REPO_RE.fullmatch(repo_parts[1]):
        raise ValueError("invalid repository name")
    if parts.path.split("/")[1:3] != event.repo_full_name.split("/"):
        raise ValueError("evidence repository mismatch")
    path = parts.path.split("/")
    if event.event_kind in {"pr_opened", "pr_reviewed", "pr_merged"}:
        if len(path) != 5 or path[3] != "pull" or not NUMBER_RE.fullmatch(path[4]) or parts.query or parts.fragment:
            raise ValueError("invalid pull request evidence URL")
    elif event.event_kind in {"issue_opened", "issue_replied"}:
        if len(path) != 5 or path[3] != "issues" or not NUMBER_RE.fullmatch(path[4]) or parts.query or parts.fragment:
            raise ValueError("invalid issue evidence URL")
    else:
        if not LOGIN_RE.fullmatch(login):
            raise ValueError("invalid GitHub login")
        if event.contribution_day is None:
            raise ValueError("invalid commit evidence URL")
        next_day = (date.fromisoformat(event.contribution_day) + timedelta(days=1)).isoformat()
        expected_query = f"author={login}&since={event.contribution_day}T00%3A00%3A00Z&until={next_day}T00%3A00%3A00Z"
        if parts.path != f"/{event.repo_full_name}/commits" or parts.fragment or parts.query != expected_query:
            raise ValueError("invalid commit evidence URL")
