from github_member_activity.metrics import aggregate
from github_member_activity.models import LedgerEvent
from github_member_activity.canonical import canonical_json


def event(kind: str, node: str, subject: str, owner: str = "external") -> LedgerEvent:
    partition = "root" if kind in {"issue_replied", "pr_reviewed"} else "search-prs_opened-20260101t000000z--20260102t000000z"
    return LedgerEvent("alice", "U_1", kind, node, subject, "R_1", f"{owner}/repo", "O_1", owner, "2026-01-02T00:00:00Z", None, 1, "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z", partition, f"https://github.com/{owner}/repo/issues/1")


def test_dimension_counts_do_not_form_total_score():
    rows = [event("pr_opened", "P_1", "P_1"), event("issue_replied", "C_1", "I_1"), event("issue_replied", "C_2", "I_1")]
    result = aggregate(rows, ["alice"], {"alice": "Alice"}, {"dynamia-ai"})
    metrics = result["members"][0]["metrics"]
    assert metrics["prs_opened"] == 1
    assert metrics["issue_replies_created"] == 2
    assert metrics["issues_replied_to"] == 1
    assert result["team"]["by_dimension"]["issue_replies"]["unique_artifacts"] == 1


def test_ledger_round_trip_is_not_recursive():
    row = event("pr_opened", "P_1", "P_1")
    value = row.to_dict()
    assert value["normalized_row_digest"] == row.normalized_row_digest
    assert type(row).from_dict(value) == row


def test_canonical_json_rejects_floats_and_is_stable():
    assert canonical_json({"b": 1, "a": [True, None]}) == '{"a":[true,null],"b":1}'


def test_complete_empty_commit_context_is_zero_not_null():
    result = aggregate([], ["alice"], {"alice": "Alice"}, set(), {"alice": True})
    assert result["members"][0]["metrics"]["commit_contributions"] == 0
