import subprocess
from datetime import UTC, datetime
import json
import hashlib
import os
from pathlib import Path
import sys

import pytest
from typer.testing import CliRunner

import github_member_activity.cli as cli_module
from github_member_activity.collector import CollectionResult
from github_member_activity.cli import _validate_receipt_path, _write_receipt, app
from github_member_activity.canonical import canonical_json
from github_member_activity.models import SourceStatus
from github_member_activity.period import ReportPeriod, build_period


RUNNER = CliRunner()
EXAMPLE_CONFIG = Path(__file__).parents[1] / "config.example.yaml"


def test_cli_help_documents_stable_exit_codes():
    result = subprocess.run(["uv", "run", "github-member-activity", "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    help_text = " ".join(result.stdout.split())
    assert "0 publishable run" in help_text
    assert "2 setup/configuration failure" in help_text
    assert "3 safe diagnostic run" in help_text
    assert "4 collection/core-source, receipt, verification, or artifact failure" in help_text


@pytest.mark.parametrize("command", ["verify", "render"])
def test_cli_missing_run_directory_is_integrity_failure(command, tmp_path):
    result = RUNNER.invoke(app, [command, "--run-dir", str(tmp_path / "missing")])
    assert result.exit_code == 4


def test_cli_verify_rejects_invalid_expected_manifest_hash(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = RUNNER.invoke(app, ["verify", "--run-dir", str(run_dir), "--expected-manifest-sha256", "not-a-sha"])
    assert result.exit_code == 4


def test_cli_verify_rejects_valid_but_wrong_expected_manifest_hash(tmp_path):
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "workflow" / "build_fixture.py"
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src"), "RUNNER_TEMP": str(runner_temp)}
    subprocess.run([sys.executable, str(fixture), "published"], cwd=tmp_path, env=env, check=True)
    run_dir = tmp_path / "output" / "weekly-20260727--20260803" / "20260804t000000z-00000000-0000-4000-8000-000000000000"
    actual = hashlib.sha256((run_dir / "run-manifest.json").read_bytes()).hexdigest()
    assert RUNNER.invoke(app, ["verify", "--run-dir", str(run_dir), "--expected-manifest-sha256", actual]).exit_code == 0
    wrong = "0" * 64 if actual != "0" * 64 else "1" * 64
    assert RUNNER.invoke(app, ["verify", "--run-dir", str(run_dir), "--expected-manifest-sha256", wrong]).exit_code == 4


def test_cli_validate_config_scheduled_example():
    result = RUNNER.invoke(app, ["validate-config", "--config", str(EXAMPLE_CONFIG), "--scheduled"])
    assert result.exit_code == 0
    assert "valid: members=1 timezone=Asia/Shanghai" in result.stdout


def test_cli_rejects_scheduled_non_asia_shanghai_and_enforces_stability_gap(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8").replace("timezone: Asia/Shanghai", "timezone: UTC"), encoding="utf-8")
    assert RUNNER.invoke(app, ["validate-config", "--config", str(config), "--scheduled"]).exit_code == 2

    asia_config = tmp_path / "asia-config.yaml"
    asia_config.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    result = RUNNER.invoke(app, ["collect", "--config", str(asia_config), "--from", "2099-01-01T00:00:00Z", "--to", "2099-01-02T00:00:00Z"])
    assert result.exit_code == 3
    diagnostics = list((tmp_path / "diagnostics").iterdir())
    assert len(diagnostics) == 1
    assert '"run_reason":"stability_gap_not_met"' in (diagnostics[0] / "run-manifest.json").read_text(encoding="utf-8")


def test_receipt_path_is_fixed_and_rejects_symlink_or_wrong_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    expected = tmp_path / "github-member-activity-receipt.json"
    _validate_receipt_path(expected)
    with pytest.raises(ValueError):
        _validate_receipt_path(tmp_path / "nested" / expected.name)
    expected.symlink_to(tmp_path / "other")
    with pytest.raises(ValueError):
        _validate_receipt_path(expected)


def test_collect_oserror_is_run_aborted_not_artifact_failure(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "test-token")

    class NoopClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(cli_module, "GitHubClient", NoopClient)
    monkeypatch.setattr(cli_module, "collect", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("collection transport")))
    result = RUNNER.invoke(app, ["collect", "--config", str(config), "--period", "weekly"])
    assert result.exit_code == 4
    diagnostic = list((tmp_path / "diagnostics").iterdir())
    assert len(diagnostic) == 1
    assert '"run_reason":"run_aborted"' in (diagnostic[0] / "run-manifest.json").read_text(encoding="utf-8")


def test_build_oserror_is_artifact_write_failure_and_writes_diagnostic(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "test-token")

    class NoopClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    statuses = [SourceStatus("example-member", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2099-01-01T00:00:00Z") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")]
    monkeypatch.setattr(cli_module, "GitHubClient", NoopClient)
    monkeypatch.setattr(cli_module, "token_for", lambda value: "test-token")
    monkeypatch.setattr(cli_module, "collect", lambda *args, **kwargs: CollectionResult([], statuses, [], [], "2026-08-01T00:00:00Z"))
    monkeypatch.setattr(cli_module, "aggregate", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("build failure")))
    result = RUNNER.invoke(app, ["collect", "--config", str(config), "--period", "weekly"])
    assert result.exit_code == 4
    diagnostic = list((tmp_path / "diagnostics").iterdir())
    assert len(diagnostic) == 1
    assert '"run_reason":"artifact_write_failed"' in (diagnostic[0] / "run-manifest.json").read_text(encoding="utf-8")


def test_receipt_failure_stops_before_diagnostic_without_recursive_compensation(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    class NoopClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    statuses = [SourceStatus("example-member", source, "optional" if source == "commit_context" else "core", "complete", None, True, None if source in {"issue_replies", "prs_reviewed"} else True, True, True, "2026-08-01T00:00:00Z") for source in ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context")]
    monkeypatch.setattr(cli_module, "GitHubClient", NoopClient)
    monkeypatch.setattr(cli_module, "token_for", lambda value: "test-token")
    monkeypatch.setattr(cli_module, "collect", lambda *args, **kwargs: CollectionResult([], statuses, [], [], "2026-08-01T00:00:00Z"))
    published = tmp_path / "published-run"
    monkeypatch.setattr(cli_module, "write_published", lambda *args, **kwargs: published)
    monkeypatch.setattr(cli_module, "_write_receipt", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("receipt write")))
    receipt = tmp_path / "github-member-activity-receipt.json"
    result = RUNNER.invoke(app, ["collect", "--config", str(config), "--period", "weekly", "--scheduled", "--receipt-path", str(receipt)])
    assert result.exit_code == 4
    assert not (tmp_path / "diagnostics").exists()


def test_receipt_transaction_is_canonical_bound_and_cleans_on_oserror(tmp_path, monkeypatch):
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")
    rid = "20260103t000000z-00000000-0000-4000-8000-000000000000"
    run_dir = tmp_path / "output" / period.id / rid
    run_dir.mkdir(parents=True)
    (run_dir / "run-manifest.json").write_text("{}\n", encoding="utf-8")
    manifest = {"run_id": rid, "period": {"id": period.id}}
    monkeypatch.setattr("github_member_activity.cli.verify_directory", lambda path: (manifest, 0))
    monkeypatch.setattr("github_member_activity.cli.digest_file", lambda path: "a" * 64)
    receipt = runner_temp / "github-member-activity-receipt.json"
    _write_receipt(receipt, period, rid, run_dir, tmp_path)
    raw = receipt.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw) == {"schema_version": "1.0", "period_id": period.id, "period_utc_slug": "20260101t000000z--20260102t000000z", "run_id": rid, "run_dir": f"output/{period.id}/{rid}", "manifest_sha256": "a" * 64}
    assert raw == (canonical_json(json.loads(raw)) + "\n").encode()

    receipt.unlink()
    real_replace = cli_module.os.replace

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(cli_module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        _write_receipt(receipt, period, rid, run_dir, tmp_path)
    assert not receipt.exists()
    assert not list(runner_temp.glob(".github-member-activity-receipt.*"))
    monkeypatch.setattr(cli_module.os, "replace", real_replace)


def test_receipt_transaction_baseexception_never_publishes_fixed_receipt(tmp_path, monkeypatch):
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    period = build_period("explicit", "UTC", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")
    rid = "20260103t000000z-00000000-0000-4000-8000-000000000000"
    run_dir = tmp_path / "output" / period.id / rid
    run_dir.mkdir(parents=True)
    (run_dir / "run-manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("github_member_activity.cli.verify_directory", lambda path: ({"run_id": rid, "period": {"id": period.id}}, 0))
    monkeypatch.setattr("github_member_activity.cli.digest_file", lambda path: "b" * 64)

    class Crash(BaseException):
        pass

    def crash_replace(source, target):
        raise Crash()

    monkeypatch.setattr(cli_module.os, "replace", crash_replace)
    receipt = runner_temp / "github-member-activity-receipt.json"
    with pytest.raises(Crash):
        _write_receipt(receipt, period, rid, run_dir, tmp_path)
    assert not receipt.exists()


@pytest.mark.parametrize("mode", ["published", "diagnostic_success"])
def test_receipt_transaction_preserves_real_published_or_diagnostic_authority(tmp_path, monkeypatch, mode):
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "workflow" / "build_fixture.py"
    case_root = tmp_path / mode
    runner_temp = case_root / "runner"
    runner_temp.mkdir(parents=True)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src"), "RUNNER_TEMP": str(runner_temp)}
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    subprocess.run([sys.executable, str(fixture), mode], cwd=case_root, env=env, check=True)
    run_id = "20260804t000000z-00000000-0000-4000-8000-000000000000"
    if mode == "published":
        run_dir = case_root / "output" / "weekly-20260727--20260803" / run_id
    else:
        run_dir = case_root / "diagnostics" / run_id
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    period = ReportPeriod("weekly", "Asia/Shanghai", datetime.fromisoformat(manifest["period"]["start_local"]), datetime.fromisoformat(manifest["period"]["end_local"]))
    receipt = runner_temp / "github-member-activity-receipt.json"
    _write_receipt(receipt, period, run_id, run_dir, case_root)
    assert receipt.is_file()
    assert cli_module.verify_directory(run_dir)[1] == (0 if mode == "published" else 3)
    receipt.unlink()

    real_replace = cli_module.os.replace

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(cli_module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        _write_receipt(receipt, period, run_id, run_dir, case_root)
    monkeypatch.setattr(cli_module.os, "replace", real_replace)
    assert not receipt.exists()
    assert cli_module.verify_directory(run_dir)[1] == (0 if mode == "published" else 3)
    authorities = list((case_root / ("output/weekly-20260727--20260803" if mode == "published" else "diagnostics")).glob("*/run-manifest.json"))
    assert len(authorities) == 1


def test_workflow_run_blocks_are_shell_parseable():
    workflow = Path(__file__).parents[2] / ".github" / "workflows" / "github-member-activity.yml"
    text = workflow.read_text(encoding="utf-8")
    wrapper = workflow.parents[2] / "github-member-activity" / "scripts" / "workflow_wrapper.sh"
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "uv run github-member-activity collect" in wrapper_text
    assert "github-member-activity-receipt.json" in wrapper_text
    assert "scripts/final_gate.sh" in text
    blocks = []
    current = []
    in_block = False
    for line in text.splitlines():
        if line.startswith("        run: |"):
            in_block = True
            current = []
            continue
        if in_block and line.startswith("        ") and not line.startswith("          "):
            blocks.append("\n".join(current) + "\n")
            in_block = False
        elif in_block:
            current.append(line[10:] if line.startswith("          ") else line)
    if in_block:
        blocks.append("\n".join(current) + "\n")
    assert blocks
    for block in blocks:
        completed = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(["bash", "-n", str(wrapper)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    final_gate = wrapper.parent / "final_gate.sh"
    completed = subprocess.run(["bash", "-n", str(final_gate)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
