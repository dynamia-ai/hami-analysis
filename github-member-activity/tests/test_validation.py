from dataclasses import replace

import pytest

from github_member_activity.models import LedgerEvent
from github_member_activity.validation import validate_evidence_url


def event(kind: str) -> LedgerEvent:
    is_pull = kind in {"pr_opened", "pr_reviewed", "pr_merged"}
    path = "pull" if is_pull else "issues"
    partition = "root" if kind in {"pr_reviewed", "issue_replied"} else f"search-{'prs_opened' if kind == 'pr_opened' else 'issues_opened' if kind == 'issue_opened' else 'authored_prs_merged'}-20260101t000000z--20260102t000000z"
    return LedgerEvent(
        "alice", "U_1", kind, "E_1", "S_1" if kind in {"pr_reviewed", "issue_replied"} else "E_1",
        "R_1", "owner/repo", "O_1", "owner", "2026-01-01T12:00:00Z", None, 1,
        "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z", partition,
        f"https://github.com/owner/repo/{path}/1",
    )


@pytest.mark.parametrize("kind", ["pr_opened", "issue_opened"])
@pytest.mark.parametrize("number", ["0", "007", "12345678901", "\u0667"])
def test_evidence_url_rejects_noncanonical_numbers(kind: str, number: str):
    value = event(kind)
    invalid = replace(value, evidence_url=value.evidence_url.rsplit("/", 1)[0] + f"/{number}")
    with pytest.raises(ValueError):
        validate_evidence_url(invalid, "Alice")


def test_evidence_url_rejects_invalid_local_login():
    value = LedgerEvent(
        "alice", "U_1", "commit_day", None, "R_1", "R_1", "owner/repo", "O_1", "owner",
        None, "2026-01-01", 1, "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z",
        "commit-root-2026-01-01--2026-01-02",
        "https://github.com/owner/repo/commits?author=alice&x=y&since=2026-01-01T00%3A00%3A00Z&until=2026-01-02T00%3A00%3A00Z",
    )
    with pytest.raises(ValueError, match="invalid GitHub login"):
        validate_evidence_url(value, "alice&x=y")
