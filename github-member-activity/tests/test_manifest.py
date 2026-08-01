import json

from github_member_activity.manifest import verify_directory, write_diagnostic


def test_diagnostic_manifest_has_exact_shape(tmp_path):
    manifest = {
        "schema_version": "1.0", "run_id": "20260801t000000z-00000000-0000-4000-8000-000000000000",
        "run_status": "diagnostic", "publishable": False,
        "artifacts": {key: {"present": False, "sha256": None} for key in ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")},
    }
    path = write_diagnostic(tmp_path / "diagnostics", manifest)
    loaded, code = verify_directory(path)
    assert loaded["run_id"] == manifest["run_id"]
    assert code == 3
