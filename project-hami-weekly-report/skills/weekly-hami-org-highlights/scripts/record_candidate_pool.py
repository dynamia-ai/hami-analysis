#!/usr/bin/env python3
"""Record a candidate pool selected from a complete evidence index-read trace."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sys


EVIDENCE_ID_RE = re.compile(
    r"^<!-- ITEM_START (?P<kind>issue|pull_request) (?P<id>Project-HAMi/[A-Za-z0-9_.-]+#\d+) -->[ \t]*$",
    re.MULTILINE,
)
MAX_ISSUES = 24


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evidence_items(path: Path) -> dict[str, str]:
    return {match.group("id"): match.group("kind") for match in EVIDENCE_ID_RE.finditer(path.read_text(encoding="utf-8"))}


def _traced_items(path: Path, evidence_sha256: str) -> set[str]:
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"index trace line {line_number} is not valid JSON: {error.msg}") from error
        if not isinstance(record, dict) or record.get("schema_version") != "1.0":
            raise ValueError(f"index trace line {line_number} has an invalid schema")
        if record.get("view") != "index" or record.get("evidence_sha256") != evidence_sha256:
            raise ValueError(f"index trace line {line_number} does not match the evidence file")
        item_ids = record.get("item_ids")
        if not isinstance(item_ids, list) or not all(isinstance(item_id, str) for item_id in item_ids):
            raise ValueError(f"index trace line {line_number} has invalid item_ids")
        seen.update(item_ids)
    return seen


def record_candidate_pool(
    evidence: Path,
    index_trace: Path,
    candidate_ids: list[str],
    output: Path,
    *,
    all_pull_requests: bool = False,
) -> None:
    evidence_items = _evidence_items(evidence)
    if all_pull_requests:
        candidate_ids = [
            *candidate_ids,
            *(
                item_id
                for item_id, kind in evidence_items.items()
                if kind == "pull_request" and item_id not in candidate_ids
            ),
        ]
    if not candidate_ids:
        raise ValueError("candidate pool must contain at least one item")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate pool contains duplicate item IDs")
    evidence_sha256 = _sha256(evidence)
    traced_items = _traced_items(index_trace, evidence_sha256)
    if traced_items != set(evidence_items):
        missing = sorted(set(evidence_items) - traced_items)
        unexpected = sorted(traced_items - set(evidence_items))
        problems: list[str] = []
        if missing:
            problems.append("index trace did not cover evidence items: " + ", ".join(missing))
        if unexpected:
            problems.append("index trace contains IDs absent from evidence: " + ", ".join(unexpected))
        raise ValueError("; ".join(problems))
    invalid = sorted(set(candidate_ids) - set(evidence_items))
    if invalid:
        raise ValueError("candidate pool has IDs absent from evidence: " + ", ".join(invalid))
    candidates = [{"id": item_id, "kind": evidence_items[item_id]} for item_id in candidate_ids]
    issue_count = sum(candidate["kind"] == "issue" for candidate in candidates)
    if issue_count > MAX_ISSUES:
        raise ValueError(
            f"candidate pool has {issue_count} issue items; maximum is {MAX_ISSUES}"
        )
    evidence_pull_requests = {
        item_id for item_id, kind in evidence_items.items() if kind == "pull_request"
    }
    candidate_pull_requests = {
        candidate["id"] for candidate in candidates if candidate["kind"] == "pull_request"
    }
    missing_pull_requests = sorted(evidence_pull_requests - candidate_pull_requests)
    if missing_pull_requests:
        raise ValueError(
            "candidate pool must include every pull request in evidence; missing: "
            + ", ".join(missing_pull_requests)
        )
    result = {
        "schema_version": "1.1",
        "created_at": datetime.now(UTC).isoformat(),
        "evidence": {"path": str(evidence), "sha256": evidence_sha256},
        "index_trace": {"path": str(index_trace), "sha256": _sha256(index_trace)},
        "pull_request_scope": "all_evidence",
        "candidates": candidates,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--index-trace", type=Path, required=True)
    parser.add_argument("--candidate", action="append", default=[], metavar="PROJECT_HAMI_ID")
    parser.add_argument(
        "--all-pull-requests",
        action="store_true",
        help="include every pull request from the evidence file",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        record_candidate_pool(
            args.evidence,
            args.index_trace,
            args.candidate,
            args.output,
            all_pull_requests=args.all_pull_requests,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote candidate pool: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
