from __future__ import annotations

from dataclasses import dataclass

from .config import RepositoryPolicyConfig


@dataclass(frozen=True, slots=True)
class RepositoryMetadata:
    node_id: str
    full_name: str
    owner_node_id: str
    owner_login: str
    visibility: str


def public_and_allowed(repo: RepositoryMetadata, policy: RepositoryPolicyConfig) -> bool:
    return (
        repo.visibility == "PUBLIC"
        and repo.node_id not in policy.excluded_repo_ids
        and repo.owner_node_id not in policy.excluded_owner_ids
    )


def classify_owner(owner_login: str, policy: RepositoryPolicyConfig) -> str:
    return "first_party" if owner_login.lower() in set(policy.first_party_owners) else "external"
