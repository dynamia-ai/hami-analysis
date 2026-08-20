import json
import os
from pathlib import Path
import pytest
import subprocess
import sys

from github_member_activity.manifest import ARTIFACT_FILES, ledger_text, source_status_object, source_summary, verify_directory, write_published, write_diagnostic
from github_member_activity.metrics import aggregate
from github_member_activity.models import LedgerEvent, SourceStatus
from github_member_activity.renderers import render_csv, render_markdown, render_summary
from github_member_activity.canonical import canonical_json, sha256_bytes, sha256_json


def _committed_published_fixture(tmp_path):
    root = Path(__file__).parents[1]
    fixture = root / "tests" / "fixtures" / "workflow" / "build_fixture.py"
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "RUNNER_TEMP": str(tmp_path / "runner")}
    Path(env["RUNNER_TEMP"]).mkdir(parents=True)
    subprocess.run([sys.executable, str(fixture), "published"], cwd=tmp_path, env=env, check=True)
    return tmp_path / "output" / "weekly-20260727--20260803" / "20260804t000000z-00000000-0000-4000-8000-000000000000"


def _committed_diagnostic_fixture(tmp_path):
    root = Path(__file__).parents[1]
    fixture = root / "tests" / "fixtures" / "workflow" / "build_fixture.py"
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "RUNNER_TEMP": str(tmp_path / "runner")}
    Path(env["RUNNER_TEMP"]).mkdir(parents=True)
    subprocess.run([sys.executable, str(fixture), "diagnostic_success"], cwd=tmp_path, env=env, check=True)
    return tmp_path / "diagnostics" / "20260804t000000z-00000000-0000-4000-8000-000000000000"


@pytest.mark.parametrize(
    ("run_reason", "validator_result", "expected_code"),
    [
        ("no_applicable_members", {"status": "not_run", "reason": None}, 3),
        ("validation_failed", {"status": "failed", "reason": "ledger_invalid"}, 3),
    ],
)
def test_diagnostic_manifest_has_exact_shape(tmp_path, run_reason, validator_result, expected_code):
    statuses = source_status_object([SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "not_applicable", "member_window_empty") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")])
    manifest = {
        "schema_version": "1.0", "run_id": "20260101t000000z-00000000-0000-4000-8000-000000000000",
        "run_status": "diagnostic", "publishable": False, "collector": {"version": "1.0.0", "git_commit": "0000000000000000000000000000000000000000"}, "github_rest_api_version": "2026-03-10", "period": {"id": "explicit-20260101t000000z--20260102t000000z", "timezone": "UTC", "start_local": "2026-01-01T00:00:00+00:00", "end_local": "2026-01-02T00:00:00+00:00", "start_utc": "2026-01-01T00:00:00Z", "end_utc": "2026-01-02T00:00:00Z"}, "observed_at": "2026-01-01T00:00:00Z", "publish_visibility_verified_at": None, "safe_resolved_config_sha256": None, "member_config_sha256": None, "repository_policy_summary": {"public_only": True, "first_party_owners": [], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []}, "source_status_summary": {"core_complete": False, "optional_complete": False, "noncomplete": []}, "semantic_ledger_sha256": None, "run_reason": "no_applicable_members", "diagnostic_source_status": statuses, "validator_result": {"status": "not_run", "reason": None},
        "artifacts": {key: {"present": False, "sha256": None} for key in ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")},
    }
    manifest["run_reason"] = run_reason
    manifest["validator_result"] = validator_result
    path = write_diagnostic(tmp_path / "diagnostics", manifest)
    loaded, code = verify_directory(path)
    assert loaded["run_id"] == manifest["run_id"]
    assert code == expected_code


def test_published_run_replays_and_rejects_tampering(tmp_path):
    statuses = source_status_object([SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2026-01-03T00:00:00Z") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")])
    event = LedgerEvent("alice", "U_1", "pr_opened", "P_1", "P_1", "R_1", "dynamia-ai/repo", "O_1", "dynamia-ai", "2026-01-01T12:00:00Z", None, 1, "2026-01-04T00:00:00Z", "2026-01-03T00:00:00Z", "search-prs_opened-20260101t000000z--20260102t000000z", "https://github.com/dynamia-ai/repo/pull/1")
    resolved = {"schema_version": "1.0", "timezone": "UTC", "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": "2020-01-01", "active_until": None}], "repository_policy": {"public_only": True, "first_party_owners": ["dynamia-ai"], "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []}}
    period = {"id": "explicit-20260101t000000z--20260102t000000z", "timezone": "UTC", "start_local": "2026-01-01T00:00:00+00:00", "end_local": "2026-01-02T00:00:00+00:00", "start_utc": "2026-01-01T00:00:00Z", "end_utc": "2026-01-02T00:00:00Z"}
    summary = render_summary(run_id="20260103t000000z-00000000-0000-4000-8000-000000000000", period=period, observed_at="2026-01-03T00:00:00Z", publishable=True, aggregate=aggregate([event], ["alice"], {"alice": "Alice"}, {"dynamia-ai"}, {"alice": True}))
    files = {"resolved-config.json": (json.dumps(resolved, sort_keys=True, separators=(",", ":")) + "\n").encode(), "event-ledger.jsonl": ledger_text([event.to_dict()]).encode(), "source-status.json": (json.dumps(statuses, sort_keys=True, separators=(",", ":")) + "\n").encode(), "summary.json": (json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n").encode(), "summary.csv": render_csv(summary["members"]).encode(), "report.md": render_markdown(summary, statuses).encode()}
    manifest_base = {"schema_version": "1.0", "run_id": summary["run_id"], "collector": {"version": "1.0.0", "git_commit": "0000000000000000000000000000000000000000"}, "github_rest_api_version": "2026-03-10", "period": period, "observed_at": "2026-01-03T00:00:00Z", "publish_visibility_verified_at": "2026-01-04T00:00:00Z", "safe_resolved_config_sha256": sha256_bytes(files["resolved-config.json"]), "member_config_sha256": sha256_json(resolved["members"]), "repository_policy_summary": resolved["repository_policy"], "source_status_summary": source_summary(statuses), "semantic_ledger_sha256": sha256_json(sorted([event.normalized_row_digest])), "diagnostic_source_status": None}
    path = write_published(tmp_path / "output", period["id"], summary["run_id"], files, manifest_base)
    _, code = verify_directory(path)
    assert code == 0
    original_manifest = (path / "run-manifest.json").read_bytes()
    stale_manifest = json.loads(original_manifest)
    stale_manifest["observed_at"] = "2026-01-02T00:00:00Z"
    stale_manifest["run_id"] = "20260102t000000z-00000000-0000-4000-8000-000000000000"
    stale_path = path.parent / stale_manifest["run_id"]
    path.rename(stale_path)
    (stale_path / "run-manifest.json").write_text(canonical_json(stale_manifest) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="period_invalid"):
        verify_directory(stale_path)
    stale_path.rename(path)
    (path / "run-manifest.json").write_bytes(original_manifest)
    renamed = path.parent / "renamed-run"
    path.rename(renamed)
    with pytest.raises(ValueError):
        verify_directory(renamed)
    renamed.rename(path)
    manifest = json.loads((path / "run-manifest.json").read_text(encoding="utf-8"))
    ledger = (path / "event-ledger.jsonl").read_bytes()
    duplicate_key_ledger = ledger.replace(b'"member_id":"alice"', b'"member_id":"alice","member_id":"alice"', 1)
    (path / "event-ledger.jsonl").write_bytes(duplicate_key_ledger)
    manifest["artifacts"]["event_ledger"]["sha256"] = sha256_bytes(duplicate_key_ledger)
    (path / "run-manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_directory(path)
    incomplete = [SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "partial", "api_contract_violation") if source == "prs_opened" else SourceStatus("alice", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2026-01-03T00:00:00Z") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")]
    incomplete_status = source_status_object(incomplete)
    incomplete_files = dict(files)
    incomplete_files["source-status.json"] = (canonical_json(incomplete_status) + "\n").encode()
    incomplete_base = dict(manifest_base)
    incomplete_base["source_status_summary"] = source_summary(incomplete_status)
    with pytest.raises(ValueError):
        write_published(tmp_path / "incomplete-output", period["id"], summary["run_id"], incomplete_files, incomplete_base)
    (path / "summary.json").write_text("not-json", encoding="utf-8")
    try:
        verify_directory(path)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered artifact was accepted")


@pytest.mark.parametrize("shape", ["extra_file", "extra_dir", "symlink", "hardlink", "fifo", "diagnostic_boundary"])
def test_committed_published_artifact_shape_and_boundary_attacks_fail_closed(tmp_path, shape):
    path = _committed_published_fixture(tmp_path)
    if shape == "extra_file":
        (path / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    elif shape == "extra_dir":
        (path / "unexpected").mkdir()
    elif shape == "symlink":
        target = path / "summary.json"
        target.unlink()
        target.symlink_to(path / "run-manifest.json")
    elif shape == "hardlink":
        target = path / "summary.json"
        target.unlink()
        target.hardlink_to(path / "run-manifest.json")
    elif shape == "fifo":
        target = path / "summary.json"
        target.unlink()
        os.mkfifo(target)
    else:
        boundary = tmp_path / "diagnostics" / path.name
        boundary.parent.mkdir(parents=True)
        path.rename(boundary)
        path = boundary
    with pytest.raises(ValueError):
        verify_directory(path)


@pytest.mark.parametrize("framing", ["pretty", "double_newline", "duplicate_key"])
def test_manifest_canonical_framing_attacks_are_isolated(tmp_path, framing):
    path = _committed_published_fixture(tmp_path)
    manifest_path = path / "run-manifest.json"
    raw = manifest_path.read_text(encoding="utf-8")
    if framing == "pretty":
        manifest_path.write_text(json.dumps(json.loads(raw), indent=2) + "\n", encoding="utf-8")
    elif framing == "double_newline":
        manifest_path.write_text(raw + "\n", encoding="utf-8")
    else:
        manifest_path.write_text(raw.replace('"schema_version":"1.0"', '"schema_version":"1.0","schema_version":"1.0"', 1), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_directory(path)


@pytest.mark.parametrize("shape", ["extra_file", "extra_dir", "symlink", "hardlink", "fifo"])
def test_diagnostic_directory_shape_attacks_are_exact_and_isolated(tmp_path, shape):
    path = _committed_diagnostic_fixture(tmp_path)
    target = path / "run-manifest.json"
    if shape == "extra_file":
        (path / "unexpected").write_text("unexpected", encoding="utf-8")
    elif shape == "extra_dir":
        (path / "unexpected").mkdir()
    elif shape == "symlink":
        (path / "unexpected").symlink_to(target)
    elif shape == "hardlink":
        (path / "unexpected").hardlink_to(target)
    else:
        os.mkfifo(path / "unexpected")
    with pytest.raises(ValueError):
        verify_directory(path)


def test_diagnostic_private_sentinel_and_published_rebound_fail_closed(tmp_path):
    diagnostic = _committed_diagnostic_fixture(tmp_path / "diagnostic")
    manifest_path = diagnostic / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["repository_policy_summary"]["applied_public_excluded_owner_ids"] = ["private-owner"]
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="privacy_invariant_failed"):
        verify_directory(diagnostic)

    published = _committed_published_fixture(tmp_path / "published")
    manifest_path = published / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["observed_at"] = "2026-08-02T00:00:00Z"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_directory(published)


@pytest.mark.parametrize("reason", ["identity_node_mismatch", "authentication_failed"])
def test_diagnostic_identity_auth_failure_replays_all_six_sources(tmp_path, reason):
    path = _committed_diagnostic_fixture(tmp_path)
    manifest_path = path / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["diagnostic_source_status"]["rows"]:
        row.update({"status": "failed", "reason": reason, "pagination_complete": None, "partition_complete": None, "snapshot_complete": None, "visibility_complete": None, "snapshot_completed_at": None})
    manifest["run_reason"] = "core_source_incomplete"
    manifest["source_status_summary"] = source_summary(manifest["diagnostic_source_status"])
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    loaded, code = verify_directory(path)
    assert loaded["diagnostic_source_status"]["rows"]
    assert len(loaded["diagnostic_source_status"]["rows"]) == 6
    assert code == 3


def test_diagnostic_allows_normalized_optional_commit_failure(tmp_path):
    path = _committed_diagnostic_fixture(tmp_path)
    manifest_path = path / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["diagnostic_source_status"]["rows"]:
        row.update({"status": "failed", "reason": "api_contract_violation", "pagination_complete": False, "partition_complete": None, "snapshot_complete": False, "visibility_complete": None, "snapshot_completed_at": None})
        if row["source"] == "commit_context":
            row.update({"status": "partial", "reason": "commit_context_unavailable", "pagination_complete": None, "partition_complete": None, "snapshot_complete": None, "visibility_complete": None})
    manifest["run_reason"] = "core_source_incomplete"
    manifest["source_status_summary"] = source_summary(manifest["diagnostic_source_status"])
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    loaded, code = verify_directory(path)
    assert loaded["diagnostic_source_status"]["rows"][-1]["reason"] == "commit_context_unavailable"
    assert code == 3


def test_diagnostic_allows_precise_optional_commit_gate_failure(tmp_path):
    path = _committed_diagnostic_fixture(tmp_path)
    manifest_path = path / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["diagnostic_source_status"]["rows"]:
        row.update({"status": "failed", "reason": "api_contract_violation", "pagination_complete": False, "partition_complete": None, "snapshot_complete": False, "visibility_complete": None, "snapshot_completed_at": None})
        if row["source"] == "commit_context":
            row.update({"status": "failed", "reason": "repository_binding_changed", "pagination_complete": True, "partition_complete": True, "snapshot_complete": True, "visibility_complete": False, "snapshot_completed_at": "2026-08-04T00:00:00Z"})
    manifest["run_reason"] = "core_source_incomplete"
    manifest["source_status_summary"] = source_summary(manifest["diagnostic_source_status"])
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    loaded, code = verify_directory(path)
    assert loaded["diagnostic_source_status"]["rows"][-1]["reason"] == "repository_binding_changed"
    assert code == 3
