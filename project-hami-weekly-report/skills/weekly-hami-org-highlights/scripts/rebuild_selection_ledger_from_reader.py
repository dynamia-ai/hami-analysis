#!/usr/bin/env python3
"""Rebuild candidate ledger measurements from the bounded triage reader."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


STATUS_RE = re.compile(
    r"chunk (?P<chunk>\d+)/(?P<chunks>\d+); output bytes: (?P<output>\d+); "
    r"view bytes: (?P<view>\d+);(?P<tail>.*)"
)
COUNT_PATTERNS = {
    "human_count": re.compile(r"^- Human (?:comment )?activity: `(?P<count>\d+)`$", re.MULTILINE),
    "maintainer_count": re.compile(
        r"^- Maintainer/member/collaborator (?:comment )?activity: `(?P<count>\d+)`$",
        re.MULTILINE,
    ),
    "bot_count": re.compile(r"^- Bot (?:comment )?activity: `(?P<count>\d+)`$", re.MULTILINE),
}
READER_TIMEOUT_SECONDS = 60.0


def _reader_script() -> Path:
    return Path(__file__).with_name("evidence_reader.py")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_chunk(evidence: Path, kind: str, item_id: str, chunk: int) -> tuple[str, str]:
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_reader_script()),
                "item",
                kind,
                item_id,
                "--view",
                "triage",
                "--chunk",
                str(chunk),
                "--max-bytes",
                "20000",
                str(evidence),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=READER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"reader timed out for {item_id} chunk {chunk}") from error
    if result.returncode != 0:
        raise RuntimeError(f"reader failed for {item_id} chunk {chunk}: {result.stderr.strip()}")
    return result.stdout, result.stderr


def _measure(evidence: Path, kind: str, item_id: str) -> dict[str, object]:
    first_stdout, first_stderr = _run_chunk(evidence, kind, item_id, 1)
    match = STATUS_RE.search(first_stderr)
    if match is None:
        raise RuntimeError(f"reader status is not parseable for {item_id}: {first_stderr.strip()}")
    total_chunks = int(match.group("chunks"))
    output_bytes = int(match.group("output"))
    view_bytes = int(match.group("view"))
    stdout = first_stdout
    statuses = [first_stderr]
    for chunk in range(2, total_chunks + 1):
        chunk_stdout, chunk_stderr = _run_chunk(evidence, kind, item_id, chunk)
        chunk_match = STATUS_RE.search(chunk_stderr)
        if chunk_match is None or int(chunk_match.group("chunks")) != total_chunks:
            raise RuntimeError(f"reader chunk status changed for {item_id}: {chunk_stderr.strip()}")
        output_bytes += int(chunk_match.group("output"))
        stdout += chunk_stdout
        statuses.append(chunk_stderr)
    values: dict[str, int] = {}
    for field, pattern in COUNT_PATTERNS.items():
        matches = pattern.findall(stdout)
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one {field} for {item_id}, found {len(matches)}")
        values[field] = int(matches[0])
    return {
        "bytes_read": output_bytes,
        "view_bytes": view_bytes,
        "chunks_complete": len(statuses) == total_chunks
        and statuses[-1].find("end of item view") >= 0,
        **values,
    }


def rebuild(evidence: Path, pool: Path, ledger: Path, output: Path, diff_output: Path) -> None:
    records = {str(record["id"]): record for record in _read_jsonl(ledger)}
    candidates = json.loads(pool.read_text(encoding="utf-8"))["candidates"]
    rebuilt: list[dict[str, object]] = []
    diff_rows = [
        "id\told_bytes_read\treader_bytes_read\told_chunks_complete\treader_chunks_complete"
        "\told_human\treader_human\told_maintainer\treader_maintainer\told_bot\treader_bot\treader_view_bytes"
    ]
    for candidate in candidates:
        item_id = str(candidate["id"])
        record = dict(records[item_id])
        measured = _measure(evidence, str(candidate["kind"]), item_id)
        old = {
            "views_read": record["views_read"],
            "bytes_read": record["bytes_read"],
            "chunks_complete": record["chunks_complete"],
            "human_count": record["human_count"],
            "maintainer_count": record["maintainer_count"],
            "bot_count": record["bot_count"],
        }
        record["views_read"] = ["triage"]
        for field in ("bytes_read", "chunks_complete", "human_count", "maintainer_count", "bot_count"):
            record[field] = measured[field]
        rebuilt.append(record)
        diff_rows.append(
            "\t".join(
                str(value)
                for value in (
                    item_id,
                    old["bytes_read"],
                    measured["bytes_read"],
                    old["chunks_complete"],
                    measured["chunks_complete"],
                    old["human_count"],
                    measured["human_count"],
                    old["maintainer_count"],
                    measured["maintainer_count"],
                    old["bot_count"],
                    measured["bot_count"],
                    measured["view_bytes"],
                )
            )
        )
    output.write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in rebuilt), encoding="utf-8")
    diff_output.write_text("\n".join(diff_rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diff-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        rebuild(args.evidence, args.pool, args.ledger, args.output, args.diff_output)
    except (OSError, KeyError, RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote rebuilt ledger: {args.output}")
    print(f"Wrote reader diff: {args.diff_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
