#!/usr/bin/env python3
"""Validate links and list structure in a Weekly HAMi Org Highlights report."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys


REPORT_TITLE = "# Weekly HAMi Org Highlights"
ANALYTIC_SECTION_ORDER = (
    "Executive Summary",
    "Must Pay Attention",
    "Worth Engineering Investment",
    "Pull Requests Requiring Action",
    "Important Resolutions",
    "Emerging Engineering Themes",
    "Recommended Resource Allocation",
    "Active but Not Worth Investing This Week",
)
ANALYTIC_SECTIONS = set(ANALYTIC_SECTION_ORDER)
ONE_ENGINEER_HEADING = "### One engineer-week priority"
SECTION_MAX_ITEMS = {
    "Executive Summary": 6,
    "Must Pay Attention": 5,
    "Worth Engineering Investment": 8,
    "Pull Requests Requiring Action": 8,
    "Important Resolutions": 6,
    "Emerging Engineering Themes": 5,
    "Recommended Resource Allocation": 5,
    "Active but Not Worth Investing This Week": 8,
}
PR_CATEGORIES = {
    "Review now",
    "Help contributor finish",
    "Maintainer decision required",
    "Investigate before merge",
    "Check current CI and merge readiness",
}
COMMON_DETAIL_FIELDS = {
    "相关事项",
    "已知事实",
    "证据来源",
    "分析推断",
    "当前状态",
    "信息缺口",
    "工程影响",
    "建议下一步",
    "Owner / 验收标准",
}
SECTION_REQUIRED_FIELDS = {
    "Must Pay Attention": {"必须关注的原因", "延迟处理风险", "建议投入类型"},
    "Worth Engineering Investment": {"工程价值", "用户或社区证据", "当前投入理由", "建议投入类型"},
    "Pull Requests Requiring Action": {"PR 目标", "阻塞点或信息缺口", "Dynamia 行动", "投入理由", "建议投入类型"},
    "Important Resolutions": {"解决结论或进展", "建议投入类型"},
    "Emerging Engineering Themes": {"规划意义", "置信度", "建议投入类型"},
    "Recommended Resource Allocation": {"工程主题", "推荐动作", "投入规模", "预期结果", "延迟处理风险"},
    "Active but Not Worth Investing This Week": {"暂不投入原因", "建议投入类型"},
}

INVESTMENT_SCALES = {
    "quick review",
    "several engineer-hours",
    "one engineer-day",
    "multi-day investigation",
    "requires technical owner",
}

CANONICAL_LABEL_RE = re.compile(r"Project-HAMi/(?P<repo>[A-Za-z0-9_.-]+)#(?P<number>\d+)")
GITHUB_ITEM_URL_RE = re.compile(
    r"https://github\.com/Project-HAMi/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"(?P<kind>issues|pull)/(?P<number>\d+)"
)
GITHUB_ITEM_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]+)\]\((https://github\.com/Project-HAMi/[A-Za-z0-9_.-]+/"
    r"(?:issues|pull)/\d+)\)"
)
UNLINKED_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_./])Project-HAMi/[A-Za-z0-9_.-]+#\d+"
    r"|(?<![A-Za-z0-9_./])(?![CF]#\d+\b)[A-Za-z0-9_.-]+#\d+"
    r"|(?<![A-Za-z0-9_/#])#\d+"
)
ORDERED_ITEM_RE = re.compile(r"^(?P<number>\d+)\.\s+")
NUMBERED_HEADING_RE = re.compile(r"^###\s+\d+\.")
TOP_LEVEL_BULLET_RE = re.compile(r"^[-*+]\s+")
INDENTED_BULLET_RE = re.compile(r"^(?P<indent>[ \t]+)(?P<marker>[-*+])\s+")
INVESTMENT_LABEL_RE = re.compile(r"(?P<label>建议投入类型|建议投入|投入规模)\s*[：:]")
INVESTMENT_VALUE_RE = re.compile(r"\s*`([^`\r\n]+)`")
FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
FIELD_RE = re.compile(r"^   - (?P<name>[^：:]+)[：:](?P<value>.*)$")
EVIDENCE_ITEM_RE = re.compile(
    r"^<!-- ITEM_START (?P<kind>issue|pull_request) (?P<id>[^\r\n]+) -->[ \t]*$", re.MULTILINE
)
EVIDENCE_ITEM_END_RE = re.compile(
    r"^<!-- ITEM_END (?P<kind>issue|pull_request) (?P<id>[^\r\n]+) -->[ \t]*$", re.MULTILINE
)
EVIDENCE_MARKER_RE = re.compile(
    r"^<!-- ITEM_(?P<edge>START|END) (?P<kind>issue|pull_request) (?P<id>[^\r\n]+) -->[ \t]*$",
    re.MULTILINE,
)
EVIDENCE_URL_RE = re.compile(r"^- URL: (?P<url>https://github\.com/[^\s]+)$", re.MULTILINE)
REPOSITORY_VISIBILITY_RE = re.compile(
    r"^## Repository Visibility\r?\n\r?\n"
    r"- Expected repositories: (?P<expected>[^\r\n]+)\r?\n"
    r"- Repositories visible to the token: (?P<visible>[^\r\n]+)\r?\n"
    r"- Expected repository count: `(?P<expected_count>\d+)`\r?\n"
    r"- Visible repository count: `(?P<visible_count>\d+)`$",
    re.MULTILINE,
)
REPOSITORY_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
REQUIRED_EVIDENCE_METADATA = {
    "schema_version",
    "organization",
    "generated_at",
    "timezone",
    "local_start",
    "local_end",
    "utc_start",
    "utc_end",
    "issue_count",
    "pull_request_count",
    "collection_warning_count",
    "collection_status",
    "collector_started_worktree_snapshot_sha256",
    "collector_started_worktree_head",
    "collector_started_worktree_dirty",
    "collector_started_worktree_tracked_diff_sha256",
    "collector_started_worktree_untracked_sha256",
    "expected_repository_count",
    "visible_repository_count",
}


def _collector_snapshot_digest(header: dict[str, str]) -> str:
    """Recompute the normalized collector-start source snapshot digest."""
    material = {
        "dirty": header["collector_started_worktree_dirty"] == "true",
        "head": header["collector_started_worktree_head"],
        "tracked_diff_sha256": header["collector_started_worktree_tracked_diff_sha256"],
        "untracked_sha256": header["collector_started_worktree_untracked_sha256"],
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _repository_list(value: str, *, field: str, errors: list[str]) -> list[str]:
    if value in {"None configured.", "None."}:
        return []
    repositories = [entry.strip() for entry in value.split(",")]
    if not repositories or any(not REPOSITORY_SLUG_RE.fullmatch(entry) for entry in repositories):
        errors.append(f"evidence {field} must list comma-separated OWNER/REPOSITORY values")
    if len(repositories) != len(set(repositories)):
        errors.append(f"evidence {field} must not contain duplicates")
    return repositories


def _repository_visibility_errors(
    content: str, header: dict[str, str], expected_counts: dict[str, int]
) -> list[str]:
    errors: list[str] = []
    match = REPOSITORY_VISIBILITY_RE.search(content)
    if match is None:
        return ["evidence must contain a complete Repository Visibility section"]
    expected = _repository_list(match.group("expected"), field="expected repositories", errors=errors)
    visible = _repository_list(match.group("visible"), field="visible repositories", errors=errors)
    section_expected_count = int(match.group("expected_count"))
    section_visible_count = int(match.group("visible_count"))
    if expected_counts.get("expected_repository_count") != section_expected_count:
        errors.append("evidence expected_repository_count does not match Repository Visibility")
    if expected_counts.get("visible_repository_count") != section_visible_count:
        errors.append("evidence visible_repository_count does not match Repository Visibility")
    if len(expected) != section_expected_count:
        errors.append("Repository Visibility expected repository count does not match its listed repositories")
    if len(visible) != section_visible_count:
        errors.append("Repository Visibility visible repository count does not match its listed repositories")
    # Formal reports only permit a complete collection.  In that state, the
    # configured and token-visible repository sets must be the same exact set,
    # not merely satisfy a one-sided count comparison.
    if header.get("collection_status") == "complete":
        if expected_counts.get("expected_repository_count") != expected_counts.get("visible_repository_count"):
            errors.append("complete evidence requires visible_repository_count to equal expected_repository_count")
        if set(expected) != set(visible):
            errors.append("complete evidence requires visible repositories to exactly equal expected repositories")
    return errors


def _visible_lines(lines: list[str]) -> tuple[list[bool], list[str]]:
    visible: list[bool] = []
    fence_char: str | None = None
    fence_length = 0
    fence_line = 0
    for line_number, line in enumerate(lines, start=1):
        stripped = line.rstrip("\r\n")
        if fence_char is not None:
            visible.append(False)
            closing = re.fullmatch(
                rf"[ ]{{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*",
                stripped,
            )
            if closing:
                fence_char = None
                fence_length = 0
            continue
        match = FENCE_OPEN_RE.fullmatch(stripped)
        if match:
            visible.append(False)
            fence = match.group("fence")
            fence_char = fence[0]
            fence_length = len(fence)
            fence_line = line_number
            continue
        visible.append(True)
    errors = []
    if fence_char is not None:
        errors.append(f"line {fence_line}: unterminated fenced code block")
    return visible, errors


def _document_errors(lines: list[str], visible: list[bool]) -> list[str]:
    visible_text = [
        line.rstrip("\r\n") for line, is_visible in zip(lines, visible, strict=True) if is_visible
    ]
    first_nonempty = next((line for line in visible_text if line), None)
    headings = tuple(
        line[3:]
        for line in visible_text
        if line.startswith("## ") and not line.startswith("### ")
    )
    errors: list[str] = []
    if first_nonempty != REPORT_TITLE:
        errors.append(f"required report title is {REPORT_TITLE!r}")
    if headings != ANALYTIC_SECTION_ORDER:
        errors.append(
            "required report sections must appear exactly once in this order: "
            + ", ".join(ANALYTIC_SECTION_ORDER)
        )
    return errors


def _link_errors(lines: list[str], visible: list[bool]) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if not visible[line_number - 1]:
            continue
        remaining = line
        for match in reversed(list(GITHUB_ITEM_LINK_RE.finditer(line))):
            label = match.group(1)
            url = match.group(2)
            label_match = CANONICAL_LABEL_RE.fullmatch(label)
            url_match = GITHUB_ITEM_URL_RE.fullmatch(url)
            if label_match is None or url_match is None:
                errors.append(
                    f"line {line_number}: issue or pull request links must use the canonical "
                    "[Project-HAMi/REPO#NUMBER](GitHub URL) form"
                )
            elif (
                label_match.group("repo") != url_match.group("repo")
                or label_match.group("number") != url_match.group("number")
            ):
                errors.append(
                    f"line {line_number}: {label!r} does not match its GitHub URL {url!r}"
                )
            remaining = remaining[: match.start()] + remaining[match.end() :]

        bare_url = GITHUB_ITEM_URL_RE.search(remaining)
        if bare_url:
            errors.append(
                f"line {line_number}: GitHub item URLs require the canonical "
                "[Project-HAMi/REPO#NUMBER](GitHub URL) form"
            )
        unlinked = UNLINKED_REFERENCE_RE.search(remaining)
        if unlinked:
            errors.append(
                f"line {line_number}: unlinked issue or pull request reference "
                f"{unlinked.group(0)!r}"
            )
    return errors


def _list_errors(lines: list[str], visible: list[bool]) -> list[str]:
    errors: list[str] = []
    current_section: str | None = None
    section_numbers: dict[str, list[tuple[int, int]]] = {
        section: [] for section in ANALYTIC_SECTIONS
    }
    section_has_empty_message: dict[str, bool] = {
        section: False for section in ANALYTIC_SECTIONS
    }

    for index, line in enumerate(lines):
        if not visible[index]:
            continue
        stripped = line.rstrip("\r\n")
        if stripped.startswith("## ") and not stripped.startswith("### "):
            current_section = stripped[3:]
            continue
        if current_section not in ANALYTIC_SECTIONS:
            continue
        if "本周未发现" in stripped:
            section_has_empty_message[current_section] = True
        if NUMBERED_HEADING_RE.match(stripped):
            errors.append(
                f"line {index + 1}: numbered headings are not report entries; use '1. '"
            )
        if TOP_LEVEL_BULLET_RE.match(stripped):
            errors.append(
                f"line {index + 1}: top-level entries must use an ordered list; "
                "indent field bullets under the numbered item"
            )
        nested_bullet = INDENTED_BULLET_RE.match(stripped)
        if nested_bullet:
            if nested_bullet.group("indent") != "   " or nested_bullet.group("marker") != "-":
                errors.append(
                    f"line {index + 1}: field bullets must use exactly three leading spaces "
                    "and '-'"
                )
            previous_nonempty = _previous_visible_nonempty(lines, visible, index)
            if (
                previous_nonempty is not None
                and ORDERED_ITEM_RE.match(lines[previous_nonempty].rstrip("\r\n"))
                and (index == 0 or lines[index - 1].strip())
            ):
                errors.append(
                    f"line {index + 1}: add a blank line before the nested fields"
                )
        ordered = ORDERED_ITEM_RE.match(stripped)
        if ordered:
            section_numbers[current_section].append((index, int(ordered.group("number"))))
            previous_nonempty = _previous_visible_nonempty(lines, visible, index)
            if previous_nonempty is not None:
                previous_text = lines[previous_nonempty].rstrip("\r\n")
                if previous_text.startswith("##") and (index == 0 or lines[index - 1].strip()):
                    errors.append(
                        f"line {index + 1}: add a blank line before the ordered list "
                        f"in {current_section!r}"
                    )

    for section, entries in section_numbers.items():
        if not entries:
            if not section_has_empty_message[section]:
                errors.append(f"section {section!r}: expected an ordered list starting with '1. '")
            continue
        expected = list(range(1, len(entries) + 1))
        actual = [number for _, number in entries]
        if actual != expected:
            errors.append(
                f"section {section!r}: ordered items must be sequential; "
                f"expected {expected}, found {actual}"
            )
    return errors


def _previous_visible_nonempty(
    lines: list[str], visible: list[bool], before_index: int
) -> int | None:
    for index in range(before_index - 1, -1, -1):
        if visible[index] and lines[index].strip():
            return index
    return None


def _one_engineer_errors(lines: list[str], visible: list[bool]) -> list[str]:
    positions = [
        index
        for index, line in enumerate(lines)
        if visible[index] and line.rstrip("\r\n") == ONE_ENGINEER_HEADING
    ]
    if len(positions) != 1:
        return [
            f"required {ONE_ENGINEER_HEADING!r} heading must appear exactly once"
        ]

    heading_index = positions[0]
    current_section: str | None = None
    for index in range(heading_index):
        if not visible[index]:
            continue
        stripped = lines[index].rstrip("\r\n")
        if stripped.startswith("## ") and not stripped.startswith("### "):
            current_section = stripped[3:]
    errors: list[str] = []
    if current_section != "Recommended Resource Allocation":
        errors.append(
            f"line {heading_index + 1}: {ONE_ENGINEER_HEADING!r} must be in "
            "'Recommended Resource Allocation'"
        )

    body: list[str] = []
    has_non_prose_structure = False
    for index in range(heading_index + 1, len(lines)):
        if not visible[index]:
            continue
        stripped = lines[index].rstrip("\r\n")
        if stripped.startswith("## ") and not stripped.startswith("### "):
            break
        body.append(stripped)
        if re.match(r"^\s*(?:\d+\.\s+|[-*+]\s+)", stripped):
            has_non_prose_structure = True
            errors.append(
                f"line {index + 1}: one engineer-week priority must contain prose, "
                "not list items"
            )
        if re.match(r"^#{1,6}\s+", stripped):
            has_non_prose_structure = True

    paragraphs: list[list[str]] = []
    paragraph: list[str] = []
    for line in body:
        if line:
            paragraph.append(line)
        elif paragraph:
            paragraphs.append(paragraph)
            paragraph = []
    if paragraph:
        paragraphs.append(paragraph)
    if len(paragraphs) != 2 or has_non_prose_structure:
        errors.append(
            f"line {heading_index + 1}: one engineer-week priority requires exactly a "
            "conclusion paragraph and a reason paragraph"
        )
    return errors


def _investment_errors(lines: list[str], visible: list[bool]) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if not visible[line_number - 1]:
            continue
        for match in INVESTMENT_LABEL_RE.finditer(line):
            label = match.group("label")
            if label == "建议投入":
                errors.append(
                    f"line {line_number}: use the exact investment field name "
                    "'建议投入类型' or '投入规模'"
                )
            value_match = INVESTMENT_VALUE_RE.match(line, match.end())
            value = (
                value_match.group(1)
                if value_match is not None
                else "(missing exact backtick value)"
            )
            if value not in INVESTMENT_SCALES:
                errors.append(f"line {line_number}: invalid investment scale {value!r}")
    return errors


def _front_matter(content: str) -> dict[str, str]:
    match = re.match(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", content, re.DOTALL)
    if match is None:
        return {}
    result: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip().strip('"')
    return result


def _evidence_items(content: str) -> dict[tuple[str, int], tuple[str, str]]:
    """Map each evidence item to its GitHub kind and canonical URL."""
    items: dict[tuple[str, int], tuple[str, str]] = {}
    for match in EVIDENCE_ITEM_RE.finditer(content):
        item_id = match.group("id")
        id_match = CANONICAL_LABEL_RE.fullmatch(item_id)
        if id_match is None:
            continue
        next_marker = EVIDENCE_ITEM_RE.search(content, match.end())
        block = content[match.end() : next_marker.start() if next_marker else len(content)]
        url_match = EVIDENCE_URL_RE.search(block)
        if url_match is None:
            continue
        key = (id_match.group("repo"), int(id_match.group("number")))
        expected_kind = "issues" if match.group("kind") == "issue" else "pull"
        items[key] = (expected_kind, url_match.group("url"))
    return items


def _evidence_structure_errors(content: str, header: dict[str, str]) -> list[str]:
    """Reject malformed or self-inconsistent evidence before trusting its links."""
    errors: list[str] = []
    missing = sorted(field for field in REQUIRED_EVIDENCE_METADATA if not header.get(field))
    if missing:
        errors.append("evidence front matter is missing required metadata: " + ", ".join(missing))
        return errors
    if header["schema_version"] != "1.0":
        errors.append("evidence schema_version must be '1.0'")
    if header["timezone"] != "Asia/Shanghai":
        errors.append("evidence timezone must be 'Asia/Shanghai'")
    for field in ("generated_at", "local_start", "local_end", "utc_start", "utc_end"):
        try:
            datetime.fromisoformat(header[field])
        except ValueError:
            errors.append(f"evidence {field} must be an ISO-8601 datetime")
    if not re.fullmatch(r"[0-9a-f]{64}", header["collector_started_worktree_snapshot_sha256"]):
        errors.append("evidence collector_started_worktree_snapshot_sha256 must be a SHA-256")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", header["collector_started_worktree_head"]):
        errors.append("evidence collector_started_worktree_head must be a Git object ID")
    if header["collector_started_worktree_dirty"] not in {"true", "false"}:
        errors.append("evidence collector_started_worktree_dirty must be true or false")
    for field in (
        "collector_started_worktree_tracked_diff_sha256",
        "collector_started_worktree_untracked_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", header[field]):
            errors.append(f"evidence {field} must be a SHA-256")
    snapshot_fields_valid = not any(
        error.startswith("evidence collector_started_worktree_") for error in errors
    )
    if snapshot_fields_valid and (
        header["collector_started_worktree_snapshot_sha256"] != _collector_snapshot_digest(header)
    ):
        errors.append("evidence collector-start worktree snapshot digest does not match its components")
    expected_counts: dict[str, int] = {}
    for field in (
        "issue_count",
        "pull_request_count",
        "collection_warning_count",
        "expected_repository_count",
        "visible_repository_count",
    ):
        try:
            value = int(header[field])
        except ValueError:
            errors.append(f"evidence {field} must be a non-negative integer")
            continue
        if value < 0:
            errors.append(f"evidence {field} must be a non-negative integer")
        else:
            expected_counts[field] = value
    if expected_counts.get("expected_repository_count") == 0:
        errors.append("evidence expected_repository_count must be positive for a formal report")
    errors.extend(_repository_visibility_errors(content, header, expected_counts))

    starts = [(match.group("kind"), match.group("id")) for match in EVIDENCE_ITEM_RE.finditer(content)]
    ends = [(match.group("kind"), match.group("id")) for match in EVIDENCE_ITEM_END_RE.finditer(content)]
    if len(starts) != len(set(starts)):
        errors.append("evidence item start markers must be unique")
    if len(ends) != len(set(ends)):
        errors.append("evidence item end markers must be unique")
    if set(starts) != set(ends):
        errors.append("evidence ITEM_START and ITEM_END markers must match exactly")

    open_item: tuple[str, str] | None = None
    order_error = False
    for marker in EVIDENCE_MARKER_RE.finditer(content):
        item = (marker.group("kind"), marker.group("id"))
        if marker.group("edge") == "START":
            if open_item is not None:
                order_error = True
            open_item = item
        elif open_item != item:
            order_error = True
        else:
            open_item = None
    if open_item is not None:
        order_error = True
    if order_error:
        errors.append(
            "evidence ITEM_START and ITEM_END markers must pair in document order without nesting or crossing"
        )
    issue_markers = sum(kind == "issue" for kind, _ in starts)
    pr_markers = sum(kind == "pull_request" for kind, _ in starts)
    if "issue_count" in expected_counts and issue_markers != expected_counts["issue_count"]:
        errors.append("evidence issue_count does not match issue ITEM_START marker count")
    if "pull_request_count" in expected_counts and pr_markers != expected_counts["pull_request_count"]:
        errors.append("evidence pull_request_count does not match pull_request ITEM_START marker count")
    items = _evidence_items(content)
    if len(items) != len(starts):
        errors.append("every evidence item must have a canonical ID and URL metadata line")
    return errors


def _evidence_has_meaningful_limitations(content: str) -> bool:
    for heading in ("## Collection Warnings", "## Data Limitations"):
        start = content.find(heading)
        if start < 0:
            continue
        end = content.find("\n## ", start + len(heading))
        section = content[start : end if end >= 0 else len(content)]
        for line in section.splitlines()[1:]:
            value = line.strip()
            if value.startswith("- ") and value[2:].strip().lower().rstrip(".") != "none":
                return True
    return False


def _header_and_evidence_errors(
    content: str, lines: list[str], visible: list[bool], evidence: str | None
) -> list[str]:
    errors: list[str] = []
    first_heading = next(
        (
            index
            for index, line in enumerate(lines)
            if visible[index] and line.rstrip("\r\n").startswith("## ")
        ),
        len(lines),
    )
    header = [line.rstrip("\r\n") for index, line in enumerate(lines[:first_heading]) if visible[index]]
    expected = (
        "Period:",
        "Organization:",
        "Issues with activity:",
        "Pull requests with activity:",
        "Evidence limitations:",
    )
    positions: dict[str, int] = {}
    values: dict[str, str] = {}
    for field in expected:
        matches = [index for index, line in enumerate(header) if line.startswith(field)]
        if len(matches) != 1:
            errors.append(f"report header must contain exactly one {field!r} field")
            continue
        positions[field] = matches[0]
        values[field] = header[matches[0]][len(field) :].strip()
    if positions and [positions[field] for field in expected if field in positions] != sorted(positions.values()):
        errors.append("report header fields must use the required order")
    limitations_at = positions.get("Evidence limitations:")
    if limitations_at is not None:
        bullets = [line for line in header[limitations_at + 1 :] if line]
        if not bullets or any(not line.startswith("- ") for line in bullets):
            errors.append("Evidence limitations must be a top-level bullet list")

    if evidence is None:
        errors.append("validator requires an evidence file")
        return errors
    evidence_header = _front_matter(evidence)
    errors.extend(_evidence_structure_errors(evidence, evidence_header))
    if limitations_at is not None and _evidence_has_meaningful_limitations(evidence):
        if any(line[2:].strip().lower().rstrip(".") == "none" for line in bullets):
            errors.append("Evidence limitations cannot claim None when evidence contains warnings or limitations")
    if evidence_header.get("collection_status") != "complete":
        errors.append("evidence collection_status must be 'complete' for a formal report")
    expected_org = evidence_header.get("organization")
    if expected_org and values.get("Organization:") != expected_org:
        errors.append("report Organization does not match evidence organization")
    local_start = evidence_header.get("local_start")
    local_end = evidence_header.get("local_end")
    if local_start and local_end and values.get("Period:") != f"{local_start} through {local_end}":
        errors.append("report Period must exactly match the evidence local_start through local_end")
    for report_field, evidence_field in (
        ("Issues with activity:", "issue_count"),
        ("Pull requests with activity:", "pull_request_count"),
    ):
        expected_count = evidence_header.get(evidence_field)
        if expected_count is not None and values.get(report_field) != expected_count:
            errors.append(f"report {report_field[:-1]} does not match evidence {evidence_field}")

    items = _evidence_items(evidence)
    for link in GITHUB_ITEM_LINK_RE.finditer(content):
        label_match = CANONICAL_LABEL_RE.fullmatch(link.group(1))
        url_match = GITHUB_ITEM_URL_RE.fullmatch(link.group(2))
        if label_match is None or url_match is None:
            continue
        key = (label_match.group("repo"), int(label_match.group("number")))
        if key not in items:
            errors.append(f"report reference {label_match.group(0)!r} is absent from evidence")
        elif items[key] != (url_match.group("kind"), link.group(2)):
            errors.append(f"report reference {label_match.group(0)!r} does not match its evidence item")
    return errors


def _section_bounds(lines: list[str], visible: list[bool]) -> dict[str, tuple[int, int]]:
    starts = {
        line.rstrip("\r\n")[3:]: index
        for index, line in enumerate(lines)
        if visible[index]
        and line.rstrip("\r\n").startswith("## ")
        and not line.rstrip("\r\n").startswith("### ")
        and line.rstrip("\r\n")[3:] in ANALYTIC_SECTIONS
    }
    ordered = [(section, starts[section]) for section in ANALYTIC_SECTION_ORDER if section in starts]
    return {
        section: (start, ordered[index + 1][1] if index + 1 < len(ordered) else len(lines))
        for index, (section, start) in enumerate(ordered)
    }


def _entry_ranges(
    lines: list[str], visible: list[bool], start: int, end: int
) -> list[tuple[int, int]]:
    starts = [
        index
        for index in range(start + 1, end)
        if visible[index] and ORDERED_ITEM_RE.match(lines[index].rstrip("\r\n"))
    ]
    return [(entry_start, starts[index + 1] if index + 1 < len(starts) else end) for index, entry_start in enumerate(starts)]


def _field_link_items(value: str) -> dict[tuple[str, int], tuple[str, str]]:
    items: dict[tuple[str, int], tuple[str, str]] = {}
    for link in GITHUB_ITEM_LINK_RE.finditer(value):
        label_match = CANONICAL_LABEL_RE.fullmatch(link.group(1))
        url_match = GITHUB_ITEM_URL_RE.fullmatch(link.group(2))
        if label_match is not None and url_match is not None:
            items[(label_match.group("repo"), int(label_match.group("number")))] = (
                url_match.group("kind"),
                link.group(2),
            )
    return items


def _contract_errors(
    lines: list[str], visible: list[bool], evidence_items: dict[tuple[str, int], tuple[str, str]]
) -> list[str]:
    errors: list[str] = []
    bounds = _section_bounds(lines, visible)
    for section, (start, end) in bounds.items():
        entries = _entry_ranges(lines, visible, start, end)
        if len(entries) > SECTION_MAX_ITEMS[section]:
            errors.append(f"section {section!r}: allows at most {SECTION_MAX_ITEMS[section]} items")
        if section == "Pull Requests Requiring Action":
            headings = [
                lines[index].rstrip("\r\n")[4:]
                for index in range(start + 1, end)
                if visible[index] and lines[index].rstrip("\r\n").startswith("### ")
            ]
            invalid = [heading for heading in headings if heading not in PR_CATEGORIES]
            if invalid:
                errors.append("Pull Requests Requiring Action uses unsupported category: " + ", ".join(invalid))
            if entries and not headings:
                errors.append("Pull Requests Requiring Action entries require an allowed ### category")
        if section == "Executive Summary":
            continue
        for entry_start, entry_end in entries:
            field_values = {
                match.group("name").strip(): match.group("value").strip()
                for index in range(entry_start + 1, entry_end)
                if visible[index]
                and (match := FIELD_RE.match(lines[index].rstrip("\r\n"))) is not None
            }
            fields = set(field_values)
            required = COMMON_DETAIL_FIELDS | SECTION_REQUIRED_FIELDS.get(section, set())
            missing = sorted(required - fields)
            if missing:
                errors.append(
                    f"line {entry_start + 1}: {section} entry is missing required fields: "
                    + ", ".join(missing)
                )
            blank = sorted(field for field in required if field in field_values and not field_values[field])
            if blank:
                errors.append(
                    f"line {entry_start + 1}: {section} entry has blank required fields: "
                    + ", ".join(blank)
                )
            source = field_values.get("证据来源", "")
            if source and not re.search(r"actor=`(?:human|maintainer|bot)`", source):
                errors.append(
                    f"line {entry_start + 1}: 证据来源 must classify actor as human, maintainer, or bot"
                )
            if source and "in_period=`" not in source:
                errors.append(f"line {entry_start + 1}: 证据来源 must state in_period")
            related_items = _field_link_items(field_values.get("相关事项", ""))
            source_items = _field_link_items(source)
            if source and not source_items:
                errors.append(
                    f"line {entry_start + 1}: 证据来源 must include a canonical GitHub evidence deep link"
                )
            if related_items and source_items:
                if not set(related_items) & set(source_items):
                    errors.append(
                        f"line {entry_start + 1}: 证据来源 must link to an item listed in 相关事项"
                    )
                for item, source_value in source_items.items():
                    if evidence_items.get(item) != source_value:
                        errors.append(
                            f"line {entry_start + 1}: 证据来源 link must match a canonical item in evidence"
                        )
            inference = field_values.get("分析推断", "")
            if inference and not re.search(r"confidence=`(?:low|medium|high)`", inference):
                errors.append(f"line {entry_start + 1}: 分析推断 must state confidence")
            if section == "Emerging Engineering Themes":
                entry_text = "".join(lines[entry_start:entry_end])
                item_ids = set(CANONICAL_LABEL_RE.findall(entry_text))
                if len(item_ids) < 2:
                    errors.append(
                        f"line {entry_start + 1}: Emerging Engineering Themes entries require at least two distinct evidence items"
                    )
    return errors


def validate_report(content: str, evidence: str | None = None) -> list[str]:
    lines = content.splitlines(keepends=True)
    visible, fence_errors = _visible_lines(lines)
    return (
        fence_errors
        + _document_errors(lines, visible)
        + _header_and_evidence_errors(content, lines, visible, evidence)
        + _link_errors(lines, visible)
        + _list_errors(lines, visible)
        + _one_engineer_errors(lines, visible)
        + _investment_errors(lines, visible)
        + _contract_errors(lines, visible, _evidence_items(evidence) if evidence is not None else {})
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("evidence", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        content = args.report.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: cannot read report: {error}", file=sys.stderr)
        return 2

    try:
        evidence = args.evidence.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: cannot read evidence: {error}", file=sys.stderr)
        return 2

    errors = validate_report(content, evidence)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("Report format is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
