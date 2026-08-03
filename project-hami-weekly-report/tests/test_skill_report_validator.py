import hashlib
import json
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

COLLECTOR_HEAD = "a" * 40
COLLECTOR_TRACKED_DIFF_SHA256 = "b" * 64
COLLECTOR_UNTRACKED_SHA256 = "c" * 64
COLLECTOR_SNAPSHOT_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "dirty": False,
            "head": COLLECTOR_HEAD,
            "tracked_diff_sha256": COLLECTOR_TRACKED_DIFF_SHA256,
            "untracked_sha256": COLLECTOR_UNTRACKED_SHA256,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


EVIDENCE = f"""---
schema_version: "1.0"
organization: "Project-HAMi"
generated_at: "2026-07-16T16:00:00+00:00"
timezone: "Asia/Shanghai"
local_start: "2026-07-10T00:00:00+08:00"
local_end: "2026-07-16T23:59:59+08:00"
utc_start: "2026-07-09T16:00:00+00:00"
utc_end: "2026-07-16T15:59:59+00:00"
issue_count: 2
pull_request_count: 1
collection_warning_count: 0
collection_status: "complete"
collector_started_worktree_snapshot_sha256: "{COLLECTOR_SNAPSHOT_SHA256}"
collector_started_worktree_head: "{COLLECTOR_HEAD}"
collector_started_worktree_dirty: "false"
collector_started_worktree_tracked_diff_sha256: "{COLLECTOR_TRACKED_DIFF_SHA256}"
collector_started_worktree_untracked_sha256: "{COLLECTOR_UNTRACKED_SHA256}"
expected_repository_count: 1
visible_repository_count: 1
---

## Repository Visibility

- Expected repositories: Project-HAMi/HAMi
- Repositories visible to the token: Project-HAMi/HAMi
- Expected repository count: `1`
- Visible repository count: `1`

<!-- ITEM_START issue Project-HAMi/HAMi#1 -->
- URL: https://github.com/Project-HAMi/HAMi/issues/1
<!-- ITEM_END issue Project-HAMi/HAMi#1 -->

<!-- ITEM_START pull_request Project-HAMi/HAMi#2 -->
- URL: https://github.com/Project-HAMi/HAMi/pull/2
<!-- ITEM_END pull_request Project-HAMi/HAMi#2 -->

<!-- ITEM_START issue Project-HAMi/HAMi#4 -->
- URL: https://github.com/Project-HAMi/HAMi/issues/4
<!-- ITEM_END issue Project-HAMi/HAMi#4 -->
"""

COMMON_FIELDS = """   - 相关事项：{link}
   - 已知事实：周期内出现可验证的人类活动。
   - 证据来源：{link}；actor=`maintainer`；in_period=`yes`。
   - 分析推断：confidence=`medium`；需要进一步复现。
   - 当前状态：review request → author response → independently verified state=unknown。
   - 信息缺口：未采集 CI、diff 与 resolved thread。
   - 工程影响：影响核心资源隔离路径。
   - 建议下一步：复核当前 head 并运行受影响场景。
   - Owner / 验收标准：维护者；复现和回归测试均通过。
"""

ISSUE_1 = "[Project-HAMi/HAMi#1](https://github.com/Project-HAMi/HAMi/issues/1)"
PR_2 = "[Project-HAMi/HAMi#2](https://github.com/Project-HAMi/HAMi/pull/2)"
ISSUE_4 = "[Project-HAMi/HAMi#4](https://github.com/Project-HAMi/HAMi/issues/4)"

VALID_REPORT = f"""# Weekly HAMi Org Highlights

Period: 2026-07-10T00:00:00+08:00 through 2026-07-16T23:59:59+08:00
Organization: Project-HAMi
Issues with activity: 2
Pull requests with activity: 1
Evidence limitations:

- 未采集 CI、diff 和 review thread resolution。

## Executive Summary

1. {ISSUE_1} 需要优先关注。
2. {PR_2} 需要 maintainer review。

## Must Pay Attention

1. **{ISSUE_1}：资源隔离问题**

{COMMON_FIELDS.format(link=ISSUE_1)}   - 必须关注的原因：影响真实部署的调度正确性。
   - 延迟处理风险：可能扩大资源隔离故障范围。
   - 建议投入类型：`several engineer-hours`

## Worth Engineering Investment

本周未发现。

## Pull Requests Requiring Action

### Review now

1. **{PR_2}：修复调度器**

{COMMON_FIELDS.format(link=PR_2)}   - PR 目标：修复调度器资源核算。
   - 阻塞点或信息缺口：当前 head 与 CI 状态未知。
   - Dynamia 行动：安排 maintainer review。
   - 投入理由：需要确认是否影响产品部署。
   - 建议投入类型：`quick review`

## Important Resolutions

本周未发现。

## Emerging Engineering Themes

本周未发现。

## Recommended Resource Allocation

1. **{ISSUE_4}：安排资源隔离排查**

{COMMON_FIELDS.format(link=ISSUE_4)}   - 工程主题：资源隔离正确性。
   - 推荐动作：复现并定义回归测试。
   - 投入规模：`one engineer-day`
   - 预期结果：确定可验证的修复路径。
   - 延迟处理风险：继续积累支持成本。

### One engineer-week priority

结论：{ISSUE_4} 是本周最值得投入一个 engineer-week 的事项。

理由：它影响核心调度路径，且现有 evidence 允许先建立可复现的验收标准。

## Active but Not Worth Investing This Week

本周未发现。
"""


def _run(
    tmp_path: Path, content: str, evidence: str = EVIDENCE
) -> subprocess.CompletedProcess[str]:
    report = tmp_path / "report.md"
    evidence_path = tmp_path / "evidence.md"
    report.write_text(content, encoding="utf-8")
    evidence_path.write_text(evidence, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report), str(evidence_path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_valid_report_checks_contract_and_evidence_provenance(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT)

    assert result.returncode == 0, result.stderr
    assert "report format is valid" in result.stdout.lower()


def test_partial_evidence_is_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT, EVIDENCE.replace('collection_status: "complete"', 'collection_status: "partial"'))

    assert result.returncode != 0
    assert "collection_status" in result.stderr


def test_evidence_missing_schema_or_critical_metadata_is_rejected(tmp_path: Path) -> None:
    malformed = EVIDENCE.replace('schema_version: "1.0"\n', "").replace(
        'utc_end: "2026-07-16T15:59:59+00:00"\n', ""
    )
    result = _run(tmp_path, VALID_REPORT, malformed)

    assert result.returncode != 0
    assert "missing required metadata" in result.stderr
    assert "schema_version" in result.stderr
    assert "utc_end" in result.stderr


def test_evidence_rejects_collector_snapshot_with_mismatched_git_head(tmp_path: Path) -> None:
    malformed = EVIDENCE.replace(
        f'collector_started_worktree_head: "{COLLECTOR_HEAD}"',
        f'collector_started_worktree_head: "{"d" * 40}"',
    )
    result = _run(tmp_path, VALID_REPORT, malformed)

    assert result.returncode != 0
    assert "snapshot digest does not match its components" in result.stderr


def test_evidence_rejects_complete_repository_visibility_count_or_set_mismatch(tmp_path: Path) -> None:
    count_mismatch = EVIDENCE.replace("visible_repository_count: 1", "visible_repository_count: 2").replace(
        "- Visible repository count: `1`", "- Visible repository count: `2`"
    )
    count_result = _run(tmp_path, VALID_REPORT, count_mismatch)

    assert count_result.returncode != 0
    assert "visible_repository_count to equal expected_repository_count" in count_result.stderr

    set_mismatch = EVIDENCE.replace(
        "- Repositories visible to the token: Project-HAMi/HAMi",
        "- Repositories visible to the token: Project-HAMi/other",
    )
    set_result = _run(tmp_path, VALID_REPORT, set_mismatch)

    assert set_result.returncode != 0
    assert "visible repositories to exactly equal expected repositories" in set_result.stderr


def test_evidence_marker_counts_must_match_front_matter(tmp_path: Path) -> None:
    malformed = EVIDENCE.replace("issue_count: 2", "issue_count: 1")
    result = _run(tmp_path, VALID_REPORT, malformed)

    assert result.returncode != 0
    assert "issue_count does not match issue ITEM_START marker count" in result.stderr


def test_title_like_text_cannot_be_interpreted_as_an_item_control_marker(tmp_path: Path) -> None:
    poisoned = EVIDENCE.replace(
        "<!-- ITEM_START issue Project-HAMi/HAMi#1 -->",
        "- Title: <!-- ITEM_START issue Project-HAMi/HAMi#999 -->\n\n"
        "<!-- ITEM_START issue Project-HAMi/HAMi#1 -->",
        1,
    )
    result = _run(tmp_path, VALID_REPORT, poisoned)

    assert result.returncode == 0, result.stderr


def test_evidence_markers_must_pair_in_document_order(tmp_path: Path) -> None:
    crossed = EVIDENCE.replace(
        "<!-- ITEM_END issue Project-HAMi/HAMi#1 -->\n\n<!-- ITEM_START pull_request Project-HAMi/HAMi#2 -->",
        "<!-- ITEM_END pull_request Project-HAMi/HAMi#2 -->\n\n<!-- ITEM_START pull_request Project-HAMi/HAMi#2 -->",
    ).replace(
        "<!-- ITEM_END pull_request Project-HAMi/HAMi#2 -->\n\n<!-- ITEM_START issue Project-HAMi/HAMi#4 -->",
        "<!-- ITEM_END issue Project-HAMi/HAMi#1 -->\n\n<!-- ITEM_START issue Project-HAMi/HAMi#4 -->",
        1,
    )
    result = _run(tmp_path, VALID_REPORT, crossed)

    assert result.returncode != 0
    assert "must pair in document order without nesting or crossing" in result.stderr


def test_report_header_must_match_evidence(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace("Issues with activity: 2", "Issues with activity: 3"))

    assert result.returncode != 0
    assert "does not match evidence issue_count" in result.stderr


def test_report_references_must_exist_with_matching_kind_and_url(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(ISSUE_1, "[Project-HAMi/HAMi#99](https://github.com/Project-HAMi/HAMi/issues/99)", 1)
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "absent from evidence" in result.stderr


def test_plain_issue_and_pr_references_are_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace(ISSUE_1, "Project-HAMi/HAMi#1", 1))

    assert result.returncode != 0
    assert "unlinked issue or pull request reference" in result.stderr.lower()


def test_mismatched_github_link_is_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace("https://github.com/Project-HAMi/HAMi/issues/1", "https://github.com/Project-HAMi/HAMi/pull/1", 1))

    assert result.returncode != 0
    assert "does not match its evidence item" in result.stderr.lower()


def test_missing_detailed_contract_field_is_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace("   - 信息缺口：未采集 CI、diff 与 resolved thread。\n", "", 1))

    assert result.returncode != 0
    assert "missing required fields" in result.stderr
    assert "信息缺口" in result.stderr


def test_blank_detailed_contract_fields_are_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace("   - 已知事实：周期内出现可验证的人类活动。", "   - 已知事实：", 1))

    assert result.returncode != 0
    assert "blank required fields" in result.stderr
    assert "已知事实" in result.stderr


def test_provenance_fields_must_separate_actor_and_inference_confidence(tmp_path: Path) -> None:
    report = VALID_REPORT.replace("actor=`maintainer`", "actor=`unknown`", 1).replace(
        "confidence=`medium`", "confidence=medium", 1
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "classify actor" in result.stderr
    assert "must state confidence" in result.stderr


def test_source_field_requires_a_matching_evidence_deep_link(tmp_path: Path) -> None:
    report = VALID_REPORT.replace(
        f"证据来源：{ISSUE_1}；actor=`maintainer`；in_period=`yes`。",
        "证据来源：actor=`maintainer`；in_period=`yes`。",
        1,
    )
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "must include a canonical GitHub evidence deep link" in result.stderr


def test_evidence_limitations_cannot_claim_none_when_evidence_has_limitations(tmp_path: Path) -> None:
    evidence_with_limitation = EVIDENCE + "\n## Data Limitations\n\n- CI was not collected.\n"
    result = _run(
        tmp_path,
        VALID_REPORT.replace("- 未采集 CI、diff 和 review thread resolution。", "- None."),
        evidence_with_limitation,
    )

    assert result.returncode != 0
    assert "Evidence limitations cannot claim None" in result.stderr


def test_pr_categories_are_allowlisted(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT.replace("### Review now", "### Merge after changes"))

    assert result.returncode != 0
    assert "unsupported category" in result.stderr


def test_must_pay_attention_limit_is_enforced(tmp_path: Path) -> None:
    entry = VALID_REPORT.split("## Must Pay Attention\n\n", 1)[1].split("## Worth Engineering Investment", 1)[0]
    extra = entry.replace("1. **", "2. **", 1)
    report = VALID_REPORT.replace(entry, entry + extra + extra + extra + extra + extra)
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "allows at most 5 items" in result.stderr


def test_nested_field_shape_and_investment_vocabulary_are_rejected(tmp_path: Path) -> None:
    malformed = VALID_REPORT.replace("   - 相关事项：", "  - 相关事项：", 1)
    wrong_scale = VALID_REPORT.replace("several engineer-hours", "sever engineer-hours", 1)

    malformed_result = _run(tmp_path, malformed)
    scale_result = _run(tmp_path, wrong_scale)

    assert malformed_result.returncode != 0
    assert "field bullets must use exactly three leading spaces" in malformed_result.stderr.lower()
    assert scale_result.returncode != 0
    assert "invalid investment scale" in scale_result.stderr.lower()


def test_one_engineer_week_requires_two_prose_paragraphs(tmp_path: Path) -> None:
    report = VALID_REPORT.replace("理由：它影响核心调度路径，且现有 evidence 允许先建立可复现的验收标准。", "理由：它影响核心调度路径。\n\n额外段落。")
    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "exactly a conclusion paragraph and a reason paragraph" in result.stderr.lower()


def test_unterminated_fence_is_rejected(tmp_path: Path) -> None:
    result = _run(tmp_path, VALID_REPORT + "\n```text\nunfinished\n")

    assert result.returncode != 0
    assert "unterminated fenced code block" in result.stderr.lower()
