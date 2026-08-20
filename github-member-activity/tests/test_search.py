from datetime import UTC, datetime

from github_member_activity.github_client import SearchPage
from github_member_activity.search import stable_search


class FakeSearch:
    def __init__(self):
        self.calls = []

    def search(self, query: str, *, page: int = 1):
        self.calls.append((query, page))
        return SearchPage(({"node_id": "P_1", "id": 1, "actor_node_id": "U_1", "created_at": "2026-01-01T00:00:00Z"},), 1, False)


def test_search_uses_created_sort_and_two_stable_snapshots():
    client = FakeSearch()
    rows = stable_search(client, "is:pr is:public author:alice", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    assert rows[0].node_id == "P_1"
    assert len(client.calls) == 2
    assert all("created:" in query for query, _ in client.calls)
