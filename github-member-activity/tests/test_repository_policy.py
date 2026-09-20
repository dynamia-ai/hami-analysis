from github_member_activity.config import RepositoryPolicyConfig
from github_member_activity.repository_policy import RepositoryMetadata, public_and_allowed


def _repo(owner_login: str) -> RepositoryMetadata:
    return RepositoryMetadata("R1", f"{owner_login}/repo", "O1", owner_login, "PUBLIC")


def test_public_repository_owned_by_member_is_excluded_case_insensitively():
    policy = RepositoryPolicyConfig()

    assert not public_and_allowed(_repo("Alice"), policy, member_login="alice")


def test_public_repository_owned_by_someone_else_is_allowed():
    policy = RepositoryPolicyConfig()

    assert public_and_allowed(_repo("Project-HAMi"), policy, member_login="alice")
