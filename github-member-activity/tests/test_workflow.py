from pathlib import Path
import os
import re
import subprocess
import sys

import pytest
import yaml

from github_member_activity.workflow_gate import evaluate


def _values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            assert key not in values
            values[key] = value
    return values


def test_workflow_has_frozen_static_contract():
    root = Path(__file__).parents[2]
    text = root.joinpath(".github/workflows/github-member-activity.yml").read_text(encoding="utf-8")
    wrapper = root.joinpath("github-member-activity/scripts/workflow_wrapper.sh").read_text(encoding="utf-8")
    assert "cron: '15 1 * * 2'" in text
    assert "cron: '30 1 2 * *'" in text
    assert "timeout-minutes: 120" in text
    for action in (
        "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "astral-sh/setup-uv@6b9c6063abd4a2e6e5c9c6d6d0c7d25f4c0b0c21",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        assert action in text
    assert "name: github-member-activity-${{ steps.collect.outputs.period_id }}-${{ steps.collect.outputs.period_utc_slug }}-${{ steps.collect.outputs.manifest_sha256 }}" in text
    assert text.count("if: always()") == 2
    assert "if: always() && steps.collect.outputs.artifact_ready == 'true'" in text
    assert "if: always()\n" in text
    assert "permissions:" in text and "contents: read" in text
    assert "PUBLIC_GITHUB_MEMBER_ACTIVITY_CONFIG" in text
    assert "config.example.yaml" in wrapper
    assert not re.search(r"@(HEAD|main|master)\b", text)
    data = yaml.safe_load(text)
    trigger = data.get("on", data.get(True))
    assert {item["cron"] for item in trigger["schedule"]} == {"15 1 * * 2", "30 1 2 * *"}
    assert trigger["workflow_dispatch"]["inputs"]["period"]["options"] == ["weekly", "monthly"]
    steps = data["jobs"]["collect"]["steps"]
    assert steps[0]["uses"] == "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert steps[0]["with"] == {"persist-credentials": False}
    assert steps[1]["uses"] == "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
    assert steps[1]["with"] == {"python-version": "3.14"}
    assert steps[2]["uses"] == "astral-sh/setup-uv@6b9c6063abd4a2e6e5c9c6d6d0c7d25f4c0b0c21"
    collect_step = next(step for step in steps if step.get("id") == "collect")
    assert collect_step["name"] == "Collect and verify"
    upload_step = next(step for step in steps if step.get("id") == "upload")
    assert upload_step["uses"] == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    assert upload_step["if"] == "always() && steps.collect.outputs.artifact_ready == 'true'"
    assert upload_step["with"] == {"name": "github-member-activity-${{ steps.collect.outputs.period_id }}-${{ steps.collect.outputs.period_utc_slug }}-${{ steps.collect.outputs.manifest_sha256 }}", "path": "${{ steps.collect.outputs.artifact_path }}", "if-no-files-found": "error"}
    final_step = next(step for step in steps if step.get("name") == "Final gate")
    assert final_step["if"] == "always()"
    assert final_step["run"] == "scripts/final_gate.sh"
    assert final_step["env"] == {"COLLECT_EXIT_CODE": "${{ steps.collect.outputs.exit_code || '4' }}", "ARTIFACT_READY": "${{ steps.collect.outputs.artifact_ready }}", "UPLOAD_OUTCOME": "${{ steps.upload.outcome }}"}
    assert "github_member_activity.workflow_gate" in wrapper
    assert "scripts/workflow_wrapper.sh" in text
    assert "scripts/final_gate.sh" in text
    assert "--period \"$selected_period\" --scheduled" in wrapper
    assert "--receipt-path \"$receipt\"" in wrapper
    assert "--expected-manifest-sha256 \"$manifest_sha\"" in wrapper


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


@pytest.mark.parametrize("collector_code", [0, 2, 3, 4, 9, None])
@pytest.mark.parametrize("artifact_ready", ["true", "false", None])
@pytest.mark.parametrize("upload_outcome", ["success", "failure", "skipped", None])
def test_final_gate_executes_all_frozen_72_unique_combinations(collector_code, artifact_ready, upload_outcome):
    gate = Path(__file__).parents[1] / "scripts" / "final_gate.sh"
    env = dict(os.environ)
    if collector_code is None:
        env.pop("COLLECT_EXIT_CODE", None)
    else:
        env["COLLECT_EXIT_CODE"] = str(collector_code)
    if artifact_ready is None:
        env.pop("ARTIFACT_READY", None)
    else:
        env["ARTIFACT_READY"] = artifact_ready
    if upload_outcome is None:
        env.pop("UPLOAD_OUTCOME", None)
    else:
        env["UPLOAD_OUTCOME"] = upload_outcome
    result = subprocess.run(
        [str(gate)],
        env=env,
        check=False,
    )
    if collector_code in {0, 3}:
        expected = collector_code if artifact_ready == "true" and upload_outcome == "success" else 4
    elif collector_code == 2:
        expected = 4 if artifact_ready == "true" else 2
    else:
        expected = 4
    assert result.returncode == expected


def test_workflow_run_blocks_are_shell_parseable_and_all_production_runs_are_extracted():
    root = Path(__file__).parents[1]
    workflow = root.parents[0] / ".github" / "workflows" / "github-member-activity.yml"
    text = workflow.read_text(encoding="utf-8")
    wrapper = root / "scripts" / "workflow_wrapper.sh"
    blocks = []
    current = []
    in_block = False
    for line in text.splitlines():
        if line.startswith("        run: |"):
            in_block = True
            current = []
            continue
        if in_block and line.startswith("      - "):
            blocks.append("\n".join(current) + "\n")
            in_block = False
        elif in_block:
            current.append(line[10:] if line.startswith("          ") else line)
    if in_block:
        blocks.append("\n".join(current) + "\n")
    assert len(blocks) == 1
    data = yaml.safe_load(text)
    run_steps = [step["run"] for step in data["jobs"]["collect"]["steps"] if "run" in step]
    assert len(run_steps) == 4
    for block in run_steps:
        completed = subprocess.run(["bash", "-n"], input=block + ("\n" if not block.endswith("\n") else ""), text=True, capture_output=True)
        assert completed.returncode == 0, completed.stderr
    for script in (wrapper, root / "scripts" / "final_gate.sh"):
        completed = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
        assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("mode", "ready", "exit_code", "collector_code"),
    [
        ("published", True, 0, 0),
        ("diagnostic_success", True, 3, 3),
        ("safe_diagnostic", True, 4, 4),
        ("validation_failed", True, 4, 4),
        ("collector_2", False, 2, 2),
        ("collector_0_diagnostic", False, 4, 0),
        ("collector_3_code4_reason", False, 4, 3),
        ("collector_4", True, 4, 4),
        ("verify_fail", False, 4, 3),
        ("malformed_receipt", False, 4, 3),
        ("receipt_pretty", False, 4, 3),
        ("receipt_duplicate", False, 4, 3),
        ("receipt_missing", False, 4, 3),
        ("receipt_extra", False, 4, 3),
        ("receipt_wrong_type", False, 4, 3),
        ("receipt_wrong_hash", False, 4, 3),
        ("receipt_wrong_period", False, 4, 3),
        ("receipt_wrong_run", False, 4, 3),
        ("receipt_wrong_slug", False, 4, 3),
        ("receipt_absolute_path", False, 4, 3),
        ("receipt_dotdot", False, 4, 3),
        ("wrong_path_valid", False, 4, 3),
        ("status_swap", False, 4, 3),
        ("collector_crash", False, 4, 99),
    ],
)
def test_committed_workflow_fixtures_execute_real_verify_receipt_path_and_fault_matrix(tmp_path, mode, ready, exit_code, collector_code):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text((fixtures / "fake_uv.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "config.yaml").write_text((fixtures / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    runner_temp = run_root / "runner-temp"
    runner_temp.mkdir()
    output = run_root / "github-output"
    args_log = run_root / "args.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PYTHONPATH": str(root / "src"),
        "PYTHON_EXECUTABLE": sys.executable,
        "FIXTURE_BUILDER": str(fixtures / "build_fixture.py"),
        "FIXTURE_MODE": mode,
        "FIXTURE_ARGS_LOG": str(args_log),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
        "WORKFLOW_EVENT_NAME": "schedule",
        "WORKFLOW_SCHEDULE": "15 1 * * 2",
    }
    result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    values = _values(output)
    assert values["artifact_ready"] == ("true" if ready else "false")
    assert values["exit_code"] == str(exit_code)
    assert values["collector_exit_code"] == str(collector_code)
    logged = args_log.read_text(encoding="utf-8") if args_log.exists() else ""
    if ready or mode == "verify_fail":
        assert "verify" in logged and "--expected-manifest-sha256" in logged
    if ready:
        assert values["artifact_path"]
        assert values["manifest_sha256"]
        if mode == "published":
            assert (run_root / "output" / "weekly-20260727--20260803" / "20260804t000000z-00000000-0000-4000-8000-000000000000" / "run-manifest.json").is_file()
        else:
            assert (run_root / "diagnostics" / "20260804t000000z-00000000-0000-4000-8000-000000000000" / "run-manifest.json").is_file()
        final = subprocess.run(
            [str(root / "scripts" / "final_gate.sh")],
            env={**env, "COLLECT_EXIT_CODE": values["exit_code"], "ARTIFACT_READY": values["artifact_ready"], "UPLOAD_OUTCOME": "success"},
            check=False,
        )
        assert final.returncode == exit_code
        failed_upload = subprocess.run(
            [str(root / "scripts" / "final_gate.sh")],
            env={**env, "COLLECT_EXIT_CODE": values["exit_code"], "ARTIFACT_READY": values["artifact_ready"], "UPLOAD_OUTCOME": "failure"},
            check=False,
        )
        assert failed_upload.returncode == 4


def test_workflow_fixture_fails_when_fake_verify_bypasses_production_cli(tmp_path):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = (fixtures / "fake_uv.sh").read_text(encoding="utf-8")
    fake_uv = fake_uv.replace('    "$python_bin" -m github_member_activity verify "${@:4}"\n', "    exit 3\n")
    (fake_bin / "uv").write_text(fake_uv, encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "config.yaml").write_text((fixtures / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    runner_temp = run_root / "runner-temp"
    runner_temp.mkdir()
    output = run_root / "github-output"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "PYTHONPATH": str(root / "src"), "PYTHON_EXECUTABLE": sys.executable, "FIXTURE_BUILDER": str(fixtures / "build_fixture.py"), "FIXTURE_MODE": "published", "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output), "WORKFLOW_EVENT_NAME": "schedule", "WORKFLOW_SCHEDULE": "15 1 * * 2"}
    result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    values = _values(output)
    assert values["collector_exit_code"] == "0"
    assert values["artifact_ready"] == "false"
    assert values["exit_code"] == "4"


@pytest.mark.parametrize("event_name,schedule,dispatch,expected_period", [
    ("schedule", "15 1 * * 2", "", "weekly"),
    ("schedule", "30 1 2 * *", "", "monthly"),
])
@pytest.mark.parametrize("mode,expected_ready,collector_code", [
    ("published", "true", 0),
    ("diagnostic_success", "true", 3),
    ("safe_diagnostic", "true", 4),
    ("collector_2", "false", 2),
    ("verify_fail", "false", 3),
    ("malformed_receipt", "false", 3),
])
@pytest.mark.parametrize("upload_outcome", ["success", "failure", "skipped"])
def test_workflow_wrapper_to_final_gate_executes_36_event_upload_combinations(tmp_path, event_name, schedule, dispatch, expected_period, mode, expected_ready, collector_code, upload_outcome):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text((fixtures / "fake_uv.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "config.yaml").write_text((fixtures / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    runner_temp = run_root / "runner-temp"
    runner_temp.mkdir()
    output = run_root / "github-output"
    args_log = run_root / "args.log"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "PYTHONPATH": str(root / "src"), "PYTHON_EXECUTABLE": sys.executable, "FIXTURE_BUILDER": str(fixtures / "build_fixture.py"), "FIXTURE_MODE": mode, "FIXTURE_ARGS_LOG": str(args_log), "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output), "WORKFLOW_EVENT_NAME": event_name, "WORKFLOW_SCHEDULE": schedule, "WORKFLOW_DISPATCH_PERIOD": dispatch}
    result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    values = _values(output)
    assert values["artifact_ready"] == expected_ready
    assert values["collector_exit_code"] == str(collector_code)
    assert f"--period {expected_period}" in args_log.read_text(encoding="utf-8")
    final = subprocess.run([str(root / "scripts" / "final_gate.sh")], env={**env, "COLLECT_EXIT_CODE": values["exit_code"], "ARTIFACT_READY": values["artifact_ready"], "UPLOAD_OUTCOME": upload_outcome}, check=False)
    if collector_code == 2 and expected_ready == "false":
        assert final.returncode == 2
    elif values["artifact_ready"] == "true" and upload_outcome == "success" and collector_code in {0, 3}:
        assert final.returncode == collector_code
    else:
        assert final.returncode == 4


@pytest.mark.parametrize("event_name,schedule,dispatch,expected", [
    ("schedule", "15 1 * * 2", "", "weekly"),
    ("schedule", "30 1 2 * *", "", "monthly"),
    ("workflow_dispatch", "", "weekly", "weekly"),
    ("workflow_dispatch", "", "monthly", "monthly"),
])
def test_workflow_event_period_mapping_is_executed(tmp_path, event_name, schedule, dispatch, expected):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text((fixtures / "fake_uv.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "config.yaml").write_text((fixtures / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    runner_temp = run_root / "runner-temp"
    runner_temp.mkdir()
    output = run_root / "github-output"
    args_log = run_root / "args.log"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "PYTHONPATH": str(root / "src"), "PYTHON_EXECUTABLE": sys.executable, "FIXTURE_BUILDER": str(fixtures / "build_fixture.py"), "FIXTURE_MODE": "diagnostic_success", "FIXTURE_ARGS_LOG": str(args_log), "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output), "WORKFLOW_EVENT_NAME": event_name, "WORKFLOW_SCHEDULE": schedule, "WORKFLOW_DISPATCH_PERIOD": dispatch}
    result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert f"--period {expected}" in args_log.read_text(encoding="utf-8")
    values = _values(output)
    assert values["period_id"] == ("monthly-20260701--20260801" if expected == "monthly" else "weekly-20260727--20260803")


def test_workflow_unsupported_event_fails_without_collection(tmp_path):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text((fixtures / "fake_uv.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "config.yaml").write_text((fixtures / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    runner_temp = run_root / "runner-temp"
    runner_temp.mkdir()
    output = run_root / "github-output"
    log = run_root / "args.log"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "PYTHONPATH": str(root / "src"), "PYTHON_EXECUTABLE": sys.executable, "FIXTURE_BUILDER": str(fixtures / "build_fixture.py"), "FIXTURE_MODE": "diagnostic_success", "FIXTURE_ARGS_LOG": str(log), "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output), "WORKFLOW_EVENT_NAME": "repository_dispatch", "WORKFLOW_SCHEDULE": ""}
    result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert _values(output) == {"artifact_ready": "false", "exit_code": "4"}
    assert "collect" not in log.read_text(encoding="utf-8")


def test_stale_receipt_type_and_removal_matrix(tmp_path):
    root = Path(__file__).parents[1]
    fixtures = root / "tests" / "fixtures" / "workflow"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text((fixtures / "fake_uv.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)

    def run_with_receipt(kind: str, rm_mode: str | None = None):
        run_root = tmp_path / kind
        run_root.mkdir()
        runner_temp = run_root / "runner-temp"
        runner_temp.mkdir()
        receipt = runner_temp / "github-member-activity-receipt.json"
        if kind == "symlink":
            receipt.symlink_to(run_root / "elsewhere")
        elif kind == "hardlink":
            source = run_root / "source"
            source.write_text("stale", encoding="utf-8")
            receipt.hardlink_to(source)
        elif kind == "directory":
            receipt.mkdir()
        elif kind == "fifo":
            os.mkfifo(receipt)
        else:
            receipt.write_text("stale", encoding="utf-8")
        bin_dir = fake_bin
        if rm_mode:
            bin_dir = run_root / "bin"
            bin_dir.mkdir()
            (bin_dir / "uv").write_text((fake_bin / "uv").read_text(encoding="utf-8"), encoding="utf-8")
            (bin_dir / "uv").chmod(0o755)
            (bin_dir / "rm").write_text("#!/usr/bin/env bash\n" + ("exit 0\n" if rm_mode == "keep" else "exit 1\n"), encoding="utf-8")
            (bin_dir / "rm").chmod(0o755)
        output = run_root / "github-output"
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output)}
        result = subprocess.run([str(root / "scripts" / "workflow_wrapper.sh")], cwd=run_root, env=env, text=True, capture_output=True, check=False)
        return result, output, receipt

    for kind in ("symlink", "hardlink", "directory", "fifo"):
        result, output, _ = run_with_receipt(kind)
        assert result.returncode == 0
        assert _values(output) == {"artifact_ready": "false", "exit_code": "4"}
    result, output, receipt = run_with_receipt("ordinary")
    assert result.returncode == 0 and not receipt.exists()
    assert _values(output) == {"artifact_ready": "false", "exit_code": "2"}
    result, output, receipt = run_with_receipt("rm_failure", "fail")
    assert result.returncode == 0 and receipt.exists()
    assert _values(output) == {"artifact_ready": "false", "exit_code": "4"}
    result, output, receipt = run_with_receipt("rm_keeps", "keep")
    assert result.returncode == 0 and receipt.exists()
    assert _values(output) == {"artifact_ready": "false", "exit_code": "4"}
