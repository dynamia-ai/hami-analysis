from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from hami_github_activity.collector import ActivityCollector
from hami_github_activity.config import AppConfig, load_config, output_path
from hami_github_activity.date_range import ScanPeriod, build_scan_period
from hami_github_activity.github_client import GitHubClient
from hami_github_activity.markdown_renderer import render_markdown, write_markdown


app = typer.Typer(no_args_is_help=True, help="Collect GitHub evidence for weekly HAMi organization analysis.")
logger = logging.getLogger(__package__)


@contextmanager
def _progress_logging() -> Iterator[None]:
    package_logger = logging.getLogger("hami_github_activity")
    previous_level = package_logger.level
    previous_propagate = package_logger.propagate
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False
    try:
        yield
    finally:
        package_logger.removeHandler(handler)
        handler.close()
        package_logger.setLevel(previous_level)
        package_logger.propagate = previous_propagate


def _load(path: Path) -> AppConfig:
    try:
        return load_config(path)
    except (ValueError, ValidationError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _parse_date(value: str | None, option: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        typer.echo(f"Invalid {option}: {value}; expected YYYY-MM-DD", err=True)
        raise typer.Exit(code=2) from exc


def _period(config: AppConfig, start_date: str | None, end_date: str | None) -> ScanPeriod:
    try:
        return build_scan_period(
            days=config.scan.days,
            timezone=config.scan.timezone,
            start_date=_parse_date(start_date, "--start-date"),
            end_date=_parse_date(end_date, "--end-date"),
        )
    except ValueError as exc:
        typer.echo(f"Date range error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _evidence_path(config_path: Path, config: AppConfig, period: ScanPeriod) -> Path:
    try:
        return output_path(
            config_path,
            config.output.file,
            org=config.github.org,
            start_date=period.start_date,
            end_date=period.end_date,
        )
    except ValueError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _summary(
    *,
    config: AppConfig,
    period: ScanPeriod,
    evidence_path: Path,
    issue_count: int,
    pr_count: int,
    failed_requests: int,
    rate_limit_remaining: int | None,
    dry_run: bool = False,
) -> None:
    typer.echo(f"Organization: {config.github.org}")
    typer.echo(f"Scan start: {period.local_start.isoformat()}")
    typer.echo(f"Scan end: {period.local_end.isoformat()}")
    typer.echo(f"Timezone: {period.timezone}")
    typer.echo(f"Issues: {issue_count}")
    typer.echo(f"Pull requests: {pr_count}")
    typer.echo(f"API request failures: {failed_requests}")
    typer.echo(f"Evidence file: {evidence_path}")
    typer.echo(f"GitHub API rate limit remaining: {rate_limit_remaining if rate_limit_remaining is not None else 'unknown'}")
    if dry_run:
        typer.echo("Dry run: no GitHub requests were made and no evidence file was written.")


@app.command("validate-config")
def validate_config(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False, readable=True)],
) -> None:
    loaded = _load(config)
    period = _period(loaded, None, None)
    _evidence_path(config, loaded, period)
    typer.echo(f"Configuration is valid: {config.resolve()}")


@app.command()
def collect(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False, readable=True)],
    start_date: Annotated[str | None, typer.Option("--start-date", help="Inclusive local date, YYYY-MM-DD.")] = None,
    end_date: Annotated[str | None, typer.Option("--end-date", help="Inclusive local date, YYYY-MM-DD.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the execution plan without calling GitHub.")] = False,
    workers: Annotated[int, typer.Option("--workers", min=1, max=16, help="Maximum concurrent item collectors.")] = 8,
    requests_per_second: Annotated[
        float,
        typer.Option("--requests-per-second", min=1.0, max=15.0, help="Shared GitHub request start-rate limit."),
    ] = 10.0,
) -> None:
    loaded = _load(config)
    period = _period(loaded, start_date, end_date)
    evidence_path = _evidence_path(config, loaded, period)
    if dry_run:
        _summary(
            config=loaded,
            period=period,
            evidence_path=evidence_path,
            issue_count=0,
            pr_count=0,
            failed_requests=0,
            rate_limit_remaining=None,
            dry_run=True,
        )
        return

    token = os.environ.get(loaded.github.token_env)
    if not token:
        typer.echo(f"Missing GitHub token environment variable: {loaded.github.token_env}", err=True)
        raise typer.Exit(code=2)

    with _progress_logging():
        logger.info(
            "Starting GitHub collection for %s (%s through %s, %s), workers=%d, request_rate=%.1f/s",
            loaded.github.org,
            period.local_start.isoformat(),
            period.local_end.isoformat(),
            period.timezone,
            workers,
            requests_per_second,
        )
        with GitHubClient(
            token,
            max_connections=workers,
            requests_per_second=requests_per_second,
        ) as client:
            result = ActivityCollector(client, period, workers=workers).collect(loaded.github.org)
        logger.info("Rendering Markdown evidence")
        content = render_markdown(org=loaded.github.org, period=period, result=result)
        logger.info("Writing evidence file: %s", evidence_path)
        write_markdown(evidence_path, content)
        logger.info("Evidence collection completed")
    _summary(
        config=loaded,
        period=period,
        evidence_path=evidence_path,
        issue_count=len(result.issues),
        pr_count=len(result.pull_requests),
        failed_requests=result.failed_requests,
        rate_limit_remaining=result.rate_limit_remaining,
    )
