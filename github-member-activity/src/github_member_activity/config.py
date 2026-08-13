from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import sha256_json
from .models import SCHEMA_VERSION

TOKEN_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
MEMBER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
LOGIN_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,39}(?<!-)$")
NODE_RE = re.compile(r"^[A-Za-z0-9_:+/=\-]+$")


class GithubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token_env: str = Field(min_length=1)
    api_version: str = "2026-03-10"

    @field_validator("token_env")
    @classmethod
    def token_name(cls, value: str) -> str:
        if not TOKEN_RE.fullmatch(value):
            raise ValueError("invalid token environment name")
        return value


class PeriodConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid IANA timezone") from exc
        return value


class MemberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    member_id: str
    github_login: str
    github_node_id: str
    active_from: date
    active_until: date | None = None

    @field_validator("member_id")
    @classmethod
    def member_id_safe(cls, value: str) -> str:
        if not MEMBER_RE.fullmatch(value):
            raise ValueError("invalid member_id")
        return value

    @field_validator("github_login")
    @classmethod
    def login_safe(cls, value: str) -> str:
        if not LOGIN_RE.fullmatch(value):
            raise ValueError("invalid github_login")
        return value

    @field_validator("github_node_id")
    @classmethod
    def node_safe(cls, value: str) -> str:
        if not value or not NODE_RE.fullmatch(value):
            raise ValueError("invalid github_node_id")
        return value

    @model_validator(mode="after")
    def active_range(self) -> "MemberConfig":
        if self.active_until is not None and self.active_until <= self.active_from:
            raise ValueError("active_until must be later than active_from")
        return self


class RepositoryPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    public_only: bool = True
    first_party_owners: list[str] = Field(default_factory=list)
    excluded_owner_ids: list[str] = Field(default_factory=list)
    excluded_repo_ids: list[str] = Field(default_factory=list)

    @field_validator("public_only")
    @classmethod
    def public(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("public_only must be true")
        return value

    @field_validator("first_party_owners")
    @classmethod
    def owners(cls, values: list[str]) -> list[str]:
        if any(not LOGIN_RE.fullmatch(value) for value in values):
            raise ValueError("invalid first_party_owners")
        lowered = [value.lower() for value in values]
        if len(lowered) != len(set(lowered)):
            raise ValueError("duplicate first_party_owners")
        return sorted(lowered)

    @field_validator("excluded_owner_ids", "excluded_repo_ids")
    @classmethod
    def excluded_ids(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value or not NODE_RE.fullmatch(value) for value in values):
            raise ValueError("invalid excluded id")
        if len(values) != len(set(values)):
            raise ValueError("duplicate excluded id")
        return values


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    directory: str = "./output"

    @field_validator("directory")
    @classmethod
    def safe_directory(cls, value: str) -> str:
        if value != "./output":
            raise ValueError("output.directory must be ./output so runtime artifacts remain untracked")
        return value


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    github: GithubConfig
    period: PeriodConfig
    members: list[MemberConfig] = Field(min_length=1)
    repository_policy: RepositoryPolicyConfig
    output: OutputConfig

    @model_validator(mode="after")
    def unique_members(self) -> "AppConfig":
        ids = [m.member_id for m in self.members]
        logins = [m.github_login.lower() for m in self.members]
        nodes = [m.github_node_id for m in self.members]
        if len(ids) != len(set(ids)) or len(logins) != len(set(logins)) or len(nodes) != len(set(nodes)):
            raise ValueError("member identity fields must be unique")
        return self


def load_config(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ValueError("cannot read configuration") from exc
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML configuration") from exc
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a mapping")
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:
        raise ValueError("configuration validation failed") from exc


def token_for(config: AppConfig) -> str:
    token = os.environ.get(config.github.token_env)
    if not token:
        raise ValueError("configured token environment variable is missing")
    return token


def safe_resolved_config(config: AppConfig, applied_owner_ids: list[str] | None = None, applied_repo_ids: list[str] | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "timezone": config.period.timezone,
        "members": [
            {
                "member_id": member.member_id,
                "github_login": member.github_login,
                "github_node_id": member.github_node_id,
                "active_from": member.active_from.isoformat(),
                "active_until": member.active_until.isoformat() if member.active_until else None,
            }
            for member in sorted(config.members, key=lambda item: item.member_id)
        ],
        "repository_policy": {
            "public_only": True,
            "first_party_owners": sorted(config.repository_policy.first_party_owners),
            "applied_public_excluded_owner_ids": sorted(set(applied_owner_ids or [])),
            "applied_public_excluded_repo_ids": sorted(set(applied_repo_ids or [])),
        },
    }


def member_config_sha256(config: AppConfig) -> str:
    return sha256_json([member.model_dump(mode="json") for member in sorted(config.members, key=lambda item: item.member_id)])
