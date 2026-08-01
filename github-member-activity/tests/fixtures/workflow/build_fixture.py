from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from github_member_activity.canonical import canonical_json, sha256_bytes, sha256_json
from github_member_activity.manifest import (
    digest_file,
    ledger_text,
    source_status_object,
    source_summary,
    write_diagnostic,
    write_published,
)
from github_member_activity.metrics import aggregate
from github_member_activity.models import LedgerEvent, SourceStatus
from github_member_activity.renderers import render_csv, render_markdown, render_summary


RUN_ID = "20260804t000000z-00000000-0000-4000-8000-000000000000"
PERIOD = {
    "id": "weekly-20260727--20260803",
    "timezone": "Asia/Shanghai",
    "start_local": "2026-07-27T00:00:00+08:00",
    "end_local": "2026-08-03T00:00:00+08:00",
    "start_utc": "2026-07-26T16:00:00Z",
    "end_utc": "2026-08-02T16:00:00Z",
}
SOURCES = ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")


def _statuses(kind: str) -> dict:
    if kind == "no_applicable":
        rows = [SourceStatus("fixture-member", source, "optional" if source == "commit_context" else "core", "not_applicable", "member_window_empty") for source in SOURCES]
    elif kind == "validation_failed":
        rows = [SourceStatus("fixture-member", source, "optional" if source == "commit_context" else "core", "not_applicable", "member_window_empty") for source in SOURCES]
    else:
        rows = [SourceStatus("fixture-member", source, "optional" if source == "commit_context" else "core", "not_run", "run_aborted") for source in SOURCES]
    return source_status_object(rows)


def _diagnostic_manifest(kind: str) -> dict:
    statuses = _statuses(kind)
    return {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "run_status": "diagnostic",
        "publishable": False,
        "collector": {"version": "fixture", "git_commit": "0" * 40},
        "github_rest_api_version": "2026-03-10",
        "period": PERIOD,
        "observed_at": "2026-08-04T00:00:00Z",
        "publish_visibility_verified_at": None,
        "safe_resolved_config_sha256": None,
        "member_config_sha256": None,
        "repository_policy_summary": {"public_only": True, "first_party_owners": ["dynamia-ai"], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []},
        "source_status_summary": source_summary(statuses),
        "semantic_ledger_sha256": None,
        "run_reason": "validation_failed" if kind == "validation_failed" else "no_applicable_members" if kind == "no_applicable" else "run_aborted",
        "diagnostic_source_status": statuses,
        "artifacts": {key: {"present": False, "sha256": None} for key in ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")},
        "validator_result": {"status": "failed", "reason": "ledger_invalid"} if kind == "validation_failed" else {"status": "not_run", "reason": None},
    }


def _published() -> Path:
    observed = "2026-08-04T00:00:00Z"
    event = LedgerEvent(
        "fixture-member", "U_fixture", "pr_opened", "P_fixture", "P_fixture", "R_fixture", "dynamia-ai/fixture", "O_fixture", "dynamia-ai",
        "2026-07-28T12:00:00Z", None, 1, observed, observed,
        "search-prs_opened-20260726t160000z--20260802t160000z", "https://github.com/dynamia-ai/fixture/pull/1",
    )
    resolved = {"schema_version": "1.0", "timezone": "Asia/Shanghai", "members": [{"member_id": "fixture-member", "github_login": "fixture-login", "github_node_id": "U_fixture", "active_from": "2020-01-01", "active_until": None}], "repository_policy": {"public_only": True, "first_party_owners": ["dynamia-ai"], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []}}
    statuses = source_status_object([SourceStatus("fixture-member", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, observed) for source in SOURCES])
    summary = render_summary(run_id=RUN_ID, period=PERIOD, observed_at=observed, publishable=True, aggregate=aggregate([event], ["fixture-member"], {"fixture-member": "fixture-login"}, {"dynamia-ai"}, {"fixture-member": True}))
    files = {
        "resolved-config.json": (canonical_json(resolved) + "\n").encode(),
        "event-ledger.jsonl": ledger_text([event.to_dict()]).encode(),
        "source-status.json": (canonical_json(statuses) + "\n").encode(),
        "summary.json": (canonical_json(summary) + "\n").encode(),
        "summary.csv": render_csv(summary["members"]).encode(),
        "report.md": render_markdown(summary, statuses).encode(),
    }
    base = {
        "schema_version": "1.0", "run_id": RUN_ID, "collector": {"version": "fixture", "git_commit": "0" * 40},
        "github_rest_api_version": "2026-03-10", "period": PERIOD, "observed_at": observed,
        "publish_visibility_verified_at": observed, "safe_resolved_config_sha256": sha256_bytes(files["resolved-config.json"]),
        "member_config_sha256": sha256_json(resolved["members"]), "repository_policy_summary": resolved["repository_policy"],
        "source_status_summary": source_summary(statuses), "semantic_ledger_sha256": sha256_json([event.normalized_row_digest]), "diagnostic_source_status": None,
    }
    return write_published(Path("output"), PERIOD["id"], RUN_ID, files, base)


def _write_receipt(run_dir: Path, *, mode: str) -> None:
    receipt = Path(os.environ["RUNNER_TEMP"]) / "github-member-activity-receipt.json"
    value = {"schema_version": "1.0", "period_id": PERIOD["id"], "period_utc_slug": "20260726t160000z--20260802t160000z", "run_id": RUN_ID, "run_dir": str(run_dir.resolve().relative_to(Path.cwd().resolve())), "manifest_sha256": digest_file(run_dir / "run-manifest.json")}
    if mode == "malformed_receipt":
        receipt.write_text("not-json\n", encoding="utf-8")
        return
    if mode == "receipt_pretty":
        import json
        receipt.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return
    if mode == "receipt_duplicate":
        receipt.write_text('{"schema_version":"1.0","schema_version":"1.0"}\n', encoding="utf-8")
        return
    if mode == "receipt_missing":
        value.pop("run_id")
    elif mode == "receipt_extra":
        value["extra"] = "reject"
    elif mode == "receipt_wrong_type":
        value["manifest_sha256"] = 7
    elif mode == "receipt_wrong_hash":
        value["manifest_sha256"] = "0" * 64
    elif mode == "receipt_wrong_period":
        value["period_id"] = "monthly-20260701--20260801"
    elif mode == "receipt_wrong_run":
        value["run_id"] = "20260804t000000z-00000000-0000-4000-8000-000000000001"
    elif mode == "receipt_wrong_slug":
        value["period_utc_slug"] = "wrong"
    elif mode == "receipt_absolute_path":
        value["run_dir"] = str(run_dir.resolve())
    elif mode == "receipt_dotdot":
        value["run_dir"] = "output/../output/weekly-20260727--20260803/" + RUN_ID
    elif mode == "wrong_path":
        value["run_dir"] = "output/weekly-20260727--20260803/other"
    receipt.write_text(canonical_json(value) + "\n", encoding="utf-8")


def build(mode: str) -> None:
    if mode == "collector_2":
        return
    if mode in {"published", "wrong_path_valid", "status_swap"}:
        run_dir = _published()
        if mode == "wrong_path_valid":
            bad = Path("diagnostics") / RUN_ID
            shutil.copytree(run_dir, bad)
            run_dir = bad
        elif mode == "status_swap":
            diagnostic = Path("diagnostics") / RUN_ID
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(run_dir, diagnostic)
            swapped = Path("output") / PERIOD["id"] / RUN_ID
            shutil.rmtree(swapped)
            shutil.copytree(diagnostic, swapped)
            run_dir = swapped
    else:
        kind = "no_applicable" if mode in {"diagnostic_success", "verify_fail", "malformed_receipt", "wrong_path", "receipt_pretty", "receipt_duplicate", "receipt_missing", "receipt_extra", "receipt_wrong_type", "receipt_wrong_hash", "receipt_wrong_period", "receipt_wrong_run", "receipt_wrong_slug", "receipt_absolute_path", "receipt_dotdot"} else "validation_failed" if mode == "validation_failed" else "run_aborted"
        run_dir = write_diagnostic(Path("diagnostics"), _diagnostic_manifest(kind)).resolve()
    if mode not in {"collector_2"}:
        _write_receipt(run_dir, mode=mode)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "diagnostic_success")
