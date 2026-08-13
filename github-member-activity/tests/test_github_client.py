import pytest

from github_member_activity.github_client import GitHubClient, GitHubRequestError


class TwoPageClient(GitHubClient):
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = 0

    def graphql(self, query, variables):
        self.calls += 1
        return next(self.pages)


def page(cursor, node, has_next, end):
    return {"root": {"totalCount": 2, "edges": [{"cursor": cursor, "node": node}], "pageInfo": {"hasNextPage": has_next, "endCursor": end}}}


def test_graphql_two_pages_accepts_end_cursor_equal_to_last_edge():
    client = TwoPageClient([page("c1", {"id": "n1"}, True, "c1"), page("c2", {"id": "n2"}, False, "c2")])
    assert client.connection("", {}, ("root",)) == [{"id": "n1"}, {"id": "n2"}]


def test_graphql_duplicate_stable_node_fails():
    client = TwoPageClient([page("c1", {"id": "n1"}, True, "c1"), page("c2", {"id": "n1"}, False, "c2")])
    with pytest.raises(GitHubRequestError):
        client.connection("", {}, ("root",))


def test_graphql_total_count_overrun_fails_before_requesting_another_page():
    pages = [
        {"root": {"totalCount": 1, "edges": [{"cursor": "c1", "node": {"id": "n1"}}], "pageInfo": {"hasNextPage": True, "endCursor": "A"}}},
        {"root": {"totalCount": 1, "edges": [{"cursor": "c2", "node": {"id": "n2"}}], "pageInfo": {"hasNextPage": True, "endCursor": "B"}}},
        {"root": {"totalCount": 1, "edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}},
    ]
    client = TwoPageClient(pages)
    with pytest.raises(GitHubRequestError, match="graphql_cardinality_mismatch"):
        client.connection("", {}, ("root",))
    assert client.calls == 2


def test_graphql_cursor_cycle_fails_closed():
    pages = [
        {"root": {"totalCount": 3, "edges": [{"cursor": "c1", "node": {"id": "n1"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c2"}}},
        {"root": {"totalCount": 3, "edges": [{"cursor": "c2", "node": {"id": "n2"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}},
        {"root": {"totalCount": 3, "edges": [{"cursor": "c3", "node": {"id": "n3"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}},
    ]
    with pytest.raises(GitHubRequestError, match="cursor_invalid"):
        TwoPageClient(pages).connection("", {}, ("root",))


def test_graphql_non_adjacent_cursor_cycle_fails_closed():
    pages = [
        {"root": {"totalCount": 3, "edges": [{"cursor": "edge-1", "node": {"id": "n1"}}], "pageInfo": {"hasNextPage": True, "endCursor": "A"}}},
        {"root": {"totalCount": 3, "edges": [{"cursor": "edge-2", "node": {"id": "n2"}}], "pageInfo": {"hasNextPage": True, "endCursor": "B"}}},
        {"root": {"totalCount": 3, "edges": [{"cursor": "edge-3", "node": {"id": "n3"}}], "pageInfo": {"hasNextPage": True, "endCursor": "A"}}},
    ]
    with pytest.raises(GitHubRequestError, match="cursor_invalid"):
        TwoPageClient(pages).connection("", {}, ("root",))


def test_rest_search_projects_only_safe_fields_and_fixed_order():
    class Response:
        status_code = 200
        headers = {}
        is_error = False

        def json(self):
            return {"total_count": 1, "incomplete_results": False, "items": [{"id": 1, "node_id": "N1", "created_at": "2026-01-01T00:00:00Z", "user": {"node_id": "U1", "title": object()}, "title": object(), "body": object()}]}

    class Transport:
        def __init__(self):
            self.headers = {}
            self.kwargs = None

        def request(self, method, path, **kwargs):
            self.kwargs = (method, path, kwargs)
            return Response()

    transport = Transport()
    client = GitHubClient("token", client=transport)
    result = client.search("is:pr", page=1)
    assert result.items == ({"id": 1, "node_id": "N1", "actor_node_id": "U1", "created_at": "2026-01-01T00:00:00Z"},)
    assert transport.kwargs == ("GET", "/search/issues", {"params": {"q": "is:pr", "sort": "created", "order": "asc", "per_page": 100, "page": 1}})
