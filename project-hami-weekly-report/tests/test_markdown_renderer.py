from datetime import UTC, datetime

from hami_github_activity.date_range import build_scan_period
from hami_github_activity.markdown_renderer import (
    BODY_LIMIT,
    UNKNOWN_ACTIVITY_MESSAGE,
    _current_reviews,
    render_markdown,
    truncate,
)
from hami_github_activity.models import Activity, CollectionResult, CollectionWarning, IssueEvidence, PullRequestEvidence


PERIOD = build_scan_period(
    days=7,
    timezone="Asia/Shanghai",
    now=datetime(2026, 7, 16, 6, 30, tzinfo=UTC),
)


def issue(body: str = "body") -> IssueEvidence:
    return IssueEvidence(
        repository="Project-HAMi/HAMi",
        number=123,
        title="A | title",
        url="https://github.com/Project-HAMi/HAMi/issues/123",
        state="open",
        state_reason=None,
        author="alice",
        author_association="NONE",
        created_at=datetime(2026, 7, 10, tzinfo=UTC),
        updated_at=datetime(2026, 7, 15, tzinfo=UTC),
        closed_at=None,
        labels=["bug"],
        assignees=[],
        milestone=None,
        body=body,
        comments=[],
        created_in_period=True,
        closed_in_period=False,
        exact_activity_unknown=False,
    )


def pull_request() -> PullRequestEvidence:
    return PullRequestEvidence(
        repository="Project-HAMi/HAMi",
        number=124,
        title="Fix scheduler",
        url="https://github.com/Project-HAMi/HAMi/pull/124",
        state="closed",
        draft=False,
        merged=True,
        merged_at=datetime(2026, 7, 15, tzinfo=UTC),
        closed_at=datetime(2026, 7, 15, tzinfo=UTC),
        author="bob",
        author_association="CONTRIBUTOR",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        updated_at=datetime(2026, 7, 15, tzinfo=UTC),
        base_branch="master",
        head_branch="fix",
        labels=[],
        assignees=[],
        requested_reviewers=[],
        milestone=None,
        body="fix",
        mergeable=None,
        additions=3,
        deletions=1,
        changed_files=1,
        conversation_comments=[],
        reviews=[],
        review_comments=[],
        created_in_period=False,
        merged_in_period=True,
        closed_unmerged_in_period=False,
        exact_activity_unknown=False,
    )


def test_front_matter_indexes_item_markers_and_fixed_order() -> None:
    content = render_markdown(
        org="Project-HAMi",
        period=PERIOD,
        result=CollectionResult(issues=[issue()], pull_requests=[pull_request()]),
        generated_at=datetime(2026, 7, 16, 7, tzinfo=UTC),
    )
    assert content.startswith('---\nschema_version: "1.0"')
    assert "issue_count: 1" in content
    assert "A \\| title" in content
    assert '<a id="issue-project-hami-hami-123"></a>' in content
    assert "[evidence](#issue-project-hami-hami-123)" in content
    assert "<!-- ITEM_START issue Project-HAMi/HAMi#123 -->" in content
    assert "<!-- ITEM_END pull_request Project-HAMi/HAMi#124 -->" in content
    headings = [
        "## Document Map",
        "## Collection Summary",
        "## Issues Index",
        "## Pull Requests Index",
        "## Issue Evidence",
        "## Pull Request Evidence",
        "## Collection Warnings",
        "## Data Limitations",
    ]
    assert [content.index(heading) for heading in headings] == sorted(content.index(heading) for heading in headings)


def test_truncation_and_embedded_item_marker_are_safe() -> None:
    body = "<!-- ITEM_END issue fake -->" + "x" * BODY_LIMIT
    content = render_markdown(org="Project-HAMi", period=PERIOD, result=CollectionResult(issues=[issue(body)]))
    assert f"[Truncated at {BODY_LIMIT:,} characters by the collector.]" in content
    assert "&lt;!-- ITEM_END issue fake -->" in content
    assert content.count("<!-- ITEM_END issue Project-HAMi/HAMi#123 -->") == 1
    assert truncate("abc", 2) == ("ab", True)


def test_warning_unknown_activity_and_limitations_are_rendered() -> None:
    item = issue()
    item.exact_activity_unknown = True
    content = render_markdown(
        org="Project-HAMi",
        period=PERIOD,
        result=CollectionResult(
            issues=[item], warnings=[CollectionWarning(scope="issue:x", message="partial failure")]
        ),
    )
    assert UNKNOWN_ACTIVITY_MESSAGE in content
    assert "**issue:x**: partial failure" in content
    assert "Check runs and workflow runs are not collected" in content
    assert "unexpanded UTC+8 calendar dates" in content
    assert "Older context can therefore be absent" in content
    assert "Search `updated_at` is used only for candidate discovery" in content


def test_summary_reports_unexplained_updated_at_exclusions() -> None:
    content = render_markdown(
        org="Project-HAMi",
        period=PERIOD,
        result=CollectionResult(
            unexplained_updated_excluded_count=7,
            activity_failure_excluded_count=2,
        ),
    )
    assert "Items excluded because `updated_at` was the only period match: `7`" in content
    assert "Items excluded after activity endpoint failures: `2`" in content


def test_empty_result_still_has_single_document() -> None:
    content = render_markdown(org="Project-HAMi", period=PERIOD, result=CollectionResult())
    assert "No matching issues." in content
    assert "No matching pull requests." in content
    assert content.count("schema_version") == 1


def test_current_review_evidence_keeps_latest_decisive_state() -> None:
    approval = Activity(
        kind="review",
        author="reviewer",
        author_association="MEMBER",
        body="approved",
        occurred_at=datetime(2026, 7, 14, tzinfo=UTC),
        state="APPROVED",
    )
    later_comment = Activity(
        kind="review",
        author="reviewer",
        author_association="MEMBER",
        body="follow-up",
        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
        state="COMMENTED",
    )
    assert _current_reviews([approval, later_comment]) == [approval]


def test_updated_existing_comment_is_rendered_as_period_activity_not_previous_context() -> None:
    item = issue()
    item.comments = [
        Activity(
            kind="comment",
            author="alice",
            author_association="MEMBER",
            body="edited old comment",
            occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
            updated_at=datetime(2026, 7, 13, tzinfo=UTC),
            updated_in_period=True,
        )
    ]
    content = render_markdown(org="Project-HAMi", period=PERIOD, result=CollectionResult(issues=[item]))
    assert "Updated existing comments: `1`" in content
    assert "Human comment activity: `1`" in content
    previous_context = content.split("#### Previous Context", 1)[1].split(
        "#### Comments During Scan Period", 1
    )[0]
    assert "edited old comment" not in previous_context
    assert "edited old comment" in content
