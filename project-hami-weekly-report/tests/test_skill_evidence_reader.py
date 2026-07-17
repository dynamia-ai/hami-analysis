from pathlib import Path
import re
import subprocess
import sys


SCRIPT = Path(__file__).parents[1] / "skills" / "weekly-hami-org-highlights" / "scripts" / "evidence_reader.py"


def _evidence() -> str:
    issue_rows = "\n".join(
        [
            "| Project-HAMi/HAMi#1 | Project-HAMi/HAMi | 1 | First issue | open | yes | no | 1 | MEMBER | kind/bug | [evidence](#one) |",
            "| Project-HAMi/HAMi#2 | Project-HAMi/HAMi | 2 | Second issue | closed | no | yes | 0 | NONE | None | [evidence](#two) |",
        ]
    )
    return f"""---
schema_version: \"1.0\"
organization: \"Project-HAMi\"
issue_count: 2
pull_request_count: 1
collection_warning_count: 1
---

# Project-HAMi GitHub Activity Evidence

## Document Map

Read in segments.

## Collection Summary

- Issues retained: `2`

## Issues Index

| ID | Repository | Number | Title | State | Created in period | Closed in period | Period human activity | Author association | Labels | Section |
| --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- | --- |
{issue_rows}

## Pull Requests Index

| ID | Repository | Number | Title | State | Created in period | Merged in period | Closed unmerged in period | Period human activity | Author association | Labels | Section |
| --- | --- | ---: | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| Project-HAMi/HAMi#3 | Project-HAMi/HAMi | 3 | First PR | open | yes | no | no | 2 | CONTRIBUTOR | None | [evidence](#three) |

## Issue Evidence

<!-- ITEM_START issue Project-HAMi/HAMi#1 -->

### Issue: Project-HAMi/HAMi#1

#### Metadata

- URL: https://github.com/Project-HAMi/HAMi/issues/1

#### Activity During Scan Period

- Created in period: `yes`

#### Labels, Assignees and Milestone

- Labels: `kind/bug`

#### Body

````markdown
## Data Limitations

#### Previous Context

{"large body " * 200}
````

#### Previous Context

None.

#### Comments During Scan Period

large comment that must not appear in the triage view

#### Latest Human Comment

important human finding

#### Latest Maintainer Comment

None.

#### Data Gaps

None.

<!-- ITEM_END issue Project-HAMi/HAMi#1 -->

## Pull Request Evidence

<!-- ITEM_START pull_request Project-HAMi/HAMi#3 -->

### Pull Request: Project-HAMi/HAMi#3

#### Metadata

- URL: https://github.com/Project-HAMi/HAMi/pull/3

#### Activity During Scan Period

- Created in period: `yes`

#### Current Review Information

None.

#### Labels, Assignees and Requested Reviewers

None.

#### Change Size

- Changed files: `1`

#### Body

PR body

#### Previous Context

None.

#### Conversation Comments During Scan Period

None.

#### Reviews During Scan Period

None.

#### Review Comments During Scan Period

None.

#### Latest Human Activity

reviewed

#### Latest Maintainer Activity

None.

#### Data Gaps

None.

<!-- ITEM_END pull_request Project-HAMi/HAMi#3 -->

## Collection Warnings

- warning text

## Data Limitations

- limitation text
"""


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / "evidence.md"
    evidence.write_text(_evidence())
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, str(evidence)],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_with_content(
    tmp_path: Path, content: str, *args: str
) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / "custom-evidence.md"
    evidence.write_text(content)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, str(evidence)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_overview_returns_only_small_control_sections(tmp_path: Path) -> None:
    result = _run(tmp_path, "overview")

    assert result.returncode == 0, result.stderr
    assert 'organization: "Project-HAMi"' in result.stdout
    assert "## Collection Summary" in result.stdout
    assert "## Collection Warnings" in result.stdout
    assert "## Data Limitations" in result.stdout
    assert "## Issues Index" not in result.stdout
    assert "large body" not in result.stdout


def test_index_is_paginated_and_reports_the_next_offset(tmp_path: Path) -> None:
    result = _run(tmp_path, "index", "issue", "--offset", "0", "--limit", "1")

    assert result.returncode == 0, result.stderr
    assert "Project-HAMi/HAMi#1" in result.stdout
    assert "Project-HAMi/HAMi#2" not in result.stdout
    assert "items 1-1 of 2" in result.stderr
    assert "next offset: 1" in result.stderr


def test_index_rejects_more_than_fifty_rows(tmp_path: Path) -> None:
    result = _run(tmp_path, "index", "issue", "--limit", "51")

    assert result.returncode != 0
    assert "between 1 and 50" in result.stderr


def test_triage_item_view_excludes_unbounded_body_and_comment_sections(tmp_path: Path) -> None:
    result = _run(tmp_path, "item", "issue", "Project-HAMi/HAMi#1", "--view", "triage")

    assert result.returncode == 0, result.stderr
    assert "#### Metadata" in result.stdout
    assert "#### Activity During Scan Period" in result.stdout
    assert "#### Latest Human Comment" in result.stdout
    assert "#### Data Gaps" in result.stdout
    assert "#### Body" not in result.stdout
    assert "large body" not in result.stdout
    assert "large comment" not in result.stdout


def test_item_section_view_returns_only_the_requested_section(tmp_path: Path) -> None:
    result = _run(tmp_path, "item", "issue", "Project-HAMi/HAMi#1", "--view", "comments")

    assert result.returncode == 0, result.stderr
    assert "#### Comments During Scan Period" in result.stdout
    assert "large comment" in result.stdout
    assert "#### Body" not in result.stdout
    assert "large body" not in result.stdout
    assert "#### Latest Human Comment" not in result.stdout


def test_item_section_boundaries_ignore_headings_inside_fenced_bodies(tmp_path: Path) -> None:
    body = _run(tmp_path, "item", "issue", "Project-HAMi/HAMi#1", "--view", "body")
    previous = _run(
        tmp_path,
        "item",
        "issue",
        "Project-HAMi/HAMi#1",
        "--view",
        "previous_context",
    )

    assert body.returncode == 0, body.stderr
    assert "large body" in body.stdout
    assert previous.returncode == 0, previous.stderr
    assert "large body" not in previous.stdout
    assert "#### Comments During Scan Period" not in previous.stdout


def test_full_item_view_requires_bounded_chunks(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "item",
        "issue",
        "Project-HAMi/HAMi#1",
        "--view",
        "full",
        "--max-bytes",
        "400",
        "--chunk",
        "1",
    )

    assert result.returncode == 0, result.stderr
    assert "chunk 1/" in result.stderr
    assert len(result.stdout.encode()) <= 400
    assert "use --chunk" in result.stderr


def test_item_rejects_more_than_forty_thousand_bytes(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "item",
        "issue",
        "Project-HAMi/HAMi#1",
        "--max-bytes",
        "40001",
    )

    assert result.returncode != 0
    assert "between 256 and 40000" in result.stderr


def test_utf8_chunks_round_trip_without_exceeding_the_byte_limit(tmp_path: Path) -> None:
    content = _evidence().replace("large body " * 200, "中文🚀" * 300)
    complete = _run_with_content(
        tmp_path,
        content,
        "item",
        "issue",
        "Project-HAMi/HAMi#1",
        "--view",
        "body",
    )
    first = _run_with_content(
        tmp_path,
        content,
        "item",
        "issue",
        "Project-HAMi/HAMi#1",
        "--view",
        "body",
        "--max-bytes",
        "257",
        "--chunk",
        "1",
    )

    assert complete.returncode == 0, complete.stderr
    assert first.returncode == 0, first.stderr
    match = re.search(r"chunk 1/(\d+)", first.stderr)
    assert match is not None
    chunk_count = int(match.group(1))
    chunks = [first.stdout]
    for chunk_number in range(2, chunk_count + 1):
        chunk = _run_with_content(
            tmp_path,
            content,
            "item",
            "issue",
            "Project-HAMi/HAMi#1",
            "--view",
            "body",
            "--max-bytes",
            "257",
            "--chunk",
            str(chunk_number),
        )
        assert chunk.returncode == 0, chunk.stderr
        assert len(chunk.stdout.encode()) <= 257
        chunks.append(chunk.stdout)

    assert len(first.stdout.encode()) <= 257
    assert "".join(chunks) == complete.stdout
