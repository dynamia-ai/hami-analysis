from pathlib import Path
import re
import subprocess

import yaml

from github_member_activity.workflow_gate import evaluate


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
    assert "github_member_activity.workflow_gate" in text


def test_workflow_exit_matrix_is_executable():
    cases = [
        (2, False, "", "", "", "", 4, (False, 2)),
        (2, True, "diagnostic", "False", "run_aborted", "not_run", 3, (False, 4)),
        (0, True, "published", "True", "", "passed", 0, (True, 0)),
        (0, True, "diagnostic", "False", "run_aborted", "not_run", 3, (False, 4)),
        (3, True, "diagnostic", "False", "stability_gap_not_met", "not_run", 3, (True, 3)),
        (3, True, "diagnostic", "False", "core_source_incomplete", "not_run", 3, (False, 4)),
        (4, True, "diagnostic", "False", "validation_failed", "failed", 3, (True, 4)),
        (4, True, "diagnostic", "False", "artifact_write_failed", "not_run", 3, (True, 4)),
        (4, True, "diagnostic", "False", "artifact_write_failed", "not_run", 0, (False, 4)),
    ]
    for collector_code, receipt, status, publishable, reason, validator, verify_code, expected in cases:
        validator_reason = "ledger_invalid" if reason == "validation_failed" else ""
        assert evaluate(collector_code, receipt_present=receipt, manifest_status=status, manifest_publishable=publishable, manifest_reason=reason, validator_status=validator, validator_reason=validator_reason, verify_code=verify_code) == expected
    command = ["uv", "run", "python", "-m", "github_member_activity.workflow_gate", "--collector-code", "3", "--receipt-present", "--manifest-status", "diagnostic", "--manifest-publishable", "False", "--manifest-reason", "no_applicable_members", "--validator-status", "not_run", "--verify-code", "3"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "artifact_ready=true" in result.stdout
    assert "exit_code=3" in result.stdout
