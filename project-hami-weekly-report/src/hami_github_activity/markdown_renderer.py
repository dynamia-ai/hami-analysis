from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from hami_github_activity.date_range import ScanPeriod
from hami_github_activity.models import Activity, CollectionResult, IssueEvidence, PullRequestEvidence


BODY_LIMIT = 30_000
ACTIVITY_BODY_LIMIT = 12_000
UNKNOWN_ACTIVITY_MESSAGE = (
    "Matched by GitHub updated_at search, but the exact activity "
    "could not be determined without timeline events."
)


def _fmt(value: datetime | None) -> str:
    return value.isoformat() if value else "Not provided"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _list(values: list[str]) -> str:
    return ", ".join(values) if values else "None"


def _table(value: str | None) -> str:
    return (value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip() or "None"


def truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _markdown_body(value: str, limit: int) -> str:
    value, truncated = truncate(value, limit)
    value = value.replace("<!-- ITEM_", "&lt;!-- ITEM_")
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(4, longest + 1)
    suffix = f"\n\n_[Truncated at {limit:,} characters by the collector.]_" if truncated else ""
    if not value:
        value = "(empty)"
    return f"{fence}markdown\n{value}\n{fence}{suffix}"


def _activity_line(item: Activity) -> str:
    details = [
        f"author: `{item.author}`",
        f"association: `{item.author_association or 'NONE'}`",
        f"bot: `{_yes(item.bot)}`",
        f"occurred_at: `{_fmt(item.occurred_at)}`",
        f"in_period: `{_yes(item.in_period)}`",
    ]
    if item.updated_at and item.updated_at != item.occurred_at:
        details.append(f"updated_at: `{_fmt(item.updated_at)}`")
        details.append(f"updated_in_period: `{_yes(item.updated_in_period)}`")
    if item.state:
        details.append(f"state: `{item.state}`")
    if item.path:
        location = item.path
        line = item.line if item.line is not None else item.original_line
        if line is not None:
            location += f":{line}"
        if item.side:
            location += f" ({item.side})"
        details.append(f"location: `{location}`")
    if item.url:
        details.append(f"[source]({item.url})")
    return "- " + "; ".join(details) + "\n\n" + _markdown_body(item.body, ACTIVITY_BODY_LIMIT)


def _activity_group(items: list[Activity]) -> str:
    if not items:
        return "None."
    return "\n\n".join(_activity_line(item) for item in sorted(items, key=lambda x: x.occurred_at or datetime.min.replace(tzinfo=UTC)))


def _latest(items: list[Activity], *, maintainer: bool = False) -> Activity | None:
    candidates = [item for item in items if not item.bot and (not maintainer or item.maintainer)]
    return max(candidates, key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=UTC), default=None)


def _previous_context(items: list[Activity]) -> list[Activity]:
    previous = [item for item in items if not item.bot and not item.active_in_period]
    previous.sort(key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return list(reversed(previous[:3]))


def _anchor(kind: str, item_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", item_id.lower()).strip("-")
    return f"{kind}-{normalized}"


def _issue_index(issue: IssueEvidence) -> str:
    return " | ".join(
        [
            _table(issue.item_id),
            _table(issue.repository),
            str(issue.number),
            _table(issue.title),
            _table(issue.state),
            _yes(issue.created_in_period),
            _yes(issue.closed_in_period),
            str(issue.period_human_activity_count),
            _table(issue.author_association or "NONE"),
            _table(_list(issue.labels)),
            f"[evidence](#{_anchor('issue', issue.item_id)})",
        ]
    )


def _pr_index(pr: PullRequestEvidence) -> str:
    state = "merged" if pr.merged else pr.state
    return " | ".join(
        [
            _table(pr.item_id),
            _table(pr.repository),
            str(pr.number),
            _table(pr.title),
            _table(state),
            _yes(pr.created_in_period),
            _yes(pr.closed_unmerged_in_period),
            _yes(pr.merged_in_period),
            str(pr.period_human_activity_count),
            _table(pr.author_association or "NONE"),
            _table(_list(pr.labels)),
            f"[evidence](#{_anchor('pull-request', pr.item_id)})",
        ]
    )


def _issue_block(issue: IssueEvidence) -> str:
    new_comments = [item for item in issue.comments if item.in_period]
    period_activity = [item for item in issue.comments if item.active_in_period]
    human = [item for item in period_activity if not item.bot]
    bots = [item for item in period_activity if item.bot]
    maintainer_count = sum(item.maintainer and not item.bot for item in period_activity)
    gaps = list(issue.data_gaps)
    if issue.exact_activity_unknown:
        gaps.insert(0, UNKNOWN_ACTIVITY_MESSAGE)
    latest_human = _latest(issue.comments)
    latest_maintainer = _latest(issue.comments, maintainer=True)
    return f"""<!-- ITEM_START issue {issue.item_id} -->

<a id="{_anchor('issue', issue.item_id)}"></a>

### Issue: {issue.item_id}

#### Metadata

- Title: {issue.title}
- URL: {issue.url}
- State: `{issue.state}`
- State reason: `{issue.state_reason or 'Not provided'}`
- Author: `{issue.author}`
- Author association: `{issue.author_association or 'NONE'}`
- Created at: `{_fmt(issue.created_at)}`
- Updated at: `{_fmt(issue.updated_at)}`
- Closed at: `{_fmt(issue.closed_at)}`

#### Activity During Scan Period

- Created in period: `{_yes(issue.created_in_period)}`
- Closed in period: `{_yes(issue.closed_in_period)}`
- New comments: `{len(new_comments)}`
- Updated existing comments: `{sum(item.updated_in_period and not item.in_period for item in issue.comments)}`
- Human comment activity: `{len(human)}`
- Bot comment activity: `{len(bots)}`
- Maintainer/member/collaborator comment activity: `{maintainer_count}`

#### Labels, Assignees and Milestone

- Labels: {_list(issue.labels)}
- Assignees: {_list(issue.assignees)}
- Milestone: {issue.milestone or 'None'}

#### Body

{_markdown_body(issue.body, BODY_LIMIT)}

#### Previous Context

{_activity_group(_previous_context(issue.comments))}

#### Comments During Scan Period

**Human comments**

{_activity_group(human)}

**Bot comments (lower-salience evidence)**

{_activity_group(bots)}

#### Latest Human Comment

{_activity_group([latest_human] if latest_human else [])}

#### Latest Maintainer Comment

{_activity_group([latest_maintainer] if latest_maintainer else [])}

#### Data Gaps

{chr(10).join(f'- {gap}' for gap in gaps) if gaps else 'None.'}

<!-- ITEM_END issue {issue.item_id} -->"""


def _current_reviews(reviews: list[Activity]) -> list[Activity]:
    by_author: dict[str, list[Activity]] = {}
    for review in reviews:
        by_author.setdefault(review.author, []).append(review)
    current_reviews: list[Activity] = []
    decisive_states = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
    for author_reviews in by_author.values():
        decisive = [review for review in author_reviews if (review.state or "").upper() in decisive_states]
        candidates = decisive or author_reviews
        current_reviews.append(
            max(candidates, key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=UTC))
        )
    return sorted(current_reviews, key=lambda item: item.author.lower())


def _pr_block(pr: PullRequestEvidence) -> str:
    new_conversations = [item for item in pr.conversation_comments if item.in_period]
    new_reviews = [item for item in pr.reviews if item.in_period]
    new_review_comments = [item for item in pr.review_comments if item.in_period]
    conversations = [item for item in pr.conversation_comments if item.active_in_period]
    reviews = [item for item in pr.reviews if item.active_in_period]
    review_comments = [item for item in pr.review_comments if item.active_in_period]
    all_activity = pr.all_activity
    period_activity = conversations + reviews + review_comments
    gaps = list(pr.data_gaps)
    if pr.exact_activity_unknown:
        gaps.insert(0, UNKNOWN_ACTIVITY_MESSAGE)
    latest_human = _latest(all_activity)
    latest_maintainer = _latest(all_activity, maintainer=True)
    return f"""<!-- ITEM_START pull_request {pr.item_id} -->

<a id="{_anchor('pull-request', pr.item_id)}"></a>

### Pull Request: {pr.item_id}

#### Metadata

- Title: {pr.title}
- URL: {pr.url}
- State: `{pr.state}`
- Draft: `{_yes(pr.draft)}`
- Merged: `{_yes(pr.merged)}`
- Merged at: `{_fmt(pr.merged_at)}`
- Closed at: `{_fmt(pr.closed_at)}`
- Author: `{pr.author}`
- Author association: `{pr.author_association or 'NONE'}`
- Created at: `{_fmt(pr.created_at)}`
- Updated at: `{_fmt(pr.updated_at)}`
- Base branch: `{pr.base_branch or 'Not provided'}`
- Head branch: `{pr.head_branch or 'Not provided'}`

#### Activity During Scan Period

- Created in period: `{_yes(pr.created_in_period)}`
- Merged in period: `{_yes(pr.merged_in_period)}`
- Closed without merge in period: `{_yes(pr.closed_unmerged_in_period)}`
- New conversation comments: `{len(new_conversations)}`
- Updated existing conversation comments: `{sum(item.updated_in_period and not item.in_period for item in pr.conversation_comments)}`
- New reviews: `{len(new_reviews)}`
- New review comments: `{len(new_review_comments)}`
- Updated existing review comments: `{sum(item.updated_in_period and not item.in_period for item in pr.review_comments)}`
- Human activity: `{pr.period_human_activity_count}`
- Bot activity: `{sum(item.bot for item in period_activity)}`
- Maintainer/member/collaborator activity: `{sum(item.active_in_period and item.maintainer and not item.bot for item in all_activity)}`

#### Current Review Information

- Mergeable (current GitHub value): `{pr.mergeable if pr.mergeable is not None else 'unknown'}`
- Latest available decisive review state per reviewer follows. If none exists, the latest review record is shown. This is evidence, not a merge-readiness conclusion.

{_activity_group(_current_reviews(pr.reviews))}

#### Labels, Assignees and Requested Reviewers

- Labels: {_list(pr.labels)}
- Assignees: {_list(pr.assignees)}
- Requested reviewers: {_list(pr.requested_reviewers)}
- Milestone: {pr.milestone or 'None'}

#### Change Size

- Additions: `{pr.additions if pr.additions is not None else 'Not provided'}`
- Deletions: `{pr.deletions if pr.deletions is not None else 'Not provided'}`
- Changed files: `{pr.changed_files if pr.changed_files is not None else 'Not provided'}`

#### Body

{_markdown_body(pr.body, BODY_LIMIT)}

#### Previous Context

{_activity_group(_previous_context(all_activity))}

#### Conversation Comments During Scan Period

**Human comments**

{_activity_group([item for item in conversations if not item.bot])}

**Bot comments (lower-salience evidence)**

{_activity_group([item for item in conversations if item.bot])}

#### Reviews During Scan Period

**Human reviews**

{_activity_group([item for item in reviews if not item.bot])}

**Bot reviews (lower-salience evidence)**

{_activity_group([item for item in reviews if item.bot])}

#### Review Comments During Scan Period

**Human review comments**

{_activity_group([item for item in review_comments if not item.bot])}

**Bot review comments (lower-salience evidence)**

{_activity_group([item for item in review_comments if item.bot])}

#### Latest Human Activity

{_activity_group([latest_human] if latest_human else [])}

#### Latest Maintainer Activity

{_activity_group([latest_maintainer] if latest_maintainer else [])}

#### Data Gaps

{chr(10).join(f'- {gap}' for gap in gaps) if gaps else 'None.'}

<!-- ITEM_END pull_request {pr.item_id} -->"""


def render_markdown(
    *,
    org: str,
    period: ScanPeriod,
    result: CollectionResult,
    generated_at: datetime | None = None,
) -> str:
    generated_at = (generated_at or datetime.now(UTC)).astimezone(UTC)
    issue_index = "\n".join(f"| {_issue_index(issue)} |" for issue in result.issues)
    pr_index = "\n".join(f"| {_pr_index(pr)} |" for pr in result.pull_requests)
    warnings = "\n".join(
        f"- **{warning.scope}**: {warning.message}" + (f" ([request]({warning.url}))" if warning.url else "")
        for warning in result.warnings
    ) or "None."
    issue_blocks = "\n\n".join(_issue_block(issue) for issue in result.issues) or "No matching issues."
    pr_blocks = "\n\n".join(_pr_block(pr) for pr in result.pull_requests) or "No matching pull requests."
    rate = result.rate_limit_remaining if result.rate_limit_remaining is not None else "unknown"
    return f"""---
schema_version: "1.0"
organization: "{org}"
generated_at: "{generated_at.isoformat()}"
timezone: "{period.timezone}"
local_start: "{period.local_start.isoformat()}"
local_end: "{period.local_end.isoformat()}"
utc_start: "{period.utc_start.isoformat()}"
utc_end: "{period.utc_end.isoformat()}"
issue_count: {len(result.issues)}
pull_request_count: {len(result.pull_requests)}
collection_warning_count: {len(result.warnings)}
---

# {org} GitHub Activity Evidence

## Document Map

Recommended segmented reading order:

1. Read the front matter and Collection Summary.
2. Read the Issues Index and Pull Requests Index.
3. Select candidate items using index facts only.
4. Search for the exact `ITEM_START` marker and read through its matching `ITEM_END` marker.
5. Read related item blocks before forming a cross-item theme.
6. Finish with Collection Warnings and Data Limitations.

Do not assume this document must be loaded in one pass.

## Collection Summary

- Organization: `{org}`
- Local scan period: `{period.local_start.isoformat()}` through `{period.local_end.isoformat()}`
- UTC scan period: `{period.utc_start.isoformat()}` through `{period.utc_end.isoformat()}`
- UTC+8 Search API date window: `{period.search_start_date}` through `{period.search_end_date}`
- Issues retained after exact local filtering: `{len(result.issues)}`
- Pull requests retained after exact local filtering: `{len(result.pull_requests)}`
- Items excluded because `updated_at` was the only period match: `{result.unexplained_updated_excluded_count}`
- Items excluded after activity endpoint failures: `{result.activity_failure_excluded_count}`
- Failed API requests: `{result.failed_requests}`
- GitHub API rate limit remaining: `{rate}`

## Issues Index

| ID | Repository | Number | Title | State | Created in period | Closed in period | Period human activity | Author association | Labels | Section |
| --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- | --- |
{issue_index}

## Pull Requests Index

| ID | Repository | Number | Title | State | Created in period | Closed unmerged in period | Merged in period | Period human activity | Author association | Labels | Section |
| --- | --- | ---: | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
{pr_index}

## Issue Evidence

{issue_blocks}

## Pull Request Evidence

{pr_blocks}

## Collection Warnings

{warnings}

## Data Limitations

- Candidate discovery uses GitHub Search Issues with the unexpanded UTC+8 calendar dates, followed by exact timestamp filtering.
- GitHub Search can expose at most 1,000 results for a query. A warning is recorded when that boundary is reached.
- Timeline events are not collected. Label, assignee, milestone, reopen, and draft-to-ready transition times cannot be determined.
- Search `updated_at` is used only for candidate discovery. An item is excluded when no collected creation, close, merge, comment, or review event falls in the period. This prevents branch deletion and other unexplained metadata updates from becoming inclusion reasons.
- Items without a verifiable period event are also excluded when an activity endpoint fails. These are counted separately because the failed endpoint may have hidden qualifying activity; inspect Collection Warnings for the affected item and request URL.
- Check runs and workflow runs are not collected. This evidence cannot determine CI status or merge readiness.
- Commits and changed-file diffs are not collected. This evidence cannot determine when a commit was pushed or what individual files changed.
- Reaction details are not collected.
- Pull request `mergeable` is GitHub's current nullable value and is not proof that a pull request is ready to merge.
- Bodies are truncated at {BODY_LIMIT:,} characters and individual activity bodies at {ACTIVITY_BODY_LIMIT:,} characters, with explicit markers.
- Comment and review-comment endpoints request records updated since the UTC+8 scan boundary. Older context can therefore be absent; when returned, at most the latest three pre-period human activities are shown.
"""


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
