from pathlib import Path
import subprocess
import sys


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "weekly-hami-org-highlights"
    / "scripts"
    / "validate_report.py"
)


VALID_REPORT = """# Weekly HAMi Org Highlights

## Executive Summary

1. [Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1) 需要关注。
2. [Project-HAMi/HAMi#2](https://github.com/Project-HAMi/HAMi/pull/2) 需要 review。

## Must Pay Attention

1. **[Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)：资源隔离问题**

   - 相关事项：[Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)
   - 已知事实：周期内有人类活动。
   - 建议投入类型：`several engineer-hours`

## Worth Engineering Investment

本周未发现。

## Pull Requests Requiring Action

### Review now

1. **[Project-HAMi/HAMi#2](https://github.com/Project-HAMi/HAMi/pull/2)：修复调度器**

   - 当前状态：Open。
   - Dynamia 行动：安排 maintainer review。

## Important Resolutions

本周未发现。

## Emerging Engineering Themes

本周未发现。

## Recommended Resource Allocation

1. **[Project-HAMi/HAMi#4](https://github.com/Project-HAMi/HAMi/issues/4)：安排资源隔离排查**

   - 建议投入类型：`one engineer-day`

### One engineer-week priority

结论：[Project-HAMi/HAMi#4](https://github.com/Project-HAMi/HAMi/issues/4) 是本周最值得投入一个 engineer-week 的事项。

理由：它影响核心调度路径，且已经具备明确的复现信息。

## Active but Not Worth Investing This Week

1. **[Project-HAMi/HAMi#3](https://github.com/Project-HAMi/HAMi/issues/3)：等待报告者补充**

   - 暂不投入原因：缺少复现信息。
"""


def _run(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    report = tmp_path / "report.md"
    report.write_text(content)
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_valid_report_uses_linked_references_and_ordered_top_level_items(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT)

    assert result.returncode == 0, result.stderr
    assert "report format is valid" in result.stdout.lower()


def test_plain_issue_and_pr_references_are_rejected(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "[Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)",
        "Project-HAMi/HAMi#1",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "unlinked issue or pull request reference" in result.stderr.lower()
    assert "Project-HAMi/HAMi#1" in result.stderr


def test_mismatched_github_link_is_rejected(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "https://github.com/Project-HAMi/HAMi/issues/1",
        "https://github.com/Project-HAMi/HAMi/pull/99",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "does not match its github url" in result.stderr.lower()


def test_github_item_url_requires_the_canonical_link_label(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "[Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)",
        "[resource isolation issue](https://github.com/Project-HAMi/HAMi/issues/1)",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "canonical [project-hami/repo#number]" in result.stderr.lower()


def test_analytic_sections_reject_bullet_or_numbered_heading_top_level_items(tmp_path: Path) -> None:
    bullet = VALID_REPORT.replace(
        "1. **[Project-HAMi/HAMi#3]",
        "- **[Project-HAMi/HAMi#3]",
    )
    numbered_heading = VALID_REPORT.replace(
        "1. **[Project-HAMi/HAMi#1]",
        "### 1. [Project-HAMi/HAMi#1]",
    )

    bullet_result = _run(tmp_path, bullet)
    heading_result = _run(tmp_path, numbered_heading)

    assert bullet_result.returncode != 0
    assert "top-level entries must use an ordered list" in bullet_result.stderr.lower()
    assert heading_result.returncode != 0
    assert "numbered headings are not report entries" in heading_result.stderr.lower()


def test_first_ordered_item_requires_a_blank_line(tmp_path: Path) -> None:
    report = VALID_REPORT.replace("## Executive Summary\n\n1.", "## Executive Summary\n1.")
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "blank line before the ordered list" in result.stderr.lower()


def test_nested_field_bullets_require_three_spaces(tmp_path: Path) -> None:
    report = VALID_REPORT.replace("   - 相关事项：", "  - 相关事项：", 1)
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "field bullets must use exactly three leading spaces" in result.stderr.lower()


def test_investment_scale_must_use_the_exact_vocabulary(tmp_path: Path) -> None:
    report = VALID_REPORT.replace("several engineer-hours", "sever engineer-hours")
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "invalid investment scale" in result.stderr.lower()


def test_title_and_complete_section_order_are_required(tmp_path: Path) -> None:
    variants = (
        "",
        VALID_REPORT.replace("# Weekly HAMi Org Highlights", "# Weekly report", 1),
        VALID_REPORT.replace("## Important Resolutions\n\n本周未发现。\n\n", "", 1),
        VALID_REPORT.replace(
            "## Important Resolutions\n\n本周未发现。\n\n"
            "## Emerging Engineering Themes\n\n本周未发现。",
            "## Emerging Engineering Themes\n\n本周未发现。\n\n"
            "## Important Resolutions\n\n本周未发现。",
            1,
        ),
        VALID_REPORT.replace("## Executive Summary", "## Executive Summary ", 1),
    )

    for report in variants:
        result = _run(tmp_path, report)
        assert result.returncode != 0
        assert "required report" in result.stderr.lower()


def test_crlf_empty_section_is_rejected(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "1. [Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1) 需要关注。\n"
        "2. [Project-HAMi/HAMi#2](https://github.com/Project-HAMi/HAMi/pull/2) 需要 review。\n",
        "",
        1,
    ).replace("\n", "\r\n")
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "expected an ordered list" in result.stderr.lower()


def test_all_top_level_bullet_markers_are_rejected(tmp_path: Path) -> None:
    for marker in ("*", "+"):
        report = VALID_REPORT.replace(
            "1. **[Project-HAMi/HAMi#3]",
            f"{marker} **[Project-HAMi/HAMi#3]",
            1,
        )
        result = _run(tmp_path, report)
        assert result.returncode != 0
        assert "top-level entries must use an ordered list" in result.stderr.lower()


def test_field_bullets_reject_tabs_and_other_markers(tmp_path: Path) -> None:
    variants = (
        VALID_REPORT.replace("   - 相关事项：", "\t- 相关事项：", 1),
        VALID_REPORT.replace("   - 相关事项：", "   * 相关事项：", 1),
    )
    for report in variants:
        result = _run(tmp_path, report)
        assert result.returncode != 0
        assert "field bullets must use exactly three leading spaces and '-'" in result.stderr.lower()


def test_first_field_bullet_requires_a_blank_line(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "：资源隔离问题**\n\n   - 相关事项：",
        "：资源隔离问题**\n   - 相关事项：",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "blank line before the nested fields" in result.stderr.lower()


def test_each_pr_category_list_requires_a_blank_line(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "   - Dynamia 行动：安排 maintainer review。",
        "   - Dynamia 行动：安排 maintainer review。\n\n"
        "### Merge after changes\n"
        "2. **[Project-HAMi/HAMi#5](https://github.com/Project-HAMi/HAMi/pull/5)：补测试**",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "blank line before the ordered list" in result.stderr.lower()


def test_ordered_items_must_not_jump_or_repeat(tmp_path: Path) -> None:
    for replacement in ("3.", "1."):
        report = VALID_REPORT.replace(
            "2. [Project-HAMi/HAMi#2]",
            f"{replacement} [Project-HAMi/HAMi#2]",
            1,
        )
        result = _run(tmp_path, report)
        assert result.returncode != 0
        assert "ordered items must be sequential" in result.stderr.lower()


def test_one_engineer_week_heading_and_prose_shape_are_required(tmp_path: Path) -> None:
    missing = VALID_REPORT.replace(
        "### One engineer-week priority\n\n"
        "结论：[Project-HAMi/HAMi#4](https://github.com/Project-HAMi/HAMi/issues/4) 是本周最值得投入一个 engineer-week 的事项。\n\n"
        "理由：它影响核心调度路径，且已经具备明确的复现信息。\n\n",
        "",
        1,
    )
    list_continuation = VALID_REPORT.replace(
        "结论：[Project-HAMi/HAMi#4]",
        "2. [Project-HAMi/HAMi#4]",
        1,
    )

    missing_result = _run(tmp_path, missing)
    continuation_result = _run(tmp_path, list_continuation)

    assert missing_result.returncode != 0
    assert "one engineer-week priority" in missing_result.stderr.lower()
    assert continuation_result.returncode != 0
    assert "must contain prose, not list items" in continuation_result.stderr.lower()


def test_one_engineer_week_requires_exactly_two_plain_paragraphs(tmp_path: Path) -> None:
    extra_heading = VALID_REPORT.replace(
        "### One engineer-week priority\n\n结论：",
        "### One engineer-week priority\n\n### Bogus\n\n结论：",
        1,
    )
    third_paragraph = VALID_REPORT.replace(
        "理由：它影响核心调度路径，且已经具备明确的复现信息。",
        "理由：它影响核心调度路径，且已经具备明确的复现信息。\n\n额外段落。",
        1,
    )

    for report in (extra_heading, third_paragraph):
        result = _run(tmp_path, report)
        assert result.returncode != 0
        assert "exactly a conclusion paragraph and a reason paragraph" in result.stderr.lower()


def test_investment_field_aliases_and_multiple_values_are_rejected(tmp_path: Path) -> None:
    alias = VALID_REPORT.replace("建议投入类型：", "建议投入：", 1)
    multiple = VALID_REPORT.replace(
        "建议投入类型：`several engineer-hours`",
        "建议投入类型：`several engineer-hours`；投入规模：`sever engineer-hours`",
        1,
    )

    alias_result = _run(tmp_path, alias)
    multiple_result = _run(tmp_path, multiple)

    assert alias_result.returncode != 0
    assert "use the exact investment field name" in alias_result.stderr.lower()
    assert multiple_result.returncode != 0
    assert "invalid investment scale" in multiple_result.stderr.lower()


def test_fence_boundaries_cannot_hide_later_errors(tmp_path: Path) -> None:
    for fence in ("```text\nexample\n```   ", "~~~text\nexample\n~~~~"):
        report = VALID_REPORT.replace(
            "## Executive Summary",
            f"{fence}\n\n## Executive Summary",
            1,
        ).replace(
            "1. [Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)",
            "1. Project-HAMi/HAMi#1",
            1,
        )
        result = _run(tmp_path, report)
        assert result.returncode != 0
        assert "unlinked issue or pull request reference" in result.stderr.lower()


def test_unterminated_fence_is_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT + "\n```text\nunfinished\n")

    assert result.returncode != 0
    assert "unterminated fenced code block" in result.stderr.lower()


def test_github_item_images_do_not_count_as_links(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        "[Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)",
        "![Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "canonical [project-hami/repo#number]" in result.stderr.lower()


def test_csharp_version_text_is_not_an_issue_reference(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace("需要关注。", "涉及 C#11 兼容性。", 1))

    assert result.returncode == 0, result.stderr
