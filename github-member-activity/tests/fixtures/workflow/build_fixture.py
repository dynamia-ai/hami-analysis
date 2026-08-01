from __future__ import annotations

import os
import sys
from pathlib import Path

from github_member_activity.canonical import canonical_json
from github_member_activity.manifest import digest_file, source_status_object, source_summary, write_diagnostic
from github_member_activity.models import SourceStatus


def build(mode: str) -> None:
    run_id = "20260801t000000z-00000000-0000-4000-8000-000000000000"
    period = {
        "id": "weekly-20260727--20260803",
        "timezone": "Asia/Shanghai",
        "start_local": "2026-07-27T00:00:00+08:00",
        "end_local": "2026-08-03T00:00:00+08:00",
        "start_utc": "2026-07-26T16:00:00Z",
        "end_utc": "2026-08-02T16:00:00Z",
    }
    statuses = source_status_object([
        SourceStatus("fixture-member", source, "optional" if source == "commit_context" else "core", "not_applicable", "member_window_empty")
        for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")
    ])
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "run_status": "diagnostic",
        "publishable": False,
        "collector": {"version": "fixture", "git_commit": "0" * 40},
        "github_rest_api_version": "2026-03-10",
        "period": period,
        "observed_at": "2026-08-01T00:00:00Z",
        "publish_visibility_verified_at": None,
        "safe_resolved_config_sha256": None,
        "member_config_sha256": None,
        "repository_policy_summary": {"public_only": True, "first_party_owners": ["dynamia-ai"], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []},
        "source_status_summary": source_summary(statuses),
        "semantic_ledger_sha256": None,
        "run_reason": "no_applicable_members",
        "diagnostic_source_status": statuses,
        "artifacts": {key: {"present": False, "sha256": None} for key in ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")},
        "validator_result": {"status": "not_run", "reason": None},
    }
    run_dir = write_diagnostic(Path("diagnostics"), manifest).resolve()
    receipt = Path(os.environ["RUNNER_TEMP"]) / "github-member-activity-receipt.json"
    value = {"schema_version": "1.0", "period_id": period["id"], "period_utc_slug": "20260726t160000z--20260802t160000z", "run_id": run_id, "run_dir": str(run_dir.relative_to(Path.cwd())), "manifest_sha256": digest_file(run_dir / "run-manifest.json")}
    if mode == "malformed_receipt":
        receipt.write_text("not-json\n", encoding="utf-8")
    elif mode == "wrong_path":
        value["run_dir"] = "output/weekly-20260727--20260803/other"
        receipt.write_text(canonical_json(value) + "\n", encoding="utf-8")
    else:
        receipt.write_text(canonical_json(value) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "success")
