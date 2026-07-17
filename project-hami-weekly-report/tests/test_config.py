from pathlib import Path

import pytest
from pydantic import ValidationError

from hami_github_activity.config import load_config, output_path


def test_load_config_and_resolve_relative_output(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """github:\n  org: Project-HAMi\n  token_env: GITHUB_TOKEN\nscan:\n  days: 7\n  timezone: Asia/Shanghai\noutput:\n  file: ./output/{org}-{start_date}-{end_date}.md\n""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    path = output_path(
        config_path,
        config.output.file,
        org=config.github.org,
        start_date="2026-07-10",
        end_date="2026-07-16",
    )
    assert path == tmp_path / "output" / "Project-HAMi-2026-07-10-2026-07-16.md"


def test_config_rejects_unknown_options(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """github:\n  org: Project-HAMi\n  token_env: GITHUB_TOKEN\n  transport: gh\nscan:\n  days: 7\n  timezone: UTC\noutput:\n  file: output.md\n""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="transport"):
        load_config(path)


def test_output_template_rejects_unknown_or_advanced_placeholders(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported placeholder"):
        output_path(tmp_path / "config.yaml", "{unknown}.md", org="o", start_date="s", end_date="e")
    with pytest.raises(ValueError, match="format specifications"):
        output_path(tmp_path / "config.yaml", "{org!r}.md", org="o", start_date="s", end_date="e")


def test_config_rejects_non_utc_plus_eight_timezone(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """github:\n  org: Project-HAMi\n  token_env: GITHUB_TOKEN\nscan:\n  days: 7\n  timezone: UTC\noutput:\n  file: output.md\n""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Asia/Shanghai"):
        load_config(path)
