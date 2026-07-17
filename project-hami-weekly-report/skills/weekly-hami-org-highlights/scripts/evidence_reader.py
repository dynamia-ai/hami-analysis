#!/usr/bin/env python3
"""Read bounded views from one hami-github-activity Markdown evidence file."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


TOP_LEVEL_SECTIONS = [
    "## Document Map",
    "## Collection Summary",
    "## Issues Index",
    "## Pull Requests Index",
    "## Issue Evidence",
    "## Pull Request Evidence",
    "## Collection Warnings",
    "## Data Limitations",
]

ISSUE_SECTIONS = [
    "#### Metadata",
    "#### Activity During Scan Period",
    "#### Labels, Assignees and Milestone",
    "#### Body",
    "#### Previous Context",
    "#### Comments During Scan Period",
    "#### Latest Human Comment",
    "#### Latest Maintainer Comment",
    "#### Data Gaps",
]

PULL_REQUEST_SECTIONS = [
    "#### Metadata",
    "#### Activity During Scan Period",
    "#### Current Review Information",
    "#### Labels, Assignees and Requested Reviewers",
    "#### Change Size",
    "#### Body",
    "#### Previous Context",
    "#### Conversation Comments During Scan Period",
    "#### Reviews During Scan Period",
    "#### Review Comments During Scan Period",
    "#### Latest Human Activity",
    "#### Latest Maintainer Activity",
    "#### Data Gaps",
]

TRIAGE_SECTIONS = {
    "issue": {
        "#### Metadata",
        "#### Activity During Scan Period",
        "#### Labels, Assignees and Milestone",
        "#### Latest Human Comment",
        "#### Latest Maintainer Comment",
        "#### Data Gaps",
    },
    "pull_request": {
        "#### Metadata",
        "#### Activity During Scan Period",
        "#### Current Review Information",
        "#### Labels, Assignees and Requested Reviewers",
        "#### Change Size",
        "#### Latest Human Activity",
        "#### Latest Maintainer Activity",
        "#### Data Gaps",
    },
}

SECTION_VIEWS = {
    "issue": {
        "body": "#### Body",
        "previous_context": "#### Previous Context",
        "comments": "#### Comments During Scan Period",
    },
    "pull_request": {
        "body": "#### Body",
        "previous_context": "#### Previous Context",
        "comments": "#### Conversation Comments During Scan Period",
        "reviews": "#### Reviews During Scan Period",
        "review_comments": "#### Review Comments During Scan Period",
    },
}

MAX_INDEX_ROWS = 50
DEFAULT_INDEX_BYTES = 20_000
DEFAULT_ITEM_BYTES = 40_000
MAX_ITEM_BYTES = 40_000
MAX_OVERVIEW_BYTES = 20_000


class EvidenceError(Exception):
    """Raised when an evidence document cannot be read safely."""


def _visible_line_indices(lines: list[str]) -> set[int]:
    visible: set[int] = set()
    fence: str | None = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if fence is not None:
            if stripped == fence:
                fence = None
            continue
        match = re.fullmatch(r"(`{3,})(?:[A-Za-z0-9_-]+)?\s*", stripped)
        if match:
            fence = match.group(1)
            continue
        visible.add(index)
    if fence is not None:
        raise EvidenceError(f"unterminated Markdown fence {fence!r}")
    return visible


def _load(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as error:
        raise EvidenceError(f"cannot read evidence file: {error}") from error


def _unique_line(lines: list[str], target: str) -> int:
    visible = _visible_line_indices(lines)
    matches = [
        index
        for index, line in enumerate(lines)
        if index in visible and line.rstrip("\r\n") == target
    ]
    if len(matches) != 1:
        raise EvidenceError(f"expected exactly one {target!r}, found {len(matches)}")
    return matches[0]


def _first_line_at_or_after(lines: list[str], target: str, start: int) -> int:
    visible = _visible_line_indices(lines)
    for index in range(start, len(lines)):
        if index in visible and lines[index].rstrip("\r\n") == target:
            return index
    raise EvidenceError(f"expected {target!r} at or after line {start + 1}")


def _last_marker(lines: list[str], prefix: str) -> int | None:
    visible = _visible_line_indices(lines)
    matches = [
        index
        for index, line in enumerate(lines)
        if index in visible and line.rstrip("\r\n").startswith(prefix)
    ]
    return max(matches) if matches else None


def _top_level_bounds(lines: list[str]) -> dict[str, tuple[int, int]]:
    starts: list[int] = []
    search_from = _front_matter_end(lines)
    for heading in TOP_LEVEL_SECTIONS[:5]:
        position = _first_line_at_or_after(lines, heading, search_from)
        starts.append(position)
        search_from = position + 1

    last_issue_end = _last_marker(lines, "<!-- ITEM_END issue ")
    pull_request_start = max(search_from, (last_issue_end + 1) if last_issue_end is not None else 0)
    pull_request_evidence = _first_line_at_or_after(
        lines, "## Pull Request Evidence", pull_request_start
    )
    starts.append(pull_request_evidence)

    last_pull_request_end = _last_marker(lines, "<!-- ITEM_END pull_request ")
    warnings_start = max(
        pull_request_evidence + 1,
        (last_pull_request_end + 1) if last_pull_request_end is not None else 0,
    )
    warnings = _first_line_at_or_after(lines, "## Collection Warnings", warnings_start)
    limitations = _first_line_at_or_after(lines, "## Data Limitations", warnings + 1)
    starts.extend([warnings, limitations])

    if starts != sorted(starts):
        raise EvidenceError("top-level sections are not in the expected order")
    return {
        heading: (start, starts[index + 1] if index + 1 < len(starts) else len(lines))
        for index, (heading, start) in enumerate(zip(TOP_LEVEL_SECTIONS, starts, strict=True))
    }


def _front_matter_end(lines: list[str]) -> int:
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise EvidenceError("YAML front matter is missing")
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return index + 1
    raise EvidenceError("YAML front matter is not terminated")


def _encoded_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _print_overview(path: Path) -> None:
    lines = _load(path)
    bounds = _top_level_bounds(lines)
    parts = ["".join(lines[: _front_matter_end(lines)]).rstrip()]
    for heading in (
        "## Document Map",
        "## Collection Summary",
        "## Collection Warnings",
        "## Data Limitations",
    ):
        start, end = bounds[heading]
        parts.append("".join(lines[start:end]).rstrip())
    output = "\n\n".join(parts) + "\n"
    if _encoded_size(output) > MAX_OVERVIEW_BYTES:
        raise EvidenceError(
            f"overview is {_encoded_size(output)} bytes; expected at most {MAX_OVERVIEW_BYTES}"
        )
    sys.stdout.write(output)
    print(f"overview bytes: {_encoded_size(output)}", file=sys.stderr)


def _index_rows(lines: list[str], kind: str) -> tuple[list[str], list[str]]:
    bounds = _top_level_bounds(lines)
    heading = "## Issues Index" if kind == "issue" else "## Pull Requests Index"
    start, end = bounds[heading]
    table_lines = [line for line in lines[start + 1 : end] if line.startswith("|")]
    if len(table_lines) < 2:
        return [], []
    headers = table_lines[:2]
    rows = [line for line in table_lines[2:] if not line.startswith("| No matching")]
    return headers, rows


def _print_index(path: Path, kind: str, offset: int, limit: int, max_bytes: int) -> None:
    if offset < 0:
        raise EvidenceError("offset must be zero or greater")
    if not 1 <= limit <= MAX_INDEX_ROWS:
        raise EvidenceError(f"limit must be between 1 and {MAX_INDEX_ROWS}")
    if not 1_000 <= max_bytes <= DEFAULT_INDEX_BYTES:
        raise EvidenceError(f"max-bytes must be between 1000 and {DEFAULT_INDEX_BYTES}")

    headers, rows = _index_rows(_load(path), kind)
    if offset > len(rows):
        raise EvidenceError(f"offset {offset} is beyond the {len(rows)} available rows")

    selected: list[str] = []
    output = "".join(headers)
    for row in rows[offset : offset + limit]:
        candidate = output + "".join(selected) + row
        if _encoded_size(candidate) > max_bytes:
            break
        selected.append(row)
    if offset < len(rows) and not selected:
        raise EvidenceError("the next index row does not fit within max-bytes")

    output += "".join(selected)
    sys.stdout.write(output)
    first = offset + 1 if selected else 0
    last = offset + len(selected)
    message = f"items {first}-{last} of {len(rows)}; output bytes: {_encoded_size(output)}"
    if last < len(rows):
        message += f"; next offset: {last}"
    else:
        message += "; end of index"
    print(message, file=sys.stderr)


def _item_block(lines: list[str], kind: str, item_id: str) -> list[str]:
    start_marker = f"<!-- ITEM_START {kind} {item_id} -->"
    end_marker = f"<!-- ITEM_END {kind} {item_id} -->"
    start = _unique_line(lines, start_marker)
    end = _unique_line(lines, end_marker)
    if end <= start:
        raise EvidenceError(f"item end marker precedes start marker for {kind} {item_id}")
    return lines[start : end + 1]


def _section_positions(block: list[str], kind: str) -> list[tuple[str, int]]:
    headings = ISSUE_SECTIONS if kind == "issue" else PULL_REQUEST_SECTIONS
    visible = _visible_line_indices(block)
    positions: list[tuple[str, int]] = []
    search_from = 0
    for heading in headings:
        matches = [
            index
            for index in range(search_from, len(block))
            if index in visible and block[index].rstrip("\r\n") == heading
        ]
        if not matches:
            raise EvidenceError(f"item section {heading!r} is missing")
        position = matches[0]
        positions.append((heading, position))
        search_from = position + 1
    return positions


def _triage_view(block: list[str], kind: str) -> str:
    positions = _section_positions(block, kind)
    first_section = positions[0][1]
    output = list(block[:first_section])
    selected = TRIAGE_SECTIONS[kind]
    for index, (heading, start) in enumerate(positions):
        if heading not in selected:
            continue
        end = positions[index + 1][1] if index + 1 < len(positions) else len(block) - 1
        output.extend(block[start:end])
    output.append(block[-1])
    return "".join(output)


def _section_view(block: list[str], kind: str, view: str) -> str:
    heading = SECTION_VIEWS[kind].get(view)
    if heading is None:
        available = ", ".join(sorted(SECTION_VIEWS[kind]))
        raise EvidenceError(f"view {view!r} is not available for {kind}; choose from {available}")
    positions = _section_positions(block, kind)
    for index, (current, start) in enumerate(positions):
        if current != heading:
            continue
        end = positions[index + 1][1] if index + 1 < len(positions) else len(block) - 1
        return "".join(block[start:end])
    raise EvidenceError(f"item section {heading!r} is missing")


def _take_prefix(text: str, byte_limit: int) -> tuple[str, str]:
    if _encoded_size(text) <= byte_limit:
        return text, ""
    low = 0
    high = min(len(text), byte_limit)
    while low < high:
        middle = (low + high + 1) // 2
        if _encoded_size(text[:middle]) <= byte_limit:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        raise EvidenceError("max-bytes is too small for one UTF-8 character")
    return text[:low], text[low:]


def _chunks(text: str, max_bytes: int) -> list[str]:
    chunks: list[str] = []
    remainder = text
    while remainder:
        chunk, remainder = _take_prefix(remainder, max_bytes)
        chunks.append(chunk)
    return chunks or [""]


def _print_item(
    path: Path,
    kind: str,
    item_id: str,
    view: str,
    chunk_number: int,
    max_bytes: int,
) -> None:
    if not 256 <= max_bytes <= MAX_ITEM_BYTES:
        raise EvidenceError(f"max-bytes must be between 256 and {MAX_ITEM_BYTES}")
    if chunk_number < 1:
        raise EvidenceError("chunk must be 1 or greater")

    block = _item_block(_load(path), kind, item_id)
    if view == "full":
        output = "".join(block)
    elif view == "triage":
        output = _triage_view(block, kind)
    else:
        output = _section_view(block, kind, view)
    chunks = _chunks(output, max_bytes)
    if chunk_number > len(chunks):
        raise EvidenceError(f"chunk {chunk_number} is beyond the {len(chunks)} available chunks")

    selected = chunks[chunk_number - 1]
    sys.stdout.write(selected)
    message = (
        f"chunk {chunk_number}/{len(chunks)}; output bytes: {_encoded_size(selected)}; "
        f"view bytes: {_encoded_size(output)}"
    )
    if chunk_number < len(chunks):
        message += f"; use --chunk {chunk_number + 1} for the next chunk"
    else:
        message += "; end of item view"
    print(message, file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    overview = subparsers.add_parser("overview", help="read bounded control sections")
    overview.add_argument("evidence", type=Path)

    index = subparsers.add_parser("index", help="read one bounded index page")
    index.add_argument("kind", choices=("issue", "pull_request"))
    index.add_argument("--offset", type=int, default=0)
    index.add_argument("--limit", type=int, default=MAX_INDEX_ROWS)
    index.add_argument("--max-bytes", type=int, default=DEFAULT_INDEX_BYTES)
    index.add_argument("evidence", type=Path)

    item = subparsers.add_parser("item", help="read one bounded item view")
    item.add_argument("kind", choices=("issue", "pull_request"))
    item.add_argument("item_id")
    item.add_argument(
        "--view",
        choices=(
            "triage",
            "body",
            "previous_context",
            "comments",
            "reviews",
            "review_comments",
            "full",
        ),
        default="triage",
    )
    item.add_argument("--chunk", type=int, default=1)
    item.add_argument("--max-bytes", type=int, default=DEFAULT_ITEM_BYTES)
    item.add_argument("evidence", type=Path)

    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "overview":
            _print_overview(args.evidence)
        elif args.command == "index":
            _print_index(args.evidence, args.kind, args.offset, args.limit, args.max_bytes)
        else:
            _print_item(
                args.evidence,
                args.kind,
                args.item_id,
                args.view,
                args.chunk,
                args.max_bytes,
            )
    except EvidenceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
