from datetime import date
from pathlib import Path
import subprocess

import pytest

from github_member_activity.canonical import sha256_json
from github_member_activity.config import AppConfig, member_config_sha256, safe_resolved_config


def config() -> dict:
    return {
        "github": {"token_env": "PUBLIC_GITHUB_TOKEN", "api_version": "2026-03-10"},
        "period": {"timezone": "Asia/Shanghai"},
        "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U_1", "active_from": date(2026, 1, 1)}],
        "repository_policy": {"public_only": True, "first_party_owners": ["Project-HAMi", "dynamia-ai"]},
        "output": {"directory": "./output"},
    }


def test_config_normalizes_first_party_owners():
    value = AppConfig.model_validate(config())
    assert value.repository_policy.first_party_owners == ["dynamia-ai", "project-hami"]
    assert member_config_sha256(value) == sha256_json(safe_resolved_config(value)["members"])


def test_config_rejects_private_mode_and_extra_fields():
    bad = config()
    bad["repository_policy"]["public_only"] = False
    with pytest.raises(ValueError):
        AppConfig.model_validate(bad)
    bad = config()
    bad["unexpected"] = True
    with pytest.raises(ValueError):
        AppConfig.model_validate(bad)


def test_config_accepts_padded_github_node_ids():
    value = config()
    value["members"][0]["github_node_id"] = "MDQ6VXNlcjMzMjgxODU="
    assert AppConfig.model_validate(value).members[0].github_node_id.endswith("=")


@pytest.mark.parametrize("directory", ["output", "./reports", "reports", "../output", "/tmp/output"])
def test_config_rejects_output_directories_that_are_not_ignored(directory: str):
    value = config()
    value["output"]["directory"] = directory
    with pytest.raises(ValueError, match="output.directory"):
        AppConfig.model_validate(value)


def test_runtime_config_and_artifacts_are_git_ignored():
    repository = Path(__file__).resolve().parents[2]
    for path in (
        "github-member-activity/config.yaml",
        "github-member-activity/output/event-ledger.jsonl",
        "github-member-activity/output/report.md",
        "github-member-activity/output/run-manifest.json",
        "github-member-activity/diagnostics/run-manifest.json",
    ):
        result = subprocess.run(
            ["git", "-C", str(repository), "check-ignore", "-q", "--", path],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
