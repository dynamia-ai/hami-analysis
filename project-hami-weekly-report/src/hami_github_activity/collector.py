from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from hami_github_activity.date_range import ScanPeriod
from hami_github_activity.github_client import GitHubClient, GitHubRequestError
from hami_github_activity.models import (
    Activity,
    CollectionResult,
    CollectionWarning,
    IssueEvidence,
    PullRequestEvidence,
    actor_login,
    is_bot,
    parse_datetime,
)


logger = logging.getLogger(__name__)


class ActivityCollector:
    def __init__(self, client: GitHubClient, period: ScanPeriod, *, workers: int = 8) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        self.client = client
        self.period = period
        self.workers = workers
        self.warnings: list[CollectionWarning] = []
        self._warnings_lock = Lock()
        self._unexplained_updated_excluded_count = 0
        self._activity_failure_excluded_count = 0
        self._partial_reasons: list[str] = []

    def collect(self, org: str) -> CollectionResult:
        self.warnings = []
        self._unexplained_updated_excluded_count = 0
        self._activity_failure_excluded_count = 0
        self._partial_reasons = []
        issue_candidates = self._deduplicate(self._search(org, "issue"), kind="issue")
        pr_candidates = self._deduplicate(self._search(org, "pr"), kind="pr")
        issues: list[IssueEvidence] = []
        pull_requests: list[PullRequestEvidence] = []

        issues = self._collect_candidates(
            issue_candidates,
            label="issue",
            collect_one=self._collect_issue,
            include=self._include_issue,
        )
        pull_requests = self._collect_candidates(
            pr_candidates,
            label="pull request",
            collect_one=self._collect_pull_request,
            include=self._include_pull_request,
        )

        issues.sort(key=lambda item: (item.repository.lower(), item.number))
        pull_requests.sort(key=lambda item: (item.repository.lower(), item.number))
        self.warnings.sort(key=lambda item: (item.scope, item.message, item.url or ""))
        logger.info(
            "Collection finished: %d issues and %d pull requests retained",
            len(issues),
            len(pull_requests),
        )
        return CollectionResult(
            issues=issues,
            pull_requests=pull_requests,
            warnings=self.warnings,
            failed_requests=self.client.failed_requests,
            rate_limit_remaining=self.client.rate_limit_remaining,
            unexplained_updated_excluded_count=self._unexplained_updated_excluded_count,
            activity_failure_excluded_count=self._activity_failure_excluded_count,
            collection_status="partial" if self._partial_reasons else "complete",
            partial_reasons=sorted(set(self._partial_reasons)),
        )

    def _search(self, org: str, kind: str) -> list[dict[str, Any]]:
        query = f"org:{org} is:{kind} updated:>={self.period.search_start_date}"
        logger.info("Searching GitHub for %s candidates: %s", kind, query)
        # A failed candidate search makes it impossible to distinguish "no
        # activity" from an authentication, permission, or API outage.  Propagate
        # the error so the CLI exits non-zero and does not write success-looking
        # evidence.
        result = self.client.search_issues(query)
        if result.capped:
            message = (
                f"Search API reported {result.total_count} results; only the first 1000 are available."
            )
            self._warn(
                f"search:{kind}",
                message,
            )
            self._mark_partial(f"search:{kind}: result cap reached")
        if result.incomplete:
            self._warn(f"search:{kind}", "GitHub Search reported incomplete or partially collected results.")
            self._mark_partial(f"search:{kind}: incomplete results")
        if result.partial_error:
            self._warn(f"search:{kind}", f"Pagination stopped after partial success: {result.partial_error}", result.partial_error_url)
            self._mark_partial(f"search:{kind}: pagination error")
        if result.malformed_item_count:
            self._warn(
                f"search:{kind}",
                "GitHub Search returned "
                f"{result.malformed_item_count} non-object candidate(s); collection is partial.",
            )
            self._mark_partial(f"search:{kind}: malformed response items")
        if result.malformed_identity_count:
            self._warn(
                f"search:{kind}",
                "GitHub Search returned "
                f"{result.malformed_identity_count} candidate(s) without a valid repository identity; "
                "collection is partial.",
            )
            self._mark_partial(f"search:{kind}: malformed candidate identity")
        if result.duplicate_item_count:
            if result.incomplete:
                message = (
                    "GitHub Search returned "
                    f"{result.duplicate_item_count} duplicate candidate identity value(s), and its "
                    "candidate set cannot be certified complete."
                )
            else:
                message = (
                    "GitHub Search returned "
                    f"{result.duplicate_item_count} duplicate candidate identity value(s) across pages; "
                    f"{result.unique_item_count} unique candidate(s) reconcile with the terminal total."
                )
            self._warn(
                f"search:{kind}",
                message,
            )
        logger.info("Found %d %s candidates", len(result.items), kind)
        return result.items

    def _collect_candidates(self, candidates: list[dict[str, Any]], *, label: str, collect_one: Any, include: Any) -> list[Any]:
        total = len(candidates)
        retained: list[Any] = []
        logger.info("Collecting %d %s candidates with %d workers", total, label, self.workers)
        if not candidates:
            return retained

        def run(index: int, candidate: dict[str, Any]) -> Any:
            logger.info("Collecting %s %d/%d: %s", label, index, total, self._candidate_id(candidate))
            return collect_one(candidate)

        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="github-collect") as executor:
            futures: dict[Future[Any], str] = {
                executor.submit(run, index, candidate): self._candidate_id(candidate)
                for index, candidate in enumerate(candidates, start=1)
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                item = future.result()
                if item is not None and include(item):
                    retained.append(item)
                    logger.info("Retained %s %s (%d/%d completed)", label, item.item_id, completed, total)
                elif item is not None:
                    if self._activity_collection_failed(item):
                        self._activity_failure_excluded_count += 1
                        logger.info(
                            "Excluded %s %s after an activity endpoint failure (%d/%d completed)",
                            label,
                            item.item_id,
                            completed,
                            total,
                        )
                    elif item.exact_activity_unknown:
                        self._unexplained_updated_excluded_count += 1
                        logger.info(
                            "Excluded %s %s because updated_at is the only period match (%d/%d completed)",
                            label,
                            item.item_id,
                            completed,
                            total,
                        )
                    else:
                        logger.info(
                            "Skipped %s %s after exact period filtering (%d/%d completed)",
                            label,
                            item.item_id,
                            completed,
                            total,
                        )
                else:
                    logger.info("Skipped %s %s because collection failed or it predates the period (%d/%d completed)", label, futures[future], completed, total)
        return retained

    @staticmethod
    def _activity_collection_failed(item: IssueEvidence | PullRequestEvidence) -> bool:
        return any(gap.startswith("Could not collect ") for gap in item.data_gaps)

    @staticmethod
    def _candidate_id(candidate: dict[str, Any]) -> str:
        repository = ActivityCollector._repository_name(candidate) or "unknown-repository"
        number = candidate.get("number")
        return f"{repository}#{number if isinstance(number, int) else 'unknown'}"

    def _deduplicate(self, candidates: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
        """Return unique candidates without concealing malformed Search results.

        Search is the discovery boundary for the collection.  Silently dropping a
        returned object with no actionable identity can turn a broken response
        into a success-looking zero-result report, so retain the valid entries
        but make the overall result explicitly partial.
        """
        unique: dict[tuple[str, int], dict[str, Any]] = {}
        for position, item in enumerate(candidates, start=1):
            repository = ActivityCollector._repository_name(item)
            number = item.get("number")
            if repository is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
                self._warn(
                    f"search:{kind}",
                    "Search returned a malformed candidate at position "
                    f"{position}; repository_url must identify owner/repo and number must be positive.",
                )
                self._mark_partial(f"search:{kind}: malformed candidate")
                continue
            unique[(repository, number)] = item
        return list(unique.values())

    @staticmethod
    def _repository_name(candidate: dict[str, Any]) -> str | None:
        url = candidate.get("repository_url")
        if not isinstance(url, str):
            return None
        marker = "/repos/"
        if marker not in url:
            return None
        repository = url.split(marker, 1)[1].rstrip("/")
        owner, separator, name = repository.partition("/")
        if not separator or not owner or not name or "/" in name or "?" in name or "#" in name:
            return None
        return f"{owner}/{name}"

    def _collect_issue(self, candidate: dict[str, Any]) -> IssueEvidence | None:
        repository = self._repository_name(candidate)
        number = candidate.get("number")
        if repository is None or not isinstance(number, int):
            self._warn("issue:candidate", "Candidate is missing repository_url or number.")
            self._mark_partial("issue candidate missing required identity")
            return None
        if self._predates_period(candidate):
            logger.info("Pruned issue %s#%d from Search metadata before detail collection", repository, number)
            return None
        base = f"/repos/{repository}/issues/{number}"
        if self._complete_issue_search_payload(candidate):
            detail = candidate
            logger.info("Using Search response as issue detail for %s#%d", repository, number)
        else:
            try:
                detail = self.client.get_json(base)
            except GitHubRequestError as exc:
                self._warn(f"issue:{repository}#{number}", str(exc), exc.url)
                self._mark_partial(f"issue:{repository}#{number}: detail request failed")
                return None

        data_gaps = self._missing_fields(detail, ["title", "html_url", "state", "user", "created_at", "updated_at"])
        comments = self._safe_activities(
            base + "/comments",
            "comment",
            f"issue:{repository}#{number}",
            data_gaps,
            params=self._since_params(),
            known_count=detail.get("comments"),
        )
        created_at = parse_datetime(detail.get("created_at"))
        closed_at = parse_datetime(detail.get("closed_at"))
        updated_at = parse_datetime(detail.get("updated_at"))
        created_in_period = self.period.contains(created_at)
        closed_in_period = self.period.contains(closed_at)
        known_activity = created_in_period or closed_in_period or any(
            item.in_period or item.updated_in_period for item in comments
        )
        return IssueEvidence(
            repository=repository,
            number=number,
            title=str(detail.get("title") or "(missing title)"),
            url=str(detail.get("html_url") or candidate.get("html_url") or ""),
            state=str(detail.get("state") or "unknown"),
            state_reason=detail.get("state_reason"),
            author=actor_login(detail.get("user")),
            author_association=detail.get("author_association"),
            created_at=created_at,
            updated_at=updated_at,
            closed_at=closed_at,
            labels=self._labels(detail.get("labels")),
            assignees=[actor_login(user) for user in detail.get("assignees") or []],
            milestone=(detail.get("milestone") or {}).get("title"),
            body=str(detail.get("body") or ""),
            comments=comments,
            created_in_period=created_in_period,
            closed_in_period=closed_in_period,
            exact_activity_unknown=self.period.contains(updated_at) and not known_activity,
            data_gaps=data_gaps,
        )

    def _collect_pull_request(self, candidate: dict[str, Any]) -> PullRequestEvidence | None:
        repository = self._repository_name(candidate)
        number = candidate.get("number")
        if repository is None or not isinstance(number, int):
            self._warn("pull_request:candidate", "Candidate is missing repository_url or number.")
            self._mark_partial("pull request candidate missing required identity")
            return None
        if self._predates_period(candidate):
            logger.info("Pruned pull request %s#%d from Search metadata before detail collection", repository, number)
            return None
        base = f"/repos/{repository}/pulls/{number}"
        try:
            detail = self.client.get_json(base)
        except GitHubRequestError as exc:
            self._warn(f"pull_request:{repository}#{number}", str(exc), exc.url)
            self._mark_partial(f"pull_request:{repository}#{number}: detail request failed")
            return None

        data_gaps = self._missing_fields(detail, ["title", "html_url", "state", "user", "created_at", "updated_at"])
        issue_base = f"/repos/{repository}/issues/{number}"
        conversation = self._safe_activities(
            issue_base + "/comments", "comment", f"pull_request:{repository}#{number}", data_gaps,
            params=self._since_params(), known_count=detail.get("comments"),
        )
        reviews = self._safe_activities(base + "/reviews", "review", f"pull_request:{repository}#{number}", data_gaps)
        review_comments = self._safe_activities(
            base + "/comments", "review_comment", f"pull_request:{repository}#{number}", data_gaps,
            params=self._since_params(), known_count=detail.get("review_comments"),
        )
        head = detail.get("head") if isinstance(detail.get("head"), dict) else {}
        head_sha = head.get("sha") if isinstance(head.get("sha"), str) else None
        head_commit_at = self._head_commit_at(
            repository,
            number,
            head_sha,
            data_gaps,
        )
        created_at = parse_datetime(detail.get("created_at"))
        updated_at = parse_datetime(detail.get("updated_at"))
        merged_at = parse_datetime(detail.get("merged_at"))
        closed_at = parse_datetime(detail.get("closed_at"))
        merged = bool(detail.get("merged"))
        created_in_period = self.period.contains(created_at)
        merged_in_period = merged and self.period.contains(merged_at)
        closed_unmerged_in_period = not merged and self.period.contains(closed_at)
        all_activity = conversation + reviews + review_comments
        known_activity = created_in_period or merged_in_period or closed_unmerged_in_period or any(
            item.in_period or item.updated_in_period for item in all_activity
        )
        return PullRequestEvidence(
            repository=repository,
            number=number,
            title=str(detail.get("title") or "(missing title)"),
            url=str(detail.get("html_url") or candidate.get("html_url") or ""),
            state=str(detail.get("state") or "unknown"),
            draft=bool(detail.get("draft")),
            merged=merged,
            merged_at=merged_at,
            closed_at=closed_at,
            author=actor_login(detail.get("user")),
            author_association=detail.get("author_association"),
            created_at=created_at,
            updated_at=updated_at,
            base_branch=(detail.get("base") or {}).get("ref"),
            head_branch=(detail.get("head") or {}).get("ref"),
            labels=self._labels(detail.get("labels")),
            assignees=[actor_login(user) for user in detail.get("assignees") or []],
            requested_reviewers=[actor_login(user) for user in detail.get("requested_reviewers") or []],
            milestone=(detail.get("milestone") or {}).get("title"),
            body=str(detail.get("body") or ""),
            mergeable=detail.get("mergeable"),
            additions=detail.get("additions"),
            deletions=detail.get("deletions"),
            changed_files=detail.get("changed_files"),
            conversation_comments=conversation,
            reviews=reviews,
            review_comments=review_comments,
            created_in_period=created_in_period,
            merged_in_period=merged_in_period,
            closed_unmerged_in_period=closed_unmerged_in_period,
            exact_activity_unknown=self.period.contains(updated_at) and not known_activity,
            data_gaps=data_gaps,
            head_sha=head_sha,
            head_commit_at=head_commit_at,
            review_decision=self._review_decision(reviews),
        )

    def _head_commit_at(
        self,
        repository: str,
        number: int,
        head_sha: str | None,
        data_gaps: list[str],
    ) -> datetime | None:
        if head_sha is None:
            data_gaps.append("Pull request head SHA was not returned by GitHub.")
            return None
        path = f"/repos/{repository}/commits/{head_sha}"
        try:
            commit = self.client.get_json(path)
        except GitHubRequestError as exc:
            message = f"Could not collect head commit metadata: {exc}"
            data_gaps.append(message)
            self._warn(f"pull_request:{repository}#{number}", message, exc.url)
            self._mark_partial(f"pull_request:{repository}#{number}: head commit request failed")
            return None
        metadata = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
        author = metadata.get("author") if isinstance(metadata.get("author"), dict) else {}
        committer = metadata.get("committer") if isinstance(metadata.get("committer"), dict) else {}
        return parse_datetime(author.get("date") or committer.get("date"))

    @staticmethod
    def _review_decision(reviews: list[Activity]) -> str | None:
        latest_by_author: dict[str, Activity] = {}
        for review in reviews:
            current = latest_by_author.get(review.author)
            if current is None or (review.occurred_at or datetime.min.replace(tzinfo=UTC)) > (
                current.occurred_at or datetime.min.replace(tzinfo=UTC)
            ):
                latest_by_author[review.author] = review
        decisions = [
            f"{author}:{review.state}"
            for author, review in sorted(latest_by_author.items())
            if review.state
        ]
        return ", ".join(decisions) if decisions else None

    def _safe_activities(
        self,
        path: str,
        kind: str,
        scope: str,
        data_gaps: list[str],
        *,
        params: dict[str, Any] | None = None,
        known_count: Any = None,
    ) -> list[Activity]:
        if known_count == 0:
            logger.info("Skipping %s data for %s because GitHub reports zero records", kind, scope)
            return []
        logger.info("Collecting %s data for %s", kind, scope)
        try:
            paginated = getattr(self.client, "get_paginated_result", None)
            if callable(paginated):
                page_result = paginated(path, params=params) if params else paginated(path)
                raw = page_result.items
                if page_result.incomplete:
                    message = (
                        f"Could not collect page {page_result.failed_page} of {kind} data: "
                        f"{page_result.partial_error}"
                    )
                    data_gaps.append(message)
                    self._warn(scope, message, page_result.partial_error_url)
                    self._mark_partial(f"{scope}: {kind} pagination incomplete")
                if page_result.malformed_item_count:
                    message = (
                        f"Could not collect {page_result.malformed_item_count} non-object "
                        f"{kind} record(s) returned by GitHub."
                    )
                    data_gaps.append(message)
                    self._warn(scope, message)
                    self._mark_partial(f"{scope}: {kind} pagination malformed response items")
            else:
                raw = self.client.get_paginated(path, params=params) if params else self.client.get_paginated(path)
        except GitHubRequestError as exc:
            message = f"Could not collect {kind} data: {exc}"
            data_gaps.append(message)
            self._warn(scope, message, exc.url)
            self._mark_partial(f"{scope}: {kind} request failed")
            return []
        logger.info("Collected %d %s records for %s", len(raw), kind, scope)
        return [self._activity(item, kind) for item in raw]

    def _since_params(self) -> dict[str, str]:
        return {"since": self.period.utc_start.strftime("%Y-%m-%dT%H:%M:%SZ")}

    def _predates_period(self, candidate: dict[str, Any]) -> bool:
        updated_at = parse_datetime(candidate.get("updated_at"))
        return updated_at is not None and updated_at < self.period.utc_start

    @staticmethod
    def _complete_issue_search_payload(candidate: dict[str, Any]) -> bool:
        required = {"title", "html_url", "state", "user", "created_at", "updated_at", "comments"}
        return required.issubset(candidate)

    def _activity(self, item: dict[str, Any], kind: str) -> Activity:
        occurred_key = "submitted_at" if kind == "review" else "created_at"
        occurred_at = parse_datetime(item.get(occurred_key))
        updated_at = parse_datetime(item.get("updated_at"))
        return Activity(
            kind=kind,  # type: ignore[arg-type]
            author=actor_login(item.get("user")),
            author_association=item.get("author_association"),
            body=str(item.get("body") or ""),
            occurred_at=occurred_at,
            updated_at=updated_at,
            url=item.get("html_url"),
            bot=is_bot(item.get("user")),
            in_period=self.period.contains(occurred_at),
            updated_in_period=self.period.contains(updated_at),
            state=item.get("state"),
            path=item.get("path"),
            line=item.get("line"),
            original_line=item.get("original_line"),
            side=item.get("side"),
        )

    def _include_issue(self, item: IssueEvidence) -> bool:
        return any(
            (
                item.created_in_period,
                item.closed_in_period,
                any(activity.in_period or activity.updated_in_period for activity in item.comments),
            )
        )

    def _include_pull_request(self, item: PullRequestEvidence) -> bool:
        return any(
            (
                item.created_in_period,
                item.merged_in_period,
                item.closed_unmerged_in_period,
                any(activity.in_period or activity.updated_in_period for activity in item.all_activity),
            )
        )

    @staticmethod
    def _labels(raw: Any) -> list[str]:
        labels: list[str] = []
        for item in raw or []:
            if isinstance(item, str):
                labels.append(item)
            elif isinstance(item, dict) and item.get("name"):
                labels.append(str(item["name"]))
        return labels

    @staticmethod
    def _missing_fields(detail: dict[str, Any], fields: list[str]) -> list[str]:
        missing = [field for field in fields if field not in detail]
        return [f"GitHub API response omitted field: {field}" for field in missing]

    def _warn(self, scope: str, message: str, url: str | None = None) -> None:
        with self._warnings_lock:
            self.warnings.append(CollectionWarning(scope=scope, message=message, url=url))
        logger.warning("%s: %s%s", scope, message, f" ({url})" if url else "")

    def _mark_partial(self, reason: str) -> None:
        with self._warnings_lock:
            self._partial_reasons.append(reason)
