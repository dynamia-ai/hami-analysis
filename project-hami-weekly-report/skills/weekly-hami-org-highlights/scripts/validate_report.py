#!/usr/bin/env python3
"""Validate links and list structure in a Weekly HAMi Org Highlights report."""

from __future__ import annotations

import argparse
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


def validate_report(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)
    visible, fence_errors = _visible_lines(lines)
    return (
        fence_errors
        + _document_errors(lines, visible)
        + _link_errors(lines, visible)
        + _list_errors(lines, visible)
        + _one_engineer_errors(lines, visible)
        + _investment_errors(lines, visible)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        content = args.report.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: cannot read report: {error}", file=sys.stderr)
        return 2

    errors = validate_report(content)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("Report format is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
