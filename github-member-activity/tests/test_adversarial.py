import pytest

from github_member_activity.github_client import GitHubClient, GitHubRequestError
from github_member_activity.manifest import _validate_diagnostic_state, _validate_status, write_diagnostic
from github_member_activity.models import LedgerEvent, SourceStatus


def _statuses(*, failed_source: str | None = None, failed_reason: str | None = None):
    sources = ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")
    rows = []
    for source in sources:
        optional = source == "commit_context"
        rows.append(SourceStatus("alice", source, "optional" if optional else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2026-01-02T00:00:00Z"))
    if failed_source:
        index = sources.index(failed_source)
        rows[index] = SourceStatus("alice", failed_source, "optional" if failed_source == "commit_context" else "core", "partial", failed_reason, False, None if failed_source in {"issue_replies", "prs_reviewed"} else False, False, None, None)
    return {"schema_version": "1.0", "rows": [row.to_dict() for row in rows]}


def test_graphql_connection_rejects_boolean_total_count():
    client = object.__new__(GitHubClient)
    client.graphql = lambda query, variables: {"user": {"comments": {"totalCount": True, "edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
    with pytest.raises(GitHubRequestError, match="api_contract_violation"):
        client.connection("query", {}, ("user", "comments"))


def test_status_matrix_rejects_identity_partial_and_not_run_proofs():
    legal = _statuses(failed_source="prs_opened", failed_reason="search_capped")
    _validate_status(legal)
    illegal = _statuses(failed_source="prs_opened", failed_reason="search_capped")
    illegal["rows"][0]["pagination_complete"] = False
    illegal["rows"][0]["partition_complete"] = True
    with pytest.raises(ValueError, match="source_status_invalid"):
        _validate_status(illegal)
    identity = _statuses(failed_source="prs_opened", failed_reason="identity_resolution_failed")
    with pytest.raises(ValueError, match="source_status_invalid"):
        _validate_status(identity)
    not_run = _statuses(failed_source="prs_opened", failed_reason="search_capped")
    not_run["rows"][0]["status"] = "not_run"
    not_run["rows"][0]["reason"] = "run_aborted"
    not_run["rows"][0]["pagination_complete"] = False
    with pytest.raises(ValueError, match="source_status_invalid"):
        _validate_status(not_run)


def test_status_matrix_accepts_snapshot_complete_partial_with_visibility_gap():
    legal = _statuses(failed_source="prs_opened", failed_reason="search_capped")
    legal["rows"][0].update({
        "pagination_complete": True,
        "partition_complete": True,
        "snapshot_complete": True,
        "visibility_complete": False,
        "snapshot_completed_at": "2026-01-02T00:00:00Z",
    })
    _validate_status(legal)

    failed = {**legal, "rows": [dict(row) for row in legal["rows"]]}
    failed["rows"][0]["status"] = "failed"
    failed["rows"][0]["reason"] = "api_contract_violation"
    _validate_status(failed)

    illegal_partition = {**legal, "rows": [dict(row) for row in legal["rows"]]}
    illegal_partition["rows"][0]["partition_complete"] = False
    with pytest.raises(ValueError, match="source_status_invalid"):
        _validate_status(illegal_partition)


def test_commit_partial_reason_is_canonical_and_member_window_is_six_source_consistent():
    illegal = _statuses(failed_source="commit_context", failed_reason="pagination_incomplete")
    with pytest.raises(ValueError, match="source_status_invalid"):
        _validate_status(illegal)
    identity = _statuses(failed_source="commit_context", failed_reason="identity_resolution_failed")
    identity["rows"][-1]["status"] = "failed"
    identity["rows"][-1].update({"pagination_complete": None, "partition_complete": None, "snapshot_complete": None, "visibility_complete": None, "snapshot_completed_at": None})
    _validate_status(identity)

    valid_member = _statuses()
    commit_row = valid_member["rows"][-1]
    commit_row.update({"status": "not_applicable", "reason": "member_window_empty", "pagination_complete": None, "partition_complete": None, "snapshot_complete": None, "visibility_complete": None, "snapshot_completed_at": None})
    with pytest.raises(ValueError, match="diagnostic_state_invalid"):
        _validate_diagnostic_state({"observed_at": "2026-01-02T00:00:00Z", "run_reason": "core_source_incomplete", "diagnostic_source_status": valid_member})

    stale_snapshot = _statuses()
    stale_snapshot["rows"][0]["snapshot_completed_at"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="diagnostic_state_invalid"):
        _validate_diagnostic_state({"observed_at": "2026-01-02T00:00:00Z", "run_reason": "core_source_incomplete", "diagnostic_source_status": stale_snapshot})


def test_ledger_model_rejects_ordinary_quantity_and_wrong_partition():
    kwargs = dict(member_id="alice", actor_node_id="U1", event_kind="pr_opened", event_node_id="P1", subject_node_id="P1", repo_node_id="R1", repo_full_name="owner/repo", owner_node_id="O1", owner_login="owner", occurred_at="2026-01-01T00:00:00Z", contribution_day=None, quantity=2, visibility_verified_at="2026-01-02T00:00:00Z", collected_at="2026-01-02T00:00:00Z", query_partition="root", evidence_url="https://github.com/owner/repo/pull/1")
    with pytest.raises(ValueError):
        LedgerEvent(**kwargs)
    with pytest.raises(ValueError):
        LedgerEvent(**{**kwargs, "quantity": 1, "query_partition": "root"})
    with pytest.raises(ValueError):
        LedgerEvent(**{**kwargs, "quantity": 1, "query_partition": "search-prs_opened-20260101t000000z--20260102t000000z", "occurred_at": "2026-01-01T00:00:00+08:00"})


def test_diagnostic_publish_is_immutable(tmp_path):
    statuses = {"schema_version": "1.0", "rows": [row.to_dict() for row in [SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "not_applicable", "member_window_empty") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")]]}
    manifest = {
        "schema_version": "1.0", "run_id": "20260101t000000z-00000000-0000-4000-8000-000000000000", "run_status": "diagnostic", "publishable": False,
        "collector": {"version": "1.0.0", "git_commit": "0" * 40}, "github_rest_api_version": "2026-03-10",
        "period": {"id": "explicit-20260101t000000z--20260102t000000z", "timezone": "UTC", "start_local": "2026-01-01T00:00:00+00:00", "end_local": "2026-01-02T00:00:00+00:00", "start_utc": "2026-01-01T00:00:00Z", "end_utc": "2026-01-02T00:00:00Z"},
        "observed_at": "2026-01-01T00:00:00Z", "publish_visibility_verified_at": None, "safe_resolved_config_sha256": None, "member_config_sha256": None,
        "repository_policy_summary": {"public_only": True, "first_party_owners": [], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []}, "source_status_summary": {"core_complete": False, "optional_complete": False, "noncomplete": []}, "semantic_ledger_sha256": None, "run_reason": "no_applicable_members", "diagnostic_source_status": statuses,
        "artifacts": {key: {"present": False, "sha256": None} for key in ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")}, "validator_result": {"status": "not_run", "reason": None},
    }
    root = tmp_path / "diagnostics"
    write_diagnostic(root, manifest)
    with pytest.raises(FileExistsError):
        write_diagnostic(root, manifest)
