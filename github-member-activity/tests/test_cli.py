import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import github_member_activity.cli as cli_module
from github_member_activity.collector import CollectionResult
from github_member_activity.cli import _validate_receipt_path, app
from github_member_activity.models import SourceStatus


RUNNER = CliRunner()
EXAMPLE_CONFIG = Path(__file__).parents[1] / "config.example.yaml"


def test_cli_help_documents_stable_exit_codes():
    result = subprocess.run(["uv", "run", "github-member-activity", "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "0 publishable" in result.stdout
    assert "setup/configuration" in result.stdout
    assert "diagnostic run" in result.stdout
    assert "integrity/artifact failure" in result.stdout


@pytest.mark.parametrize("command", ["verify", "render"])
def test_cli_missing_run_directory_is_integrity_failure(command, tmp_path):
    result = RUNNER.invoke(app, [command, "--run-dir", str(tmp_path / "missing")])
    assert result.exit_code == 4


def test_cli_validate_config_scheduled_example():
    result = RUNNER.invoke(app, ["validate-config", "--config", str(EXAMPLE_CONFIG), "--scheduled"])
    assert result.exit_code == 0
    assert "valid: members=1 timezone=Asia/Shanghai" in result.stdout


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


def test_workflow_run_blocks_are_shell_parseable():
    workflow = Path(__file__).parents[2] / ".github" / "workflows" / "github-member-activity.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "uv run github-member-activity collect" in text
    assert "github-member-activity-receipt.json" in text
    assert "case \"$code\" in 0|2|3|4" in text
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
