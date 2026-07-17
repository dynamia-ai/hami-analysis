from __future__ import annotations

from pathlib import Path
from string import Formatter
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from hami_github_activity.date_range import UTC_PLUS_EIGHT_TIMEZONE


class GithubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org: str = Field(min_length=1)
    token_env: str = Field(min_length=1)


class ScanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(gt=0)
    timezone: str = Field(min_length=1)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        if value != UTC_PLUS_EIGHT_TIMEZONE:
            raise ValueError(f"timezone must be {UTC_PLUS_EIGHT_TIMEZONE} (UTC+8)")
        return value


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github: GithubConfig
    scan: ScanConfig
    output: OutputConfig


def load_config(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"config {path} must contain a YAML mapping")
    return AppConfig.model_validate(raw)


def output_path(config_path: Path, template: str, *, org: str, start_date: str, end_date: str) -> Path:
    allowed_fields = {"org", "start_date", "end_date"}
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(template):
            if field_name is not None and field_name not in allowed_fields:
                raise ValueError(f"unsupported placeholder: {field_name}")
            if format_spec or conversion:
                raise ValueError("format specifications and conversions are not supported")
        rendered = template.format(org=org, start_date=start_date, end_date=end_date)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"invalid output filename template: {exc}") from exc
    path = Path(rendered).expanduser()
    return path if path.is_absolute() else config_path.resolve().parent / path
