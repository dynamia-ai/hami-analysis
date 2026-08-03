#!/usr/bin/env python3
"""Validate a report selection ledger and write a reproducibility manifest."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


LEDGER_FIELDS = {
    "id",
    "index_signals",
    "views_read",
    "bytes_read",
    "chunks_complete",
    "human_count",
    "maintainer_count",
    "bot_count",
    "impact",
    "urgency",
    "confidence",
    "selected_section",
    "rank",
    "rejection_reason",
}
REPORT_ID_RE = re.compile(r"\[Project-HAMi/[A-Za-z0-9_.-]+#\d+\]\(")
ORDERED_REPORT_ENTRY_RE = re.compile(r"^(?P<rank>[1-9]\d*)\.\s+\*\*(?P<body>.*)$")
EVIDENCE_ID_RE = re.compile(
    r"^<!-- ITEM_START (?:issue|pull_request) (?P<id>Project-HAMi/[A-Za-z0-9_.-]+#\d+) -->[ \t]*$",
    re.MULTILINE,
)
EVIDENCE_KIND_RE = re.compile(
    r"^<!-- ITEM_START (?P<kind>issue|pull_request) (?P<id>Project-HAMi/[A-Za-z0-9_.-]+#\d+) -->[ \t]*$",
    re.MULTILINE,
)
POLISH_REVIEW_FIELDS = {
    "schema_version",
    "completed_at",
    "review_method",
    "style_skill",
    "input_report",
    "report",
    "scope",
}
POOL_FIELDS = {"schema_version", "created_at", "evidence", "index_trace", "candidates"}
STYLE_SKILL_NAME = "tech-doc-style-chinese"
RISK_LEVELS = {"low", "medium", "high", "not assessed"}
TRIAGE_MAX_BYTES = 20_000
SUBPROCESS_TIMEOUT_SECONDS = 60.0
MAX_PER_KIND = 24
TRIAGE_STDERR_RE = re.compile(
    r"\Achunk (?P<chunk>[1-9]\d*)/(?P<count>[1-9]\d*); "
    r"output bytes: (?P<output_bytes>\d+); view bytes: (?P<view_bytes>\d+)"
    r"(?P<completion>; (?:use --chunk [1-9]\d* for the next chunk|end of item view))\Z"
)
TRIAGE_ACTIVITY_HEADINGS = {
    "issue": (
        "#### Activity During Scan Period",
        "#### Labels, Assignees and Milestone",
        {
            "human_count": "Human comment activity",
            "maintainer_count": "Maintainer/member/collaborator comment activity",
            "bot_count": "Bot comment activity",
        },
    ),
    "pull_request": (
        "#### Activity During Scan Period",
        "#### Current Review Information",
        {
            "human_count": "Human activity",
            "maintainer_count": "Maintainer/member/collaborator activity",
            "bot_count": "Bot activity",
        },
    ),
}
REPORT_SECTIONS = {
    "Must Pay Attention",
    "Worth Engineering Investment",
    "Pull Requests Requiring Action",
    "Important Resolutions",
    "Emerging Engineering Themes",
    "Recommended Resource Allocation",
    "Active but Not Worth Investing This Week",
}
DETAIL_REPORT_SECTIONS = REPORT_SECTIONS


class ManifestError(ValueError):
    """Raised for invalid audit inputs."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _report_ids(path: Path) -> set[str]:
    return {
        match.group(0)[1:].split("]", 1)[0]
        for match in REPORT_ID_RE.finditer(path.read_text(encoding="utf-8"))
    }


def _report_selection_positions(path: Path) -> dict[str, tuple[str, int]]:
    """Return the detailed analytic report placement for each selected item."""
    positions: dict[str, tuple[str, int]] = {}
    section: str | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.rstrip()
        if line.startswith("## "):
            heading = line[3:]
            section = heading if heading in DETAIL_REPORT_SECTIONS else None
            continue
        if section is None:
            continue
        entry = ORDERED_REPORT_ENTRY_RE.fullmatch(line)
        if entry is None:
            continue
        rank = int(entry.group("rank"))
        item_ids = {
            match.group(0)[1:].split("]", 1)[0]
            for match in REPORT_ID_RE.finditer(entry.group("body"))
        }
        for item_id in item_ids:
            if item_id in positions:
                raise ManifestError(
                    f"report item {item_id} is selected more than once in detailed analytic sections"
                )
            positions[item_id] = (section, rank)
    return positions


def _validate_ledger_report_placements(records: list[dict[str, object]], report: Path) -> None:
    report_ids = _report_ids(report)
    rejected_ids = {
        str(record["id"])
        for record in records
        if record.get("selected_section") == "rejected"
    }
    referenced_rejected = sorted(report_ids & rejected_ids)
    if referenced_rejected:
        raise ManifestError("rejected ledger entries are referenced by report: " + ", ".join(referenced_rejected))

    placements = _report_selection_positions(report)
    problems: list[str] = []
    for record in records:
        item_id = str(record["id"])
        section = record.get("selected_section")
        if section == "rejected":
            continue
        placement = placements.get(item_id)
        if placement is None:
            problems.append(f"selected ledger item is absent from a detailed analytic report entry: {item_id}")
            continue
        expected = (section, record.get("rank"))
        if placement != expected:
            problems.append(
                f"ledger placement for {item_id} is {expected[0]!r} rank {expected[1]}, "
                f"but report has {placement[0]!r} rank {placement[1]}"
            )
    if problems:
        raise ManifestError("; ".join(problems))


def _evidence_ids(path: Path) -> set[str]:
    return set(EVIDENCE_ID_RE.findall(path.read_text(encoding="utf-8")))


def _evidence_items_by_kind(path: Path) -> dict[str, list[str]]:
    result = {"issue": [], "pull_request": []}
    for match in EVIDENCE_KIND_RE.finditer(path.read_text(encoding="utf-8")):
        result[match.group("kind")].append(match.group("id"))
    if len(result["issue"]) != len(set(result["issue"])) or len(result["pull_request"]) != len(set(result["pull_request"])):
        raise ManifestError("evidence contains duplicate ITEM_START IDs")
    return result


def _collector_snapshot_digest(fields: dict[str, str]) -> str:
    material = {
        "dirty": fields["collector_started_worktree_dirty"] == "true",
        "head": fields["collector_started_worktree_head"],
        "tracked_diff_sha256": fields["collector_started_worktree_tracked_diff_sha256"],
        "untracked_sha256": fields["collector_started_worktree_untracked_sha256"],
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _evidence_front_matter(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", content, re.DOTALL)
    if match is None:
        raise ManifestError("evidence front matter is missing")
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip().strip('"')
    snapshot = fields.get("collector_started_worktree_snapshot_sha256")
    if not snapshot or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
        raise ManifestError("evidence collector-start worktree snapshot is missing or invalid")
    head = fields.get("collector_started_worktree_head")
    if not head or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head):
        raise ManifestError("evidence collector-start Git HEAD is missing or invalid")
    dirty = fields.get("collector_started_worktree_dirty")
    if dirty not in {"true", "false"}:
        raise ManifestError("evidence collector-start dirty state is missing or invalid")
    for field in (
        "collector_started_worktree_tracked_diff_sha256",
        "collector_started_worktree_untracked_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", fields.get(field, "")):
            raise ManifestError(f"evidence {field} is missing or invalid")
    if snapshot != _collector_snapshot_digest(fields):
        raise ManifestError("evidence collector-start worktree snapshot does not match its components")
    return fields


def _read_ledger(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ManifestError(f"ledger line {line_number} is not valid JSON: {error.msg}") from error
        if not isinstance(record, dict):
            raise ManifestError(f"ledger line {line_number} must be a JSON object")
        missing = sorted(LEDGER_FIELDS - record.keys())
        if missing:
            raise ManifestError(f"ledger line {line_number} is missing fields: {', '.join(missing)}")
        if not isinstance(record.get("id"), str):
            raise ManifestError(f"ledger line {line_number} has a non-string id")
        for field in ("index_signals", "views_read"):
            value = record.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                raise ManifestError(f"ledger line {line_number} has an invalid {field}")
        if "triage" not in record["views_read"]:
            raise ManifestError(f"ledger line {line_number} must record a triage view")
        if not isinstance(record.get("bytes_read"), int) or isinstance(record["bytes_read"], bool) or record["bytes_read"] < 0:
            raise ManifestError(f"ledger line {line_number} has an invalid bytes_read")
        if not isinstance(record.get("chunks_complete"), bool):
            raise ManifestError(f"ledger line {line_number} has an invalid chunks_complete")
        for field in ("human_count", "maintainer_count", "bot_count"):
            value = record.get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ManifestError(f"ledger line {line_number} has an invalid {field}")
        if record.get("confidence") not in {"low", "medium", "high"}:
            raise ManifestError(f"ledger line {line_number} has an invalid confidence")
        for field in ("impact", "urgency"):
            if record.get(field) not in RISK_LEVELS:
                raise ManifestError(f"ledger line {line_number} has an invalid {field}")
        section = record.get("selected_section")
        if section not in REPORT_SECTIONS | {"rejected"}:
            raise ManifestError(f"ledger line {line_number} has an invalid selected_section")
        rank = record.get("rank")
        if section == "rejected":
            if rank is not None or not isinstance(record.get("rejection_reason"), str) or not record["rejection_reason"]:
                raise ManifestError(f"ledger line {line_number} has an invalid rejected-item decision")
        elif not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ManifestError(f"ledger line {line_number} has an invalid selected-item rank")
        records.append(record)
    if not records:
        raise ManifestError("selection ledger is empty")
    return records


def _git_output(directory: Path, *arguments: str) -> str | None:
    output = _git_text(directory, *arguments)
    return output or None


def _git_text(directory: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip()


def _git_bytes(directory: Path, *arguments: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=directory,
            check=True,
            capture_output=True,
            text=False,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout


def _git_provenance(directory: Path) -> dict[str, object] | None:
    root = _git_output(directory, "rev-parse", "--show-toplevel")
    if root is None:
        return None
    root_path = Path(root)
    head = _git_output(root_path, "rev-parse", "HEAD")
    status = _git_bytes(root_path, "status", "--porcelain=v1")
    tracked_diff = _git_bytes(root_path, "diff", "--binary", "HEAD")
    untracked = _git_bytes(root_path, "ls-files", "--others", "--exclude-standard", "-z")
    if head is None or status is None or tracked_diff is None or untracked is None:
        return None
    untracked_hashes: list[dict[str, str]] = []
    relative_paths = sorted(
        (os.fsdecode(raw_path) for raw_path in untracked.split(b"\0") if raw_path),
        key=os.fsencode,
    )
    for relative in relative_paths:
        path = root_path / relative
        if path.is_file():
            untracked_hashes.append({"path": relative, "sha256": _sha256(path)})
    tracked_diff_sha256 = hashlib.sha256(tracked_diff).hexdigest()
    untracked_sha256 = hashlib.sha256(
        json.dumps(untracked_hashes, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    dirty = bool(status)
    snapshot = hashlib.sha256(
        json.dumps(
            {
                "dirty": dirty,
                "head": head,
                "tracked_diff_sha256": tracked_diff_sha256,
                "untracked_sha256": untracked_sha256,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "root": str(root_path),
        "head": head,
        "dirty": dirty,
        "tracked_diff_sha256": tracked_diff_sha256,
        "untracked": untracked_hashes,
        "untracked_sha256": untracked_sha256,
        "worktree_snapshot_sha256": snapshot,
    }


def _read_polish_review(path: Path, report: Path) -> dict[str, object]:
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"style review is not valid JSON: {error.msg}") from error
    if not isinstance(review, dict):
        raise ManifestError("style review must be a JSON object")
    missing = sorted(POLISH_REVIEW_FIELDS - review.keys())
    if missing:
        raise ManifestError("style review is missing fields: " + ", ".join(missing))
    if review.get("schema_version") != "1.0":
        raise ManifestError("style review schema_version must be '1.0'")
    style_skill = review.get("style_skill")
    review_report = review.get("report")
    if not isinstance(style_skill, dict) or style_skill.get("name") != STYLE_SKILL_NAME:
        raise ManifestError("style review must identify the tech-doc-style-chinese skill")
    if not isinstance(style_skill.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", style_skill["sha256"]):
        raise ManifestError("style review must include a valid style skill SHA-256")
    input_report = review.get("input_report")
    if not isinstance(input_report, dict) or not isinstance(input_report.get("sha256"), str):
        raise ManifestError("style review must include an input report SHA-256")
    if not isinstance(input_report.get("path"), str) or not input_report["path"]:
        raise ManifestError("style review must include an input report path")
    input_path = _declared_file(path, input_report["path"], "style review input report")
    if input_report["sha256"] != _sha256(input_path):
        raise ManifestError("style review input report path does not match its SHA-256")
    if not isinstance(style_skill.get("path"), str) or not style_skill["path"]:
        raise ManifestError("style review must include a style skill path")
    skill_path = _declared_file(path, style_skill["path"], "style skill")
    if style_skill["sha256"] != _sha256(skill_path):
        raise ManifestError("style review style skill path does not match its SHA-256")
    if _skill_front_matter_name(skill_path) != STYLE_SKILL_NAME:
        raise ManifestError("style skill path does not declare name: tech-doc-style-chinese")
    if not isinstance(review_report, dict) or review_report.get("sha256") != _sha256(report):
        raise ManifestError("style review does not attest to the final report SHA-256")
    review["lint"] = _run_style_lint(skill_path, report)
    return review


def _declared_file(review: Path, raw_path: object, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ManifestError(f"{label} path is missing")
    candidate = Path(raw_path).expanduser()
    path = candidate if candidate.is_absolute() else review.parent / candidate
    if not path.is_file():
        raise ManifestError(f"{label} path does not exist or is not a file: {path}")
    return path.resolve()


def _skill_front_matter_name(path: Path) -> str | None:
    content = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n.*?^name:\s*(?P<name>[^\r\n]+)\r?$", content, re.MULTILINE | re.DOTALL)
    return match.group("name").strip().strip('"\'') if match else None


def _run_style_lint(style_skill: Path, report: Path) -> dict[str, object]:
    script = style_skill.parent / "scripts" / "lint_copy_rules.py"
    if not script.is_file():
        raise ManifestError("style skill does not contain scripts/lint_copy_rules.py")
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(report)],
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestError(f"could not execute Tech-Doc lint: {error}") from error
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip().replace("\n", "; ")[:1_000]
        raise ManifestError(f"Tech-Doc lint failed: {detail or 'no diagnostic'}")
    return {
        "script": str(script),
        "sha256": _sha256(script),
        "exit_code": result.returncode,
        "result": "passed",
    }


def _read_candidate_pool(pool: Path, evidence: Path, index_trace: Path) -> dict[str, str]:
    try:
        data = json.loads(pool.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"candidate pool is not valid JSON: {error.msg}") from error
    if not isinstance(data, dict):
        raise ManifestError("candidate pool must be a JSON object")
    missing = sorted(POOL_FIELDS - data.keys())
    if missing:
        raise ManifestError("candidate pool is missing fields: " + ", ".join(missing))
    if data.get("schema_version") != "1.0":
        raise ManifestError("candidate pool schema_version must be '1.0'")
    traced_by_kind = _validate_index_trace(evidence, index_trace)
    pool_evidence = data.get("evidence")
    pool_trace = data.get("index_trace")
    if not isinstance(pool_evidence, dict) or pool_evidence.get("sha256") != _sha256(evidence):
        raise ManifestError("candidate pool does not match the evidence SHA-256")
    if not isinstance(pool_trace, dict) or pool_trace.get("sha256") != _sha256(index_trace):
        raise ManifestError("candidate pool does not match the index trace SHA-256")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ManifestError("candidate pool must contain candidates")
    ids: list[str] = []
    kind_counts = {"issue": 0, "pull_request": 0}
    evidence_by_kind = _evidence_items_by_kind(evidence)
    evidence_ids = set(evidence_by_kind["issue"] + evidence_by_kind["pull_request"])
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
            raise ManifestError("candidate pool entries must contain a string id")
        item_id = candidate["id"]
        kind = candidate.get("kind")
        if kind not in kind_counts:
            raise ManifestError(f"candidate pool item {item_id!r} has an invalid kind")
        if item_id not in evidence_ids:
            raise ManifestError(f"candidate pool item {item_id!r} is absent from evidence")
        if item_id not in traced_by_kind[kind]:
            raise ManifestError(f"candidate pool item {item_id!r} is not present in the matching traced index")
        kind_counts[kind] += 1
        ids.append(item_id)
    if len(ids) != len(set(ids)):
        raise ManifestError("candidate pool contains duplicate item IDs")
    for kind, count in kind_counts.items():
        if count > MAX_PER_KIND:
            raise ManifestError(f"candidate pool has {count} {kind} items; maximum is {MAX_PER_KIND}")
    return {candidate["id"]: candidate["kind"] for candidate in candidates}


def _validate_index_trace(evidence: Path, index_trace: Path) -> dict[str, set[str]]:
    expected = _evidence_items_by_kind(evidence)
    progress = {"issue": 0, "pull_request": 0}
    try:
        lines = index_trace.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ManifestError(f"cannot read index trace: {error}") from error
    if not lines:
        raise ManifestError("index trace is empty")
    evidence_sha256 = _sha256(evidence)
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ManifestError(f"index trace line {line_number} is not valid JSON: {error.msg}") from error
        if not isinstance(record, dict) or record.get("schema_version") != "1.0" or record.get("view") != "index":
            raise ManifestError(f"index trace line {line_number} has an invalid schema")
        kind = record.get("kind")
        offset = record.get("offset")
        item_ids = record.get("item_ids")
        if kind not in progress or not isinstance(offset, int) or isinstance(offset, bool):
            raise ManifestError(f"index trace line {line_number} has an invalid kind or offset")
        if record.get("evidence_sha256") != evidence_sha256:
            raise ManifestError(f"index trace line {line_number} does not match the evidence SHA-256")
        if not isinstance(item_ids, list) or not item_ids or not all(isinstance(item_id, str) for item_id in item_ids):
            raise ManifestError(f"index trace line {line_number} has invalid item_ids")
        if offset != progress[kind]:
            raise ManifestError(f"index trace line {line_number} is non-contiguous or overlaps a prior page")
        expected_page = expected[kind][offset : offset + len(item_ids)]
        if item_ids != expected_page:
            raise ManifestError(f"index trace line {line_number} does not match the expected index page")
        progress[kind] += len(item_ids)
    for kind, total in ((kind, len(items)) for kind, items in expected.items()):
        if progress[kind] != total:
            raise ManifestError(f"index trace does not completely cover the {kind} index")
    return {kind: set(items) for kind, items in expected.items()}


def _run_validator(report: Path, evidence: Path) -> dict[str, str]:
    script = Path(__file__).with_name("validate_report.py")
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(report), str(evidence)],
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestError(f"could not execute report validator: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip().replace("\n", "; ")[:1_000]
        raise ManifestError(f"report validator failed: {detail or 'no diagnostic'}")
    return {"script": str(script), "sha256": _sha256(script), "result": "passed"}


def _triage_activity_counts(output: bytes, kind: str, item_id: str) -> dict[str, int]:
    """Extract the three activity counts from the reader's bounded triage response."""
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError(f"triage replay for {item_id} did not return UTF-8 output") from error
    activity_heading, following_heading, labels = TRIAGE_ACTIVITY_HEADINGS[kind]
    start = text.find(activity_heading)
    if start < 0:
        raise ManifestError(f"triage replay for {item_id} is missing the activity section")
    end = text.find(following_heading, start + len(activity_heading))
    if end < 0:
        raise ManifestError(f"triage replay for {item_id} is missing the section after activity")
    activity = text[start:end]
    values: dict[str, int] = {}
    for field, label in labels.items():
        matches = re.findall(rf"(?m)^- {re.escape(label)}: `(\d+)`\s*$", activity)
        if len(matches) != 1:
            raise ManifestError(
                f"triage replay for {item_id} has {len(matches)} {label!r} values; expected exactly one"
            )
        values[field] = int(matches[0])
    return values


def _run_reader_chunk(
    evidence: Path, kind: str, item_id: str, view: str, chunk: int
) -> tuple[bytes, re.Match[str]]:
    """Run one bounded reader chunk and prove its emitted byte measurement."""
    script = Path(__file__).with_name("evidence_reader.py")
    command = [
        sys.executable,
        str(script),
        "item",
        kind,
        item_id,
        "--view",
        view,
        "--chunk",
        str(chunk),
        "--max-bytes",
        str(TRIAGE_MAX_BYTES),
        str(evidence),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestError(f"could not execute evidence reader for {item_id}: {error}") from error
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        detail = stderr.replace("\n", "; ")[:1_000]
        raise ManifestError(f"triage replay failed for {item_id}: {detail or 'no diagnostic'}")
    status = TRIAGE_STDERR_RE.fullmatch(stderr)
    if status is None:
        raise ManifestError(f"triage replay for {item_id} returned an unrecognized reader status: {stderr[:1_000]}")
    output_bytes = int(status.group("output_bytes"))
    if output_bytes != len(result.stdout):
        raise ManifestError(
            f"triage replay for {item_id} reported {output_bytes} output bytes, "
            f"but emitted {len(result.stdout)}"
        )
    return result.stdout, status


def _replay_reader_view(evidence: Path, kind: str, item_id: str, view: str) -> tuple[bytes, int, bool]:
    """Replay every chunk of one declared item view and return its audit values."""
    first_output, first_status = _run_reader_chunk(evidence, kind, item_id, view, 1)
    total_chunks = int(first_status.group("count"))
    outputs = [first_output]
    output_bytes = int(first_status.group("output_bytes"))
    final_status = first_status
    for chunk in range(2, total_chunks + 1):
        output, status = _run_reader_chunk(evidence, kind, item_id, view, chunk)
        if int(status.group("chunk")) != chunk or int(status.group("count")) != total_chunks:
            raise ManifestError(f"triage replay for {item_id} returned inconsistent chunk metadata")
        outputs.append(output)
        output_bytes += int(status.group("output_bytes"))
        final_status = status
    return (
        b"".join(outputs),
        output_bytes,
        final_status.group("chunk") == final_status.group("count")
        and final_status.group("completion") == "; end of item view",
    )


def _validate_ledger_triage_replays(
    records: list[dict[str, object]], evidence: Path, candidate_kinds: dict[str, str]
) -> dict[str, object]:
    """Require every ledger row to match the reader output, not a claimed value."""
    problems: list[str] = []
    checked_views = 0
    for record in records:
        item_id = str(record["id"])
        kind = candidate_kinds[item_id]
        views = record["views_read"]
        if len(views) != len(set(views)):
            raise ManifestError(f"selection ledger item {item_id} lists a view more than once")
        replays = {
            view: _replay_reader_view(evidence, kind, item_id, view)
            for view in views
        }
        checked_views += len(replays)
        triage_output, _, _ = replays["triage"]
        replay = {
            "bytes_read": sum(output_bytes for _, output_bytes, _ in replays.values()),
            "chunks_complete": all(complete for _, _, complete in replays.values()),
            **_triage_activity_counts(triage_output, kind, item_id),
        }
        for field, actual in replay.items():
            if record.get(field) != actual:
                problems.append(f"{item_id} {field}: ledger={record.get(field)!r}, reader={actual!r}")
    if problems:
        raise ManifestError("selection ledger does not match evidence_reader triage replay: " + "; ".join(problems))
    script = Path(__file__).with_name("evidence_reader.py")
    return {
        "script": str(script),
        "sha256": _sha256(script),
        "view": "triage",
        "max_bytes": TRIAGE_MAX_BYTES,
        "checked_records": len(records),
        "checked_views": checked_views,
        "result": "passed",
    }


def write_manifest(
    evidence: Path,
    report: Path,
    ledger: Path,
    candidate_pool: Path,
    index_trace: Path,
    polish_review: Path,
    output: Path,
) -> None:
    validator = _run_validator(report, evidence)
    evidence_header = _evidence_front_matter(evidence)
    records = _read_ledger(ledger)
    report_ids = _report_ids(report)
    candidate_kinds = _read_candidate_pool(candidate_pool, evidence, index_trace)
    candidate_ids = set(candidate_kinds)
    ledger_ids = [str(record["id"]) for record in records]
    if len(ledger_ids) != len(set(ledger_ids)):
        raise ManifestError("selection ledger contains duplicate item IDs")
    missing_ledger = sorted(candidate_ids - set(ledger_ids))
    unexpected_ledger = sorted(set(ledger_ids) - candidate_ids)
    coverage_errors: list[str] = []
    if missing_ledger:
        coverage_errors.append("selection ledger is missing candidate-pool items: " + ", ".join(missing_ledger))
    if unexpected_ledger:
        coverage_errors.append("selection ledger has IDs absent from the candidate pool: " + ", ".join(unexpected_ledger))
    if coverage_errors:
        raise ManifestError("; ".join(coverage_errors))
    _validate_ledger_report_placements(records, report)
    selected_ids = {
        record["id"]
        for record in records
        if record.get("selected_section") not in (None, "", "rejected")
    }
    missing = sorted(report_ids - selected_ids)
    if missing:
        raise ManifestError("selection ledger is missing report references: " + ", ".join(missing))
    triage_replay = _validate_ledger_triage_replays(records, evidence, candidate_kinds)
    review = _read_polish_review(polish_review, report)
    manifest = {
        "schema_version": "1.3",
        "generated_at": datetime.now(UTC).isoformat(),
        "evidence": {"path": str(evidence), "sha256": _sha256(evidence)},
        "collector_started_worktree_snapshot_sha256": evidence_header[
            "collector_started_worktree_snapshot_sha256"
        ],
        "collector_started_worktree": {
            "head": evidence_header["collector_started_worktree_head"],
            "dirty": evidence_header["collector_started_worktree_dirty"] == "true",
            "tracked_diff_sha256": evidence_header[
                "collector_started_worktree_tracked_diff_sha256"
            ],
            "untracked_sha256": evidence_header[
                "collector_started_worktree_untracked_sha256"
            ],
            "worktree_snapshot_sha256": evidence_header[
                "collector_started_worktree_snapshot_sha256"
            ],
        },
        "report": {"path": str(report), "sha256": _sha256(report)},
        "selection_ledger": {"path": str(ledger), "sha256": _sha256(ledger), "records": len(records)},
        "candidate_pool": {"path": str(candidate_pool), "sha256": _sha256(candidate_pool)},
        "index_trace": {"path": str(index_trace), "sha256": _sha256(index_trace)},
        "style_review": {"path": str(polish_review), "sha256": _sha256(polish_review), "record": review},
        "collector_worktree": _git_provenance(evidence.parent),
        "skill_worktree": _git_provenance(Path(__file__).resolve().parent),
        "validator": validator,
        "triage_replay": triage_replay,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--candidate-pool", type=Path, required=True)
    parser.add_argument("--index-trace", type=Path, required=True)
    parser.add_argument("--polish-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        write_manifest(
            args.evidence,
            args.report,
            args.ledger,
            args.candidate_pool,
            args.index_trace,
            args.polish_review,
            args.output,
        )
    except (ManifestError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote run manifest: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
