from pathlib import Path
import re
import subprocess
import os
import sys

import yaml

from github_member_activity.workflow_gate import evaluate


def test_workflow_has_frozen_schedule_and_fail_gate():
    root = Path(__file__).parents[2]
    text = root.joinpath(".github/workflows/github-member-activity.yml").read_text(encoding="utf-8")
    wrapper = root.joinpath("github-member-activity/scripts/workflow_wrapper.sh").read_text(encoding="utf-8")
    assert "cron: '15 1 * * 2'" in text
    assert "cron: '30 1 2 * *'" in text
    assert "verify --run-dir" in wrapper
    assert "--expected-manifest-sha256" in wrapper
    assert "if: always() && steps.collect.outputs.artifact_ready == 'true'" in text
    assert "permissions:" in text and "contents: read" in text
    assert "PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG" in text
    assert "config.example.yaml" in wrapper
    assert not re.search(r"@(HEAD|main|master)\b", text)
    data = yaml.safe_load(text)
    trigger = data.get("on", data.get(True))
    assert {item["cron"] for item in trigger["schedule"]} == {"15 1 * * 2", "30 1 2 * *"}
    assert "github_member_activity.workflow_gate" in wrapper
    assert "scripts/workflow_wrapper.sh" in text
    assert "scripts/final_gate.sh" in text


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


def test_final_gate_executes_frozen_upload_matrix(tmp_path):
    gate = Path(__file__).parents[1] / "scripts" / "final_gate.sh"
    cases = [
        ({"COLLECT_EXIT_CODE": "0", "ARTIFACT_READY": "true", "UPLOAD_OUTCOME": "success"}, 0),
        ({"COLLECT_EXIT_CODE": "3", "ARTIFACT_READY": "true", "UPLOAD_OUTCOME": "success"}, 3),
        ({"COLLECT_EXIT_CODE": "4", "ARTIFACT_READY": "true", "UPLOAD_OUTCOME": "success"}, 4),
        ({"COLLECT_EXIT_CODE": "0", "ARTIFACT_READY": "true", "UPLOAD_OUTCOME": "failure"}, 4),
        ({"COLLECT_EXIT_CODE": "0", "ARTIFACT_READY": "false", "UPLOAD_OUTCOME": "skipped"}, 4),
        ({"COLLECT_EXIT_CODE": "3", "ARTIFACT_READY": "false", "UPLOAD_OUTCOME": "skipped"}, 4),
        ({"COLLECT_EXIT_CODE": "2", "ARTIFACT_READY": "false", "UPLOAD_OUTCOME": "skipped"}, 2),
        ({"COLLECT_EXIT_CODE": "2", "ARTIFACT_READY": "true", "UPLOAD_OUTCOME": "success"}, 4),
        ({"COLLECT_EXIT_CODE": "9", "ARTIFACT_READY": "false", "UPLOAD_OUTCOME": "skipped"}, 4),
    ]
    for variables, expected in cases:
        result = subprocess.run([str(gate)], env={**__import__("os").environ, **variables}, check=False)
        assert result.returncode == expected


def test_production_wrapper_executes_setup_and_receipt_fail_closed_paths(tmp_path):
    root = Path(__file__).parents[1]
    wrapper = root / "scripts" / "workflow_wrapper.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$2\" = \"github-member-activity\" ] && [ \"$3\" = \"validate-config\" ]; then exit 0; fi\n"
        "if [ \"$2\" = \"github-member-activity\" ] && [ \"$3\" = \"collect\" ]; then exit 3; fi\n"
        "exit 99\n",
        encoding="utf-8",
    )
    (fake_bin / "uv").chmod(0o755)

    no_config_env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path / "runner-1"), "GITHUB_OUTPUT": str(tmp_path / "out-1")}
    Path(no_config_env["RUNNER_TEMP"]).mkdir()
    result = subprocess.run([str(wrapper)], cwd=root, env=no_config_env, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert "artifact_ready=false" in Path(no_config_env["GITHUB_OUTPUT"]).read_text()
    assert "exit_code=2" in Path(no_config_env["GITHUB_OUTPUT"]).read_text()

    (tmp_path / "config.yaml").write_text("fixture\n", encoding="utf-8")
    malformed_env = {**no_config_env, "RUNNER_TEMP": str(tmp_path / "runner-2"), "GITHUB_OUTPUT": str(tmp_path / "out-2"), "WORKFLOW_EVENT_NAME": "schedule", "WORKFLOW_SCHEDULE": "15 1 * * 2"}
    Path(malformed_env["RUNNER_TEMP"]).mkdir()
    result = subprocess.run([str(wrapper)], cwd=tmp_path, env=malformed_env, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    output = Path(malformed_env["GITHUB_OUTPUT"]).read_text()
    assert "collector_exit_code=3" in output
    assert "artifact_ready=false" in output
    assert "exit_code=4" in output


def test_committed_workflow_fixtures_execute_success_receipt_path_and_fault_matrix(tmp_path):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text((fixtures / "fake_uv.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    base_env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PYTHONPATH": str(root / "src"),
        "PYTHON_EXECUTABLE": sys.executable,
        "FIXTURE_BUILDER": str(fixtures / "build_fixture.py"),
        "WORKFLOW_EVENT_NAME": "schedule",
        "WORKFLOW_SCHEDULE": "15 1 * * 2",
    }
    cases = [("success", True, 3), ("malformed_receipt", False, 4), ("wrong_path", False, 4), ("collector_crash", False, 4)]
    for index, (mode, ready, exit_code) in enumerate(cases):
        run_root = tmp_path / f"run-{index}"
        run_root.mkdir()
        (run_root / "config.yaml").write_text((fixtures / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        runner_temp = run_root / "runner-temp"
        runner_temp.mkdir()
        output = run_root / "github-output"
        env = {**base_env, "FIXTURE_MODE": mode, "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output)}
        result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr
        values = output.read_text(encoding="utf-8")
        assert f"artifact_ready={'true' if ready else 'false'}" in values
        assert f"exit_code={exit_code}" in values
        if ready:
            assert (run_root / "diagnostics" / "20260801t000000z-00000000-0000-4000-8000-000000000000" / "run-manifest.json").is_file()
            assert (runner_temp / "github-member-activity-receipt.json").is_file()

    (fake_bin / "rm").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "rm").chmod(0o755)
    stale_env = {**base_env, "RUNNER_TEMP": str(tmp_path / "runner-3"), "GITHUB_OUTPUT": str(tmp_path / "out-3")}
    Path(stale_env["RUNNER_TEMP"]).mkdir()
    Path(stale_env["RUNNER_TEMP"]).joinpath("github-member-activity-receipt.json").write_text("stale\n", encoding="utf-8")
    result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=root, env=stale_env, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    output = Path(stale_env["GITHUB_OUTPUT"]).read_text()
    assert "artifact_ready=false" in output
    assert "exit_code=4" in output
