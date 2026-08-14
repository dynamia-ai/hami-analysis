import json
from pathlib import Path
import subprocess
import sys

from test_skill_run_manifest import (
    AUDITABLE_EVIDENCE,
    _reader_view_output_bytes,
    _record,
)


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "weekly-hami-org-highlights"
    / "scripts"
    / "rebuild_selection_ledger_from_reader.py"
)


def _run_rebuild(tmp_path: Path, records: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / "evidence.md"
    pool = tmp_path / "candidate-pool.json"
    ledger = tmp_path / "selection-ledger.jsonl"
    output = tmp_path / "rebuilt-ledger.jsonl"
    diff_output = tmp_path / "reader-diff.tsv"
    evidence.write_text(AUDITABLE_EVIDENCE, encoding="utf-8")
    pool.write_text(
        json.dumps(
            {
                "candidates": [
                    {"id": "Project-HAMi/HAMi#1", "kind": "issue"},
                    {"id": "Project-HAMi/HAMi#2", "kind": "pull_request"},
                    {"id": "Project-HAMi/HAMi#4", "kind": "issue"},
                ]
            }
        ),
        encoding="utf-8",
    )
    ledger.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(evidence),
            "--pool",
            str(pool),
            "--ledger",
            str(ledger),
            "--output",
            str(output),
            "--diff-output",
            str(diff_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_rebuild_preserves_declared_views_and_contribution_gate_fields(
    tmp_path: Path,
) -> None:
    records = [
        _record("Project-HAMi/HAMi#1"),
        _record("Project-HAMi/HAMi#2"),
        _record("Project-HAMi/HAMi#4"),
    ]
    pull_request = records[1]
    pull_request["views_read"] = ["triage", "body"]
    pull_request["contribution_gate_evidence_views"] = ["triage", "body"]
    original_gate_fields = {
        key: pull_request[key]
        for key in (
            "contribution_gate_status",
            "contribution_gate_ids",
            "contribution_gate_reason",
            "contribution_gate_evidence_views",
            "contribution_gate_evidence_urls",
        )
    }
    expected_bytes = _reader_view_output_bytes(
        tmp_path / "measure-triage",
        AUDITABLE_EVIDENCE,
        "pull_request",
        "Project-HAMi/HAMi#2",
        "triage",
    ) + _reader_view_output_bytes(
        tmp_path / "measure-body",
        AUDITABLE_EVIDENCE,
        "pull_request",
        "Project-HAMi/HAMi#2",
        "body",
    )

    result = _run_rebuild(tmp_path, records)

    assert result.returncode == 0, result.stderr
    rebuilt = {
        record["id"]: record
        for record in (
            json.loads(line)
            for line in (tmp_path / "rebuilt-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    }
    rebuilt_pull_request = rebuilt["Project-HAMi/HAMi#2"]
    assert rebuilt_pull_request["views_read"] == ["triage", "body"]
    assert rebuilt_pull_request["bytes_read"] == expected_bytes
    assert {
        key: rebuilt_pull_request[key] for key in original_gate_fields
    } == original_gate_fields


def test_rebuild_rejects_legacy_rows_without_contribution_gate_fields(
    tmp_path: Path,
) -> None:
    records = [
        _record("Project-HAMi/HAMi#1"),
        _record("Project-HAMi/HAMi#2"),
        _record("Project-HAMi/HAMi#4"),
    ]
    records[1].pop("contribution_gate_status")

    result = _run_rebuild(tmp_path, records)

    assert result.returncode != 0
    assert "missing Contribution Gate fields" in result.stderr
