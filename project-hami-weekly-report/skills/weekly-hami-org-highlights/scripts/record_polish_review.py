#!/usr/bin/env python3
"""Record the Tech-Doc-Style-Chinese review of a completed weekly report."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sys


STYLE_SKILL_NAME = "tech-doc-style-chinese"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _skill_name(path: Path) -> str | None:
    content = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n.*?^name:\s*(?P<name>[^\r\n]+)\r?$", content, re.MULTILINE | re.DOTALL)
    return match.group("name").strip().strip('"\'') if match else None


def record_review(input_report: Path, report: Path, style_skill: Path, output: Path) -> None:
    input_report = input_report.resolve()
    report = report.resolve()
    style_skill = style_skill.resolve()
    if _skill_name(style_skill) != STYLE_SKILL_NAME:
        raise ValueError(f"style skill must declare name: {STYLE_SKILL_NAME}")
    review = {
        "schema_version": "1.0",
        "completed_at": datetime.now(UTC).isoformat(),
        "review_method": "manual review following Tech-Doc-Style-Chinese",
        "style_skill": {
            "name": STYLE_SKILL_NAME,
            "path": str(style_skill),
            "sha256": _sha256(style_skill),
        },
        "input_report": {"path": str(input_report), "sha256": _sha256(input_report)},
        "report": {"path": str(report), "sha256": _sha256(report)},
        "scope": {
            "reviewed": ["visible Chinese prose", "terminology", "punctuation", "Chinese-English spacing"],
            "preserved": [
                "report structural contract",
                "GitHub links and URLs",
                "actor and confidence annotations",
                "evidence-derived facts and limitations",
                "machine-readable identifiers and code literals",
            ],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True, help="the polished final report")
    parser.add_argument("--style-skill", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        record_review(args.input_report, args.report, args.style_skill, args.output)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote style review record: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
