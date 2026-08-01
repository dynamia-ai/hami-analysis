import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys

from test_skill_report_validator import EVIDENCE, VALID_REPORT


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "weekly-hami-org-highlights"
    / "scripts"
    / "write_run_manifest.py"
)


def _auditable_issue(item_id: str) -> str:
    number = item_id.rsplit("#", 1)[1]
    return f"""<!-- ITEM_START issue {item_id} -->
### Issue {number}
#### Metadata
- URL: https://github.com/Project-HAMi/HAMi/issues/{number}
#### Activity During Scan Period
- Human comment activity: `1`
- Bot comment activity: `0`
- Maintainer/member/collaborator comment activity: `1`
#### Labels, Assignees and Milestone
- None.
#### Body
- None.
#### Previous Context
- None.
#### Comments During Scan Period
- None.
#### Latest Human Comment
- None.
#### Latest Maintainer Comment
- None.
#### Data Gaps
- None.
<!-- ITEM_END issue {item_id} -->
"""


def _auditable_pull_request(item_id: str) -> str:
    number = item_id.rsplit("#", 1)[1]
    return f"""<!-- ITEM_START pull_request {item_id} -->
### Pull request {number}
#### Metadata
- URL: https://github.com/Project-HAMi/HAMi/pull/{number}
#### Activity During Scan Period
- Human activity: `1`
- Bot activity: `0`
- Maintainer/member/collaborator activity: `1`
#### Current Review Information
- None.
#### Labels, Assignees and Requested Reviewers
- None.
#### Change Size
- None.
#### Body
- None.
#### Previous Context
- None.
#### Conversation Comments During Scan Period
- None.
#### Reviews During Scan Period
- None.
#### Review Comments During Scan Period
- None.
#### Latest Human Activity
- None.
#### Latest Maintainer Activity
- None.
#### Data Gaps
- None.
<!-- ITEM_END pull_request {item_id} -->
"""


AUDITABLE_EVIDENCE = (
    EVIDENCE.replace(
        "<!-- ITEM_START issue Project-HAMi/HAMi#1 -->\n"
        "- URL: https://github.com/Project-HAMi/HAMi/issues/1\n"
        "<!-- ITEM_END issue Project-HAMi/HAMi#1 -->\n",
        _auditable_issue("Project-HAMi/HAMi#1"),
    )
    .replace(
        "<!-- ITEM_START pull_request Project-HAMi/HAMi#2 -->\n"
        "- URL: https://github.com/Project-HAMi/HAMi/pull/2\n"
        "<!-- ITEM_END pull_request Project-HAMi/HAMi#2 -->\n",
        _auditable_pull_request("Project-HAMi/HAMi#2"),
    )
    .replace(
        "<!-- ITEM_START issue Project-HAMi/HAMi#4 -->\n"
        "- URL: https://github.com/Project-HAMi/HAMi/issues/4\n"
        "<!-- ITEM_END issue Project-HAMi/HAMi#4 -->\n",
        _auditable_issue("Project-HAMi/HAMi#4"),
    )
)

TRIAGE_METRICS = {
    "Project-HAMi/HAMi#1": {
        "bytes_read": 741,
        "chunks_complete": True,
        "human_count": 1,
        "maintainer_count": 1,
        "bot_count": 0,
    },
    "Project-HAMi/HAMi#2": {
        "bytes_read": 820,
        "chunks_complete": True,
        "human_count": 1,
        "maintainer_count": 1,
        "bot_count": 0,
    },
    "Project-HAMi/HAMi#4": {
        "bytes_read": 741,
        "chunks_complete": True,
        "human_count": 1,
        "maintainer_count": 1,
        "bot_count": 0,
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(item_id: str, section: str | None = None, rank: int | None = None) -> dict[str, object]:
    section = section or {
        "Project-HAMi/HAMi#1": "Must Pay Attention",
        "Project-HAMi/HAMi#2": "Pull Requests Requiring Action",
        "Project-HAMi/HAMi#4": "Recommended Resource Allocation",
    }[item_id]
    return {
        "id": item_id,
        "index_signals": ["human activity"],
        "views_read": ["triage"],
        **TRIAGE_METRICS[item_id],
        "impact": "high",
        "urgency": "high",
        "confidence": "medium",
        "selected_section": section,
        "rank": None if section == "rejected" else rank or 1,
        "rejection_reason": "not selected after triage" if section == "rejected" else None,
    }


def _run(
    tmp_path: Path,
    ledger: str,
    *,
    evidence_content: str = AUDITABLE_EVIDENCE,
    style_report_sha256: str | None = None,
    style_skill_sha256: str | None = None,
    input_report_path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / "evidence.md"
    report = tmp_path / "report.md"
    ledger_path = tmp_path / "selection-ledger.jsonl"
    index_trace = tmp_path / "index-trace.jsonl"
    candidate_pool = tmp_path / "candidate-pool.json"
    style_skill = tmp_path / "Tech-Doc-Style-Chinese" / "SKILL.md"
    lint_script = style_skill.parent / "scripts" / "lint_copy_rules.py"
    input_report = tmp_path / "pre-tech-doc-report.md"
    polish_review = tmp_path / "polish-review.json"
    output = tmp_path / "manifest.json"
    evidence.write_text(evidence_content)
    report.write_text(VALID_REPORT)
    input_report.write_text(VALID_REPORT)
    ledger_path.write_text(ledger)
    index_trace.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "view": "index",
                "evidence_sha256": _sha256(evidence),
                "kind": "issue",
                "offset": 0,
                "item_ids": ["Project-HAMi/HAMi#1", "Project-HAMi/HAMi#4"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "schema_version": "1.0",
                "view": "index",
                "evidence_sha256": _sha256(evidence),
                "kind": "pull_request",
                "offset": 0,
                "item_ids": ["Project-HAMi/HAMi#2"],
            }
        )
        + "\n"
    )
    candidate_pool.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "created_at": "2026-08-01T00:00:00+00:00",
                "evidence": {"sha256": _sha256(evidence)},
                "index_trace": {"sha256": _sha256(index_trace)},
                "candidates": [
                    {"id": "Project-HAMi/HAMi#1", "kind": "issue"},
                    {"id": "Project-HAMi/HAMi#2", "kind": "pull_request"},
                    {"id": "Project-HAMi/HAMi#4", "kind": "issue"},
                ],
            }
        )
    )
    style_skill.parent.mkdir()
    style_skill.write_text("---\nname: tech-doc-style-chinese\n---\n")
    lint_script.parent.mkdir()
    lint_script.write_text("import sys\nsys.exit(0)\n")
    polish_review.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "completed_at": "2026-08-01T00:00:00+00:00",
                "review_method": "manual review following Tech-Doc-Style-Chinese",
                "style_skill": {
                    "name": "tech-doc-style-chinese",
                    "path": str(style_skill),
                    "sha256": style_skill_sha256 or _sha256(style_skill),
                },
                "input_report": {
                    "path": input_report_path or str(input_report),
                    "sha256": _sha256(input_report),
                },
                "report": {"sha256": style_report_sha256 or _sha256(report)},
                "scope": {},
            }
        )
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(evidence),
            "--report",
            str(report),
            "--ledger",
            str(ledger_path),
            "--candidate-pool",
            str(candidate_pool),
            "--index-trace",
            str(index_trace),
            "--polish-review",
            str(polish_review),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _reader_view_output_bytes(
    tmp_path: Path, evidence_content: str, kind: str, item_id: str, view: str
) -> int:
    evidence = tmp_path / "measurement" / "evidence.md"
    evidence.parent.mkdir()
    evidence.write_text(evidence_content)
    total = 0
    chunk = 1
    while True:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT.with_name("evidence_reader.py")),
                "item",
                kind,
                item_id,
                "--view",
                view,
                "--chunk",
                str(chunk),
                "--max-bytes",
                "20000",
                str(evidence),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        status = re.fullmatch(
            r"chunk (?P<chunk>\d+)/(?P<count>\d+); output bytes: (?P<bytes>\d+); "
            r"view bytes: \d+; (?P<tail>.*)",
            result.stderr.strip(),
        )
        assert status is not None
        assert int(status.group("chunk")) == chunk
        total += int(status.group("bytes"))
        if chunk == int(status.group("count")):
            assert status.group("tail") == "end of item view"
            return total
        chunk += 1


def test_run_manifest_records_hashes_and_requires_report_selection(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "".join(json.dumps(_record(item_id)) + "\n" for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")),
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["schema_version"] == "1.3"
    assert len(manifest["evidence"]["sha256"]) == 64
    assert manifest["selection_ledger"]["records"] == 3
    assert manifest["style_review"]["record"]["style_skill"]["name"] == "tech-doc-style-chinese"
    assert manifest["validator"]["result"] == "passed"
    assert len(manifest["validator"]["sha256"]) == 64
    assert manifest["style_review"]["record"]["lint"]["exit_code"] == 0
    assert len(manifest["style_review"]["record"]["lint"]["sha256"]) == 64
    assert manifest["triage_replay"] == {
        "script": str(SCRIPT.with_name("evidence_reader.py")),
        "sha256": _sha256(SCRIPT.with_name("evidence_reader.py")),
        "view": "triage",
        "max_bytes": 20_000,
        "checked_records": 3,
        "checked_views": 3,
        "result": "passed",
    }
    assert manifest["collector_started_worktree"]["head"] == "a" * 40
    assert manifest["collector_started_worktree"]["dirty"] is False
    assert "worktree_snapshot_sha256" in manifest["skill_worktree"]


def test_run_manifest_rejects_missing_selected_report_item(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "".join(
            json.dumps(record) + "\n"
            for record in (
                _record("Project-HAMi/HAMi#1", section="rejected"),
                _record("Project-HAMi/HAMi#2"),
                _record("Project-HAMi/HAMi#4"),
            )
        ),
    )

    assert result.returncode != 0
    assert "rejected ledger entries are referenced by report" in result.stderr


def test_run_manifest_requires_ledger_section_and_rank_to_match_report(tmp_path: Path) -> None:
    records = (
        _record("Project-HAMi/HAMi#1", section="Must Pay Attention", rank=1),
        _record("Project-HAMi/HAMi#2", section="Must Pay Attention", rank=1),
        _record("Project-HAMi/HAMi#4", section="Recommended Resource Allocation", rank=2),
    )
    result = _run(tmp_path, "".join(json.dumps(record) + "\n" for record in records))

    assert result.returncode != 0
    assert "ledger placement for Project-HAMi/HAMi#2" in result.stderr
    assert "ledger placement for Project-HAMi/HAMi#4" in result.stderr


def test_run_manifest_requires_exactly_one_ledger_record_per_candidate_pool_item(tmp_path: Path) -> None:
    result = _run(tmp_path, json.dumps(_record("Project-HAMi/HAMi#2")) + "\n")

    assert result.returncode != 0
    assert "missing candidate-pool items" in result.stderr


def test_run_manifest_rejects_style_review_for_a_different_report_hash(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "".join(json.dumps(_record(item_id)) + "\n" for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")),
        style_report_sha256="0" * 64,
    )

    assert result.returncode != 0
    assert "does not attest to the final report SHA-256" in result.stderr


def test_run_manifest_rejects_missing_style_review_input_path(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "".join(json.dumps(_record(item_id)) + "\n" for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")),
        input_report_path="missing-draft.md",
    )

    assert result.returncode != 0
    assert "input report path does not exist" in result.stderr


def test_run_manifest_rejects_mismatched_style_skill_hash(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "".join(json.dumps(_record(item_id)) + "\n" for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")),
        style_skill_sha256="0" * 64,
    )

    assert result.returncode != 0
    assert "style skill path does not match its SHA-256" in result.stderr


def test_run_manifest_rejects_ledger_bytes_that_do_not_match_reader_output(tmp_path: Path) -> None:
    records = [_record(item_id) for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")]
    records[0]["bytes_read"] = 1

    result = _run(tmp_path, "".join(json.dumps(record) + "\n" for record in records))

    assert result.returncode != 0
    assert "Project-HAMi/HAMi#1 bytes_read: ledger=1, reader=741" in result.stderr


def test_run_manifest_rejects_ledger_activity_or_chunk_values_that_do_not_match_reader(tmp_path: Path) -> None:
    records = [_record(item_id) for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")]
    records[0]["maintainer_count"] = 0
    records[1]["chunks_complete"] = False

    result = _run(tmp_path, "".join(json.dumps(record) + "\n" for record in records))

    assert result.returncode != 0
    assert "Project-HAMi/HAMi#1 maintainer_count: ledger=0, reader=1" in result.stderr
    assert "Project-HAMi/HAMi#2 chunks_complete: ledger=False, reader=True" in result.stderr


def test_run_manifest_sums_only_declared_per_item_views(tmp_path: Path) -> None:
    records = [_record(item_id) for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")]
    records[0]["views_read"] = ["triage", "body"]
    records[0]["bytes_read"] = 741 + 301

    result = _run(tmp_path, "".join(json.dumps(record) + "\n" for record in records))

    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["triage_replay"]["checked_views"] == 4


def test_run_manifest_sums_all_reader_chunks_for_each_declared_view(tmp_path: Path) -> None:
    long_evidence = AUDITABLE_EVIDENCE.replace(
        "#### Body\n- None.\n#### Previous Context",
        "#### Body\n" + "x" * 20_000 + "\n#### Previous Context",
        1,
    )
    records = [_record(item_id) for item_id in ("Project-HAMi/HAMi#1", "Project-HAMi/HAMi#2", "Project-HAMi/HAMi#4")]
    records[0]["views_read"] = ["triage", "body"]
    records[0]["bytes_read"] = TRIAGE_METRICS["Project-HAMi/HAMi#1"]["bytes_read"] + _reader_view_output_bytes(
        tmp_path,
        long_evidence,
        "issue",
        "Project-HAMi/HAMi#1",
        "body",
    )

    result = _run(
        tmp_path,
        "".join(json.dumps(record) + "\n" for record in records),
        evidence_content=long_evidence,
    )

    assert result.returncode == 0, result.stderr
