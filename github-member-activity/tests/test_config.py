from datetime import date

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
