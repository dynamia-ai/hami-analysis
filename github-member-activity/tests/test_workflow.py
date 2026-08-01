from pathlib import Path
import re

import yaml


def test_workflow_has_frozen_schedule_and_fail_gate():
    text = Path(__file__).parents[2].joinpath(".github/workflows/github-member-activity.yml").read_text(encoding="utf-8")
    assert "cron: '15 1 * * 2'" in text
    assert "cron: '30 1 2 * *'" in text
    assert "verify --run-dir" in text
    assert "--expected-manifest-sha256" in text
    assert "if: always() && steps.collect.outputs.artifact_ready == 'true'" in text
    assert "permissions:" in text and "contents: read" in text
    assert "PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG" in text
    assert "config.example.yaml" in text
    assert not re.search(r"@(HEAD|main|master)\b", text)
    data = yaml.safe_load(text)
    trigger = data.get("on", data.get(True))
    assert {item["cron"] for item in trigger["schedule"]} == {"15 1 * * 2", "30 1 2 * *"}
