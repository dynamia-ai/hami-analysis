import json

from github_member_activity.manifest import ARTIFACT_FILES, ledger_text, source_status_object, source_summary, verify_directory, write_published, write_diagnostic
from github_member_activity.metrics import aggregate
from github_member_activity.models import LedgerEvent, SourceStatus
from github_member_activity.renderers import render_csv, render_markdown, render_summary
from github_member_activity.canonical import sha256_bytes, sha256_json


def test_diagnostic_manifest_has_exact_shape(tmp_path):
    statuses = source_status_object([SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "not_applicable", "member_window_empty") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")])
    manifest = {
        "schema_version": "1.0", "run_id": "20260801t000000z-00000000-0000-4000-8000-000000000000",
        "run_status": "diagnostic", "publishable": False, "collector": {"version": "1.0.0", "git_commit": "x"}, "github_rest_api_version": "2026-03-10", "period": {}, "observed_at": "2026-01-01T00:00:00Z", "publish_visibility_verified_at": None, "safe_resolved_config_sha256": None, "member_config_sha256": None, "repository_policy_summary": {"public_only": True, "first_party_owners": [], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []}, "source_status_summary": {"core_complete": False, "optional_complete": False, "noncomplete": []}, "semantic_ledger_sha256": None, "run_reason": "no_applicable_members", "diagnostic_source_status": statuses, "validator_result": {"status": "not_run", "reason": None},
        "artifacts": {key: {"present": False, "sha256": None} for key in ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")},
    }
    path = write_diagnostic(tmp_path / "diagnostics", manifest)
    loaded, code = verify_directory(path)
    assert loaded["run_id"] == manifest["run_id"]
    assert code == 3


def test_published_run_replays_and_rejects_tampering(tmp_path):
    statuses = source_status_object([SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2026-01-03T00:00:00Z") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")])
    event = LedgerEvent("alice", "U_1", "pr_opened", "P_1", "P_1", "R_1", "dynamia-ai/repo", "O_1", "dynamia-ai", "2026-01-01T12:00:00Z", None, 1, "2026-01-04T00:00:00Z", "2026-01-03T00:00:00Z", "search-prs_opened-20260101t000000z--20260102t000000z", "https://github.com/dynamia-ai/repo/pull/1")
    resolved = {"schema_version": "1.0", "timezone": "UTC", "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": "2020-01-01", "active_until": None}], "repository_policy": {"public_only": True, "first_party_owners": ["dynamia-ai"], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []}}
    period = {"id": "explicit-20260101t000000z--20260102t000000z", "timezone": "UTC", "start_local": "2026-01-01T00:00:00+00:00", "end_local": "2026-01-02T00:00:00+00:00", "start_utc": "2026-01-01T00:00:00Z", "end_utc": "2026-01-02T00:00:00Z"}
    summary = render_summary(run_id="20260102t000000z-00000000-0000-4000-8000-000000000000", period=period, observed_at="2026-01-02T00:00:00Z", publishable=True, aggregate=aggregate([event], ["alice"], {"alice": "Alice"}, {"dynamia-ai"}, {"alice": True}))
    files = {"resolved-config.json": (json.dumps(resolved, sort_keys=True, separators=(",", ":")) + "\n").encode(), "event-ledger.jsonl": ledger_text([event.to_dict()]).encode(), "source-status.json": (json.dumps(statuses, sort_keys=True, separators=(",", ":")) + "\n").encode(), "summary.json": (json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n").encode(), "summary.csv": render_csv(summary["members"]).encode(), "report.md": render_markdown(summary, statuses).encode()}
    manifest_base = {"schema_version": "1.0", "run_id": summary["run_id"], "collector": {"version": "1.0.0", "git_commit": "x"}, "github_rest_api_version": "2026-03-10", "period": period, "observed_at": "2026-01-02T00:00:00Z", "publish_visibility_verified_at": "2026-01-04T00:00:00Z", "safe_resolved_config_sha256": sha256_bytes(files["resolved-config.json"]), "member_config_sha256": sha256_json(resolved["members"]), "repository_policy_summary": resolved["repository_policy"], "source_status_summary": source_summary(statuses), "semantic_ledger_sha256": sha256_json(sorted([event.normalized_row_digest])), "diagnostic_source_status": None}
    path = write_published(tmp_path / "output", period["id"], summary["run_id"], files, manifest_base)
    _, code = verify_directory(path)
    assert code == 0
    (path / "summary.json").write_text("not-json", encoding="utf-8")
    try:
        verify_directory(path)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered artifact was accepted")
