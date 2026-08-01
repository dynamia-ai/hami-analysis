from datetime import date

import pytest

from github_member_activity.config import AppConfig


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


def test_config_rejects_private_mode_and_extra_fields():
    bad = config()
    bad["repository_policy"]["public_only"] = False
    with pytest.raises(ValueError):
        AppConfig.model_validate(bad)
    bad = config()
    bad["unexpected"] = True
    with pytest.raises(ValueError):
        AppConfig.model_validate(bad)
