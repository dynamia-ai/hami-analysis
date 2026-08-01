from github_member_activity.metrics import aggregate
from github_member_activity.models import LedgerEvent


def event(kind: str, node: str, subject: str, owner: str = "external") -> LedgerEvent:
    return LedgerEvent("alice", "U_1", kind, node, subject, "R_1", f"{owner}/repo", "O_1", owner, "2026-01-02T00:00:00Z", None, 1, "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z", "root", f"https://github.com/{owner}/repo/issues/1")


def test_dimension_counts_do_not_form_total_score():
    rows = [event("pr_opened", "P_1", "P_1"), event("issue_replied", "C_1", "I_1"), event("issue_replied", "C_2", "I_1")]
    result = aggregate(rows, ["alice"], {"alice": "Alice"}, {"dynamia-ai"})
    metrics = result["members"][0]["metrics"]
    assert metrics["prs_opened"] == 1
    assert metrics["issue_replies_created"] == 2
    assert metrics["issues_replied_to"] == 1
    assert result["team"]["by_dimension"]["issue_replies"]["unique_artifacts"] == 1
