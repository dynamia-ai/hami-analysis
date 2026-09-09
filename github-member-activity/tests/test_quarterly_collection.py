"""Regression fixtures for failures encountered collecting a quarterly window."""
from copy import deepcopy
from datetime import UTC, date, datetime
import re

import pytest

from github_member_activity.collector import (
    DISCOVERY_QUERY, HYDRATE_QUERY, IDENTITY_QUERY, REVIEWS_QUERY,
    REVIEW_CONTRIBUTIONS_QUERY, collect,
)
from github_member_activity.config import AppConfig
from github_member_activity.github_client import GitHubRequestError, SearchPage
from github_member_activity.period import build_period, parse_rfc3339


CONFIG = AppConfig.model_validate({
    "github": {"token_env": "PUBLIC_GITHUB_TOKEN"},
    "members": [{"member_id": "alice", "github_login": "Alice", "github_node_id": "U1", "active_from": date(2020, 1, 1)}],
    "period": {"timezone": "Asia/Shanghai"},
    "repository_policy": {"public_only": True, "first_party_owners": []},
    "output": {"directory": "./output"},
})
PERIOD = build_period("explicit", "Asia/Shanghai", start="2026-07-01T00:00:00+08:00", end="2026-09-08T00:00:00+08:00")
OBSERVED = datetime(2026, 9, 9, tzinfo=UTC)


class QuarterlyGitHub:
    def __init__(self, *, count=1, rest_created="2026-07-02T00:00:01Z", created="2026-07-02T00:00:00Z", author_type="User", author_id="U1", visibility="PUBLIC", review=False, contribution_at="2026-06-30T01:00:00Z", submitted="2026-06-30T01:00:00Z"):
        self.ids = [f"P{i}" for i in range(count)]
        self.rest_created = rest_created
        self.created = created
        self.author = {"__typename": author_type}
        if author_type == "User":
            self.author["id"] = author_id
        self.visibility = visibility
        self.review = review
        self.contribution_at = contribution_at
        self.submitted = submitted
        self.node_calls = []
        self.review_reads = 0
        self.hydrated_created = None
        self.discovery_calls = 0
        self.unstable_author = False

    def search(self, query, *, page=1):
        if self.review or "is:issue" in query or "is:merged" in query:
            return SearchPage((), 0, False)
        # Emulate REST's own inclusive timestamp filter, so boundary tests catch
        # omission of the one-second discovery envelope.
        bounds = re.search(r"created:(\S+)\.\.(\S+)", query)
        assert bounds
        if not parse_rfc3339(bounds[1]) <= parse_rfc3339(self.rest_created) <= parse_rfc3339(bounds[2]):
            return SearchPage((), 0, False)
        rows = tuple({"node_id": key, "actor_node_id": "U1", "created_at": self.rest_created} for key in self.ids)
        return SearchPage(rows[(page - 1) * 100:page * 100], len(rows), False)

    def node(self, key):
        if key.startswith("RV"):
            return {"__typename": "PullRequestReview", "id": key, "pullRequest": self.node(key[2:])}
        return {
            "__typename": "PullRequest", "id": key, "number": int(key[1:]) + 1,
            "author": dict(self.author), "createdAt": self.created, "mergedAt": None,
            "repository": {"id": "R1", "nameWithOwner": "community/project", "visibility": self.visibility, "owner": {"id": "O1", "login": "community"}},
        }

    def graphql(self, query, variables):
        if query == IDENTITY_QUERY:
            return {"user": {"__typename": "User", "id": "U1", "login": "Alice"}}
        if "commitContributionsByRepository" in query:
            return {"user": {"contributionsCollection": {"commitContributionsByRepository": []}}}
        ids = variables["ids"]
        assert len(ids) <= 100, "GitHub nodes(ids:) limit exceeded"
        self.node_calls.append((query, list(ids)))
        nodes = [self.node(key) for key in ids]
        if query == DISCOVERY_QUERY:
            self.discovery_calls += 1
            if self.unstable_author and self.discovery_calls == 2:
                for node in nodes:
                    node["author"] = {"__typename": "Bot"}
        if query == HYDRATE_QUERY and self.hydrated_created:
            for node in nodes:
                node["createdAt"] = self.hydrated_created
        return {"nodes": nodes}

    def connection(self, query, variables, path):
        if query == REVIEW_CONTRIBUTIONS_QUERY and self.review:
            return [{"isRestricted": False, "occurredAt": self.contribution_at, "user": {"__typename": "User", "id": "U1"}, "pullRequest": {"id": key}} for key in self.ids]
        if query == REVIEWS_QUERY:
            self.review_reads += 1
            return [{"__typename": "PullRequestReview", "id": "RV" + variables['id'], "author": {"__typename": "User", "id": "U1"}, "state": "APPROVED", "submittedAt": self.submitted}]
        return []


def run(client):
    return collect(CONFIG, PERIOD, client, observed_at=OBSERVED)


def status(result, source="prs_opened"):
    return next(s for s in result.statuses if s.source == source)


@pytest.mark.parametrize("review", [False, True])
def test_quarterly_sources_and_publish_gates_batch_more_than_100_nodes(review):
    client = QuarterlyGitHub(count=205, review=review, submitted="2026-07-02T00:00:00Z")
    result = run(client)
    assert all(s.status == "complete" for s in result.statuses)
    assert len(result.events) == 205
    assert {e.subject_node_id for e in result.events} == set(client.ids)
    assert all(len(ids) <= 100 for _, ids in client.node_calls)
    if review:
        assert client.review_reads == 410
        assert any(ids[0].startswith('RV') for _, ids in client.node_calls)


@pytest.mark.parametrize("fault", ["missing", "reordered", "null", "duplicate", "extra", "failure"])
def test_later_nodes_batch_cannot_mask_incomplete_or_wrong_identity(fault):
    from github_member_activity.collector import _query_nodes

    class Broken(QuarterlyGitHub):
        def graphql(self, query, variables):
            if variables['ids'][0] == 'P100':
                if fault == 'failure':
                    raise GitHubRequestError('graphql_partial_response')
                nodes = super().graphql(query, variables)['nodes']
                if fault == 'missing': nodes.pop()
                elif fault == 'reordered': nodes.reverse()
                elif fault == 'null': nodes[0] = None
                elif fault == 'duplicate': nodes[1] = deepcopy(nodes[0])
                elif fault == 'extra': nodes.append(deepcopy(nodes[-1]))
                return {'nodes': nodes}
            return super().graphql(query, variables)
    client = Broken(count=205)
    with pytest.raises((RuntimeError, GitHubRequestError)):
        _query_nodes(client, DISCOVERY_QUERY, client.ids)


@pytest.mark.parametrize(('rest', 'canonical'), [
    ('2026-07-02T00:00:01Z', '2026-07-02T00:00:00Z'),
    ('2026-07-01T23:59:59Z', '2026-07-02T00:00:00Z'),
    ('2026-06-30T15:59:59Z', '2026-06-30T16:00:00Z'),
])
def test_one_second_rest_difference_uses_stable_graphql_event_time(rest, canonical):
    result = run(QuarterlyGitHub(rest_created=rest, created=canonical))
    assert status(result).status == 'complete'
    assert [e.occurred_at for e in result.events] == [canonical]


@pytest.mark.parametrize(('rest', 'canonical'), [
    ('2026-06-30T16:00:00Z', '2026-06-30T15:59:59Z'),
    ('2026-09-07T15:59:59Z', '2026-09-07T16:00:00Z'),
])
def test_canonical_time_outside_half_open_window_is_not_counted(rest, canonical):
    result = run(QuarterlyGitHub(rest_created=rest, created=canonical))
    assert status(result).status == 'complete'
    assert result.events == []


def test_larger_rest_difference_still_fails():
    result = run(QuarterlyGitHub(rest_created='2026-07-02T00:00:02Z'))
    assert status(result).reason == 'search_candidate_conflict'
    assert not result.events


@pytest.mark.parametrize(('source', 'kind'), [('issues_opened', 'issue_opened'), ('authored_prs_merged', 'pr_merged')])
def test_time_reconciliation_also_covers_issue_creation_and_merged_prs(source, kind):
    class OtherSource(QuarterlyGitHub):
        def search(self, query, *, page=1):
            selected = 'is:issue' in query if source == 'issues_opened' else 'is:merged' in query
            if not selected:
                return SearchPage((), 0, False)
            return SearchPage(({'node_id': 'P0', 'actor_node_id': 'U1', 'created_at': self.rest_created},), 1, False)

        def node(self, key):
            node = super().node(key)
            if kind == 'issue_opened':
                node['__typename'] = 'Issue'
            else:
                node['mergedAt'] = '2026-07-03T00:00:00Z'
            return node

    result = run(OtherSource())
    assert status(result, source).status == 'complete'
    assert [(e.event_kind, e.occurred_at) for e in result.events] == [(kind, '2026-07-02T00:00:00Z' if kind == 'issue_opened' else '2026-07-03T00:00:00Z')]


def test_failed_later_batch_blocks_source_completion_and_events():
    class Incomplete(QuarterlyGitHub):
        def graphql(self, query, variables):
            if query == DISCOVERY_QUERY and self.discovery_calls == 1:
                raise GitHubRequestError('graphql_partial_response')
            return super().graphql(query, variables)

    result = run(Incomplete(count=205))
    assert status(result).status == 'partial'
    assert not result.events


def test_changed_timestamp_after_discovery_is_not_accepted_as_tolerance():
    client = QuarterlyGitHub()
    client.hydrated_created = '2026-07-02T00:00:01Z'
    result = run(client)
    assert status(result).reason == 'graphql_snapshot_unstable'
    assert not result.events


def test_search_attributed_bot_is_excluded_only_after_stable_public_discovery():
    result = run(QuarterlyGitHub(author_type='Bot'))
    assert all(s.status == 'complete' for s in result.statuses)
    assert not result.events


@pytest.mark.parametrize('visibility', ['PRIVATE', 'INTERNAL'])
def test_bot_does_not_bypass_visibility_gate(visibility):
    client = QuarterlyGitHub(author_type='Bot', visibility=visibility)
    result = run(client)
    assert status(result).reason == 'visibility_unverified'
    assert not any(q == HYDRATE_QUERY for q, _ in client.node_calls)


def test_other_human_is_not_silently_filtered_as_bot():
    result = run(QuarterlyGitHub(author_id='OTHER'))
    assert status(result).status == 'failed'
    assert not result.events


def test_changing_author_type_is_not_silently_filtered():
    client = QuarterlyGitHub()
    client.unstable_author = True
    result = run(client)
    assert status(result).reason == 'graphql_snapshot_unstable'


def test_outside_contribution_with_no_in_window_review_is_excluded_after_read():
    client = QuarterlyGitHub(review=True)
    result = run(client)
    assert status(result, 'prs_reviewed').status == 'complete'
    assert client.review_reads == 2
    assert not result.events


def test_outside_contribution_with_actual_in_window_review_is_kept():
    result = run(QuarterlyGitHub(review=True, submitted='2026-07-02T00:00:00Z'))
    assert status(result, 'prs_reviewed').status == 'complete'
    assert [(e.event_kind, e.occurred_at) for e in result.events] == [('pr_reviewed', '2026-07-02T00:00:00Z')]


def test_in_window_candidate_missing_eligible_review_still_fails():
    result = run(QuarterlyGitHub(review=True, contribution_at='2026-07-02T00:00:00Z'))
    assert status(result, 'prs_reviewed').reason == 'api_contract_violation'


def test_outside_candidate_cannot_hide_incomplete_reviews_connection():
    class Incomplete(QuarterlyGitHub):
        def connection(self, query, variables, path):
            if query == REVIEWS_QUERY:
                raise GitHubRequestError('pagination_incomplete')
            return super().connection(query, variables, path)
    result = run(Incomplete(review=True))
    assert status(result, 'prs_reviewed').reason == 'pagination_incomplete'
    assert not result.events
