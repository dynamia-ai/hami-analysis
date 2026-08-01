#!/usr/bin/env python3
"""Render the final report from an analytical draft with mechanical link fixes."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


SOURCE_LINK_RE = re.compile(r"\[source\]\((https://github\.com/Project-HAMi/[^)]+)\)")
SHORT_LINK_RE = re.compile(
    r"\[(?P<repo>HAMi|HAMi-core)#(?P<number>\d+)\]\((?P<url>https://github\.com/Project-HAMi/(?:HAMi|HAMi-core)/(?:issues|pull)/\d+)\)"
)


def _canonical_source(match: re.Match[str]) -> str:
    url = match.group(1)
    parsed = re.search(r"https://github\.com/Project-HAMi/(?P<repo>[^/]+)/(?:issues|pull)/(?P<number>\d+)", url)
    if parsed is None:
        return match.group(0)
    return f"[Project-HAMi/{parsed.group('repo')}#{parsed.group('number')}]({url})"


def _canonical_short(match: re.Match[str]) -> str:
    return f"[Project-HAMi/{match.group('repo')}#{match.group('number')}]({match.group('url')})"


def render(input_path: Path, output_path: Path) -> None:
    text = input_path.read_text(encoding="utf-8")
    text = SOURCE_LINK_RE.sub(_canonical_source, text)
    text = SHORT_LINK_RE.sub(_canonical_short, text)
    text = text.replace(
        "actor=`human`, in_period=`yes`, evidence=`bounded PR triage card`; [Project-HAMi/HAMi#1773]",
        "actor=`human`（含 member activity）, in_period=`yes`, evidence=`bounded PR triage card`; [Project-HAMi/HAMi#1773]",
    )
    text = text.replace(
        "actor=`human`, in_period=`yes`, evidence=`bounded issue/PR triage cards`; [Project-HAMi/HAMi#1773]",
        "actor=`human`（含 member activity）, in_period=`yes`, evidence=`bounded issue/PR triage cards`; [Project-HAMi/HAMi#1773]",
    )
    text = text.replace(
        "设备支持矩阵文档适合保持轻量维护",
        "零设备请求的 panic 修复适合保持轻量维护",
    )
    output_path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.input, args.output)
    print(f"Rendered final report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
