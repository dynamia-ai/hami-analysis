import pytest

from github_member_activity.github_client import GitHubClient, GitHubRequestError


class TwoPageClient(GitHubClient):
    def __init__(self, pages):
        self.pages = iter(pages)

    def graphql(self, query, variables):
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


def test_graphql_cursor_cycle_fails_closed():
    pages = [
        {"root": {"totalCount": 3, "edges": [{"cursor": "c1", "node": {"id": "n1"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c2"}}},
        {"root": {"totalCount": 3, "edges": [{"cursor": "c2", "node": {"id": "n2"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}},
        {"root": {"totalCount": 3, "edges": [{"cursor": "c3", "node": {"id": "n3"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}},
    ]
    with pytest.raises(GitHubRequestError, match="cursor_invalid"):
        TwoPageClient(pages).connection("", {}, ("root",))
