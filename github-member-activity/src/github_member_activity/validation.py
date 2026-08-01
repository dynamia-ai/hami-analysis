from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .models import LedgerEvent

NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")


def validate_evidence_url(event: LedgerEvent, login: str) -> None:
    parts = urlsplit(event.evidence_url)
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.username or parts.port or parts.query == "" and event.event_kind == "commit_day":
        raise ValueError("invalid evidence URL")
    if any(part == "" or "/" in part or not NAME_RE.fullmatch(part) for part in event.repo_full_name.split("/")):
        raise ValueError("invalid repository name")
    if parts.path.split("/")[1:3] != event.repo_full_name.split("/"):
        raise ValueError("evidence repository mismatch")
    path = parts.path.split("/")
    if event.event_kind in {"pr_opened", "pr_reviewed", "pr_merged"}:
        if len(path) != 5 or path[3] != "pull" or not path[4].isdigit() or int(path[4]) <= 0 or parts.query or parts.fragment:
            raise ValueError("invalid pull request evidence URL")
    elif event.event_kind in {"issue_opened", "issue_replied"}:
        if len(path) != 5 or path[3] != "issues" or not path[4].isdigit() or int(path[4]) <= 0 or parts.query or parts.fragment:
            raise ValueError("invalid issue evidence URL")
    else:
        expected = f"/commits?author={login}&since={event.contribution_day}T00%3A00%3A00Z"
        if not parts.path.endswith("/commits") or parts.fragment or parse_qs(parts.query).get("author") != [login] or "since=" not in parts.query or "until=" not in parts.query:
            raise ValueError("invalid commit evidence URL")
