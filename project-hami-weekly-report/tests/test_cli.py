from pathlib import Path

import hami_github_activity.cli as cli_module
from hami_github_activity.models import CollectionResult
from hami_github_activity.github_client import PaginatedResult
from typer.testing import CliRunner

from hami_github_activity.cli import app


runner = CliRunner()


def write_config(path: Path) -> None:
    path.write_text(
        """github:\n  org: Project-HAMi\n  token_env: TEST_GITHUB_TOKEN\n  expected_repositories:\n    - Project-HAMi/HAMi\nscan:\n  days: 7\n  timezone: Asia/Shanghai\noutput:\n  file: ./output/{org}-{start_date}-{end_date}.md\n""",
        encoding="utf-8",
    )


def test_validate_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    result = runner.invoke(app, ["validate-config", "--config", str(path)])
    assert result.exit_code == 0
    assert "Configuration is valid" in result.stdout


def test_dry_run_does_not_require_token_or_write_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    result = runner.invoke(
        app,
        [
            "collect",
            "--config",
            str(path),
            "--start-date",
            "2026-07-10",
            "--end-date",
            "2026-07-16",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Organization: Project-HAMi" in result.stdout
    assert "2026-07-16T23:59:59.999999+08:00" in result.stdout
    assert "no GitHub requests were made" in result.stdout
    assert not (tmp_path / "output").exists()


def test_missing_token_is_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    result = runner.invoke(app, ["collect", "--config", str(path)], env={"TEST_GITHUB_TOKEN": ""})
    assert result.exit_code == 2
    assert "Missing GitHub token environment variable" in result.stderr


def test_collect_writes_one_evidence_file_and_prints_summary(tmp_path: Path, monkeypatch: object) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)

    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, _: str, **kwargs: object) -> None:
            captured["client"] = kwargs

        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def list_org_repositories(self, _: str) -> PaginatedResult:
            return PaginatedResult(items=[{"full_name": "Project-HAMi/HAMi"}])

    class DummyCollector:
        def __init__(self, *_: object, **kwargs: object) -> None:
            captured["collector"] = kwargs

        def collect(self, _: str) -> CollectionResult:
            return CollectionResult(failed_requests=1, rate_limit_remaining=88)

    monkeypatch.setattr(cli_module, "GitHubClient", DummyClient)  # type: ignore[attr-defined]
    monkeypatch.setattr(cli_module, "ActivityCollector", DummyCollector)  # type: ignore[attr-defined]
    result = runner.invoke(
        app,
        [
            "collect",
            "--config",
            str(path),
            "--start-date",
            "2026-07-10",
            "--end-date",
            "2026-07-16",
            "--workers",
            "3",
        ],
        env={"TEST_GITHUB_TOKEN": "token"},
    )
    outputs = list((tmp_path / "output").glob("*.md"))
    assert result.exit_code == 0
    assert len(outputs) == 1
    assert outputs[0].read_text(encoding="utf-8").startswith('---\nschema_version: "1.0"')
    assert "API request failures: 1" in result.stdout
    assert "GitHub API rate limit remaining: 88" in result.stdout
    assert "Starting GitHub collection" in result.stderr
    assert "Rendering Markdown evidence" in result.stderr
    assert "Writing evidence file" in result.stderr
    assert "token" not in result.stderr
    assert captured["client"] == {"max_connections": 3, "requests_per_second": 10.0}
    assert captured["collector"] == {"workers": 3}


def test_repository_visibility_rejects_an_unlisted_visible_repository() -> None:
    result = CollectionResult()

    cli_module._record_repository_visibility(
        result,
        expected=["Project-HAMi/HAMi"],
        repositories=[
            {"full_name": "Project-HAMi/HAMi"},
            {"full_name": "Project-HAMi/new-repository"},
        ],
        inventory_incomplete=False,
    )

    assert result.collection_status == "partial"
    assert "repository inventory has unlisted repositories: Project-HAMi/new-repository" in result.partial_reasons


def test_repository_visibility_rejects_non_object_inventory_entries() -> None:
    result = CollectionResult()

    cli_module._record_repository_visibility(
        result,
        expected=["Project-HAMi/HAMi"],
        repositories=[{"full_name": "Project-HAMi/HAMi"}],
        inventory_incomplete=False,
        inventory_malformed_item_count=1,
    )

    assert result.collection_status == "partial"
    assert "repository inventory returned 1 non-object entries" in result.partial_reasons
