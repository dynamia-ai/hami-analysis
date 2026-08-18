import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "weekly-hami-org-highlights"
    / "scripts"
    / "validate_report.py"
)
SKILL = SCRIPT.parents[1] / "SKILL.md"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "weekly_hami_report_validator_test", SCRIPT
)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
VALIDATOR_MODULE = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR_MODULE)

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
GATE_SCOPE_NOTE = (
    "范围：覆盖本周期 evidence 中全部活跃 PR；“活跃”指采集周期内存在活动，"
    "包含当前已关闭或已合并事项。普通 Issue 不适用当前六项门禁。"
    "`no_confirmed_violation` 与 `insufficient_evidence` 均不代表门禁已通过。"
)

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

## Active Contributions Not Meeting Contribution Gates

{GATE_SCOPE_NOTE}

本周未发现。
"""


def _gated_report() -> str:
    report = VALID_REPORT.replace(f"2. {PR_2} 需要 maintainer review。\n", "")
    action_start = report.index("## Pull Requests Requiring Action")
    action_end = report.index("## Important Resolutions")
    report = (
        report[:action_start]
        + "## Pull Requests Requiring Action\n\n本周未发现。\n\n"
        + report[action_end:]
    )
    gate_entry = f"""1. **{PR_2}：review 回复未回应具体意见**

{COMMON_FIELDS.format(link=PR_2)}   - 未满足的门禁：`review-replies`
   - 门禁判定依据：maintainer 明确确认作者回复没有回应具体 review 意见。
   - 恢复条件：作者针对该意见给出本人编写且可验证的技术回复。
   - 建议投入类型：`quick review`
"""
    return report.replace(
        f"## Active Contributions Not Meeting Contribution Gates\n\n{GATE_SCOPE_NOTE}\n\n本周未发现。",
        f"## Active Contributions Not Meeting Contribution Gates\n\n{GATE_SCOPE_NOTE}\n\n{gate_entry.rstrip()}",
    )


GATED_REPORT = _gated_report()


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


def test_confirmed_contribution_gate_entry_is_valid_and_self_contained(tmp_path: Path) -> None:
    result = _run(tmp_path, GATED_REPORT)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "section",
    (
        "Executive Summary",
        "Must Pay Attention",
        "Worth Engineering Investment",
        "Pull Requests Requiring Action",
        "Important Resolutions",
        "Emerging Engineering Themes",
        "Recommended Resource Allocation",
        "Active but Not Worth Investing This Week",
    ),
)
def test_confirmed_contribution_gate_item_cannot_leak_into_old_sections(
    tmp_path: Path, section: str
) -> None:
    report = GATED_REPORT.replace(
        f"## {section}\n",
        f"## {section}\n\n门禁事项不得重复：{PR_2}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_confirmed_contribution_gate_item_cannot_leak_into_one_engineer_answer(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "结论：",
        f"结论：{PR_2} 不得在这里出现；",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_encoded_contribution_gate_link_cannot_bypass_quarantine(tmp_path: Path) -> None:
    encoded = (
        "[Project-HAMi/HAMi&num;2]"
        "(https://github.com/Project-HAMi/HAMi/pull/%32)"
    )
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n门禁事项不得重复：{encoded}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "canonical [Project-HAMi/REPO#NUMBER](GitHub URL) form" in result.stderr
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_case_variant_contribution_gate_link_cannot_bypass_quarantine(
    tmp_path: Path,
) -> None:
    variant = (
        "[project-hami/hami#2]"
        "(http://github.com/project-hami/hami/pull/2)"
    )
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n门禁事项不得重复：{variant}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "canonical [Project-HAMi/REPO#NUMBER](GitHub URL) form" in result.stderr
    assert "Contribution Gate item must not appear outside" in result.stderr


@pytest.mark.parametrize(
    "url",
    (
        "/Project-HAMi/HAMi/pull/2",
        "/x/../Project-HAMi/HAMi/pull/2",
        "/%2e%2e/Project-HAMi/HAMi/pull/2",
        "/foo/%2e%2e/Project-HAMi/HAMi/pull/2",
        "//github.com/Project-HAMi/HAMi/pull/2",
        "https://www.github.com/Project-HAMi/HAMi/pull/2",
        "https:///github.com/Project-HAMi/HAMi/pull/2",
        "https://github.com.:443/Project-HAMi/HAMi/pull/2",
        r"https:\github.com\Project-HAMi\HAMi\pull\2",
        "https://github.com/Project-HAMi/HAMi/x/../pull/2",
        "https://github.com/Project-HAMi/x/../HAMi/pull/2",
        "https://github.com/Project-HAMi/HAMi/%2e%2e/HAMi/pull/2",
        "https://github.com//Project-HAMi//HAMi//pull//2",
        "https://github。com/Project-HAMi/HAMi/pull/2",
    ),
)
def test_alternate_github_url_forms_cannot_bypass_quarantine(
    tmp_path: Path, url: str
) -> None:
    variant = f"[same PR]({url})"
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n门禁事项不得重复：{variant}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "canonical [Project-HAMi/REPO#NUMBER](GitHub URL) form" in result.stderr
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_contribution_gate_entry_cannot_reference_another_item(tmp_path: Path) -> None:
    report = GATED_REPORT.replace(
        f"   - 相关事项：{PR_2}",
        f"   - 相关事项：{PR_2}、{ISSUE_1}",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "must not reference other items" in result.stderr


def test_encoded_other_item_cannot_hide_inside_contribution_gate_entry(tmp_path: Path) -> None:
    encoded = (
        "[Project-HAMi/HAMi&num;1]"
        "(https://github.com/Project-HAMi/HAMi/issues/%31)"
    )
    report = GATED_REPORT.replace(
        f"   - 相关事项：{PR_2}",
        f"   - 相关事项：{PR_2}、{encoded}",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "must not reference other items" in result.stderr


@pytest.mark.parametrize(
    ("replacement", "message"),
    (
        ("`unknown-gate`", "unsupported IDs"),
        (
            "`review-replies`、`author-understanding`",
            "must follow the Contribution Gates policy order",
        ),
        ("`review-replies`、`review-replies`", "must not contain duplicate IDs"),
        (
            "hardware-validation、`review-replies`",
            "must contain only backticked Contribution Gate IDs separated by 、",
        ),
    ),
)
def test_contribution_gate_ids_are_allowlisted_unique_and_policy_ordered(
    tmp_path: Path, replacement: str, message: str
) -> None:
    report = GATED_REPORT.replace("`review-replies`", replacement, 1)

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert message in result.stderr


def test_contribution_gate_ids_accept_multiple_values_in_policy_order(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "`review-replies`",
        "`author-understanding`、`review-replies`",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode == 0, result.stderr


def test_contribution_gate_scope_note_stays_synchronized_with_skill() -> None:
    assert GATE_SCOPE_NOTE == VALIDATOR_MODULE.CONTRIBUTION_GATE_SCOPE_NOTE
    assert SKILL.read_text(encoding="utf-8").count(GATE_SCOPE_NOTE) == 2


def test_contribution_gate_section_requires_scope_note_and_gate_fields(tmp_path: Path) -> None:
    missing_scope = _run(tmp_path, GATED_REPORT.replace(GATE_SCOPE_NOTE, "范围：候选事项。", 1))
    missing_field = _run(
        tmp_path,
        GATED_REPORT.replace("   - 恢复条件：作者针对该意见给出本人编写且可验证的技术回复。\n", "", 1),
    )

    assert missing_scope.returncode != 0
    assert "exact candidate-pool scope note once" in missing_scope.stderr
    assert missing_field.returncode != 0
    assert "恢复条件" in missing_field.stderr


def test_contribution_gate_section_rejects_duplicate_basis_field(tmp_path: Path) -> None:
    report = GATED_REPORT.replace(
        "   - 恢复条件：",
        "   - 门禁判定依据：另一条更强的未审计指控。\n   - 恢复条件：",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "exactly one of each gate field" in result.stderr


@pytest.mark.parametrize(
    "encoded_field",
    (
        "   - 未满足的门禁&#58; `hardware-validation`",
        "   - 门禁判定依据&colon; 未经审计的更严重指控。",
        "   - 恢复条件&#xFF1A; 无。",
    ),
)
def test_contribution_gate_section_rejects_html_encoded_reserved_fields(
    tmp_path: Path, encoded_field: str
) -> None:
    report = GATED_REPORT.replace(
        "   - 恢复条件：",
        f"{encoded_field}\n   - 恢复条件：",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "must use literal canonical field lines" in result.stderr


@pytest.mark.parametrize(
    "smuggled_field",
    (
        "未满足的门禁：`hardware-validation`",
        "> 未满足的门禁：`hardware-validation`",
        "**未满足的门禁**：`hardware-validation`",
        "未满足的**门禁**：`hardware-validation`",
        "未满足的`门禁`：`hardware-validation`",
        "门禁判定**依据**：未经审计的指控。",
        "恢**复**条件：无。",
        "> 未满足的**门禁**&#58; `hardware-validation`",
        "<span>门禁判定依据</span>：未经审计的指控。",
        "未满足<!-- -->的门禁：`hardware-validation`",
        "未满足的[门禁][gate]：`hardware-validation`\n[gate]: https://example.com",
    ),
)
def test_contribution_gate_section_rejects_noncanonical_rendered_gate_fields(
    tmp_path: Path, smuggled_field: str
) -> None:
    report = GATED_REPORT.replace(
        "   - 恢复条件：",
        f"{smuggled_field}\n   - 恢复条件：",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert (
        "must use literal canonical field lines" in result.stderr
        or "raw HTML is not allowed" in result.stderr
    )


@pytest.mark.parametrize(
    "raw_link",
    (
        '<a href="https://github.com/Project-HAMi/HAMi/pull/2">same PR</a>',
        '<a href="//github.com/Project-HAMi/HAMi/pull/2">same PR</a>',
        '<a href="https://github.com/Project-\tHAMi/HAMi/pull/2">same PR</a>',
    ),
)
def test_raw_html_links_cannot_bypass_contribution_gate_quarantine(
    tmp_path: Path, raw_link: str
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n门禁事项不得重复：{raw_link}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "GitHub item URLs require the canonical" in result.stderr
    assert "Contribution Gate item must not appear outside" in result.stderr


@pytest.mark.parametrize(
    "raw_html",
    (
        "Project-<span>HAMi</span>/HAMi#2",
        "<!-- hide the remainder of the report",
        "<details>\n<summary>collapsed contribution</summary>",
        "<?",
        "<!DOCTYPE",
        "<![CDATA[",
        "<details",
        "<script",
    ),
)
def test_raw_html_is_rejected_anywhere_in_visible_report(
    tmp_path: Path, raw_html: str
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n{raw_html}\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "raw HTML is not allowed in the report" in result.stderr


def test_reference_style_link_cannot_bypass_contribution_gate_quarantine(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        "## Executive Summary\n\n门禁事项不得重复：[Project-HAMi][org]/HAMi#2\n"
        "[org]: https://example.com\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "reference-style Markdown links are not allowed" in result.stderr


def test_fenced_reference_cannot_bypass_contribution_gate_quarantine(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        "## Executive Summary\n\n```markdown\n"
        f"{PR_2}\n"
        "```\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_fenced_plain_label_cannot_bypass_contribution_gate_quarantine(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        "## Executive Summary\n\n```text\nProject-HAMi/HAMi#2\n```\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_contribution_gate_section_rejects_fenced_duplicate_gate_field(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "   - 恢复条件：",
        "```text\n未满足的门禁：`hardware-validation`\n```\n"
        "   - 恢复条件：",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "must not contain fenced code blocks" in result.stderr


@pytest.mark.parametrize(
    "smuggled_field",
    (
        "未满足的门禁：`hardware-validation`",
        "> 门禁判定依据：未经审计。",
    ),
)
def test_contribution_gate_section_rejects_gate_fields_before_first_entry(
    tmp_path: Path, smuggled_field: str
) -> None:
    report = GATED_REPORT.replace(
        f"{GATE_SCOPE_NOTE}\n\n",
        f"{GATE_SCOPE_NOTE}\n\n{smuggled_field}\n\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "only allowed as canonical fields inside an entry" in result.stderr


@pytest.mark.parametrize(
    "plain_reference",
    (
        "github.com/Project-HAMi/HAMi/pull/2",
        "www.github.com/Project-HAMi/HAMi/pull/2",
        "Project-HAMi/HAMi/pull/2",
    ),
)
def test_plain_path_references_cannot_bypass_contribution_gate_quarantine(
    tmp_path: Path, plain_reference: str
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n门禁事项不得重复：{plain_reference}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "GitHub item URLs require the canonical" in result.stderr
    assert "Contribution Gate item must not appear outside" in result.stderr


def test_bare_hash_reference_is_rejected_instead_of_bypassing_quarantine(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        "## Executive Summary\n\n门禁事项不得重复：PR #2。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "unlinked issue or pull request reference" in result.stderr


@pytest.mark.parametrize(
    "rendered_reference",
    (
        "Project-HAMi/HAMi#\u200b2",
        "Project-**HAMi**/HAMi#2",
    ),
)
def test_rendered_plain_references_cannot_bypass_quarantine(
    tmp_path: Path, rendered_reference: str
) -> None:
    report = GATED_REPORT.replace(
        "## Executive Summary\n",
        f"## Executive Summary\n\n门禁事项不得重复：{rendered_reference}。\n",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "unlinked issue or pull request reference" in result.stderr


def test_contribution_gate_section_rejects_empty_marker_with_entries(
    tmp_path: Path,
) -> None:
    report = GATED_REPORT.replace(
        GATE_SCOPE_NOTE,
        f"{GATE_SCOPE_NOTE}\n\n本周未发现。",
        1,
    )

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "must not claim 本周未发现 when entries exist" in result.stderr


def test_contribution_gate_section_is_required(tmp_path: Path) -> None:
    report = VALID_REPORT.split("## Active Contributions Not Meeting Contribution Gates", 1)[0]

    result = _run(tmp_path, report)

    assert result.returncode != 0
    assert "required report sections must appear exactly once" in result.stderr
