from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .canonical import sha256_bytes, sha256_json
from .collector import collect, empty_statuses
from .config import AppConfig, load_config, member_config_sha256, safe_resolved_config, token_for
from .github_client import GitHubClient
from .manifest import ARTIFACTS, ARTIFACT_FILES, digest_file, run_id, source_status_object, source_summary, verify_directory, write_diagnostic
from .period import build_period, format_z
from .renderers import render_csv, render_markdown, render_summary, write_json

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _config(path: Path) -> AppConfig:
    try:
        return load_config(path)
    except ValueError as exc:
        typer.echo(f"Error: configuration validation failed", err=True)
        raise typer.Exit(2) from exc


def _period(config: AppConfig, kind: str, from_: str | None, to: str | None):
    try:
        return build_period("explicit" if from_ or to else kind, config.period.timezone, start=from_, end=to)
    except Exception as exc:
        typer.echo("Error: invalid period", err=True)
        raise typer.Exit(2) from exc


@app.command("validate-config")
def validate_config(config: Path = typer.Option(..., "--config", exists=True, dir_okay=False), scheduled: bool = typer.Option(False, "--scheduled")) -> None:
    value = _config(config)
    if scheduled and value.period.timezone != "Asia/Shanghai":
        typer.echo("Error: scheduled mode requires Asia/Shanghai", err=True)
        raise typer.Exit(2)
    typer.echo(f"valid: members={len(value.members)} timezone={value.period.timezone}")


@app.command("collect")
def collect_command(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
    period: Optional[str] = typer.Option(None, "--period"),
    from_: Optional[str] = typer.Option(None, "--from"),
    to: Optional[str] = typer.Option(None, "--to"),
    scheduled: bool = typer.Option(False, "--scheduled"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    receipt_path: Optional[Path] = typer.Option(None, "--receipt-path"),
) -> None:
    value = _config(config)
    if (from_ is None) != (to is None) or (from_ and period is not None) or (from_ and scheduled) or (scheduled and dry_run) or (receipt_path and not scheduled):
        typer.echo("Error: invalid option combination", err=True)
        raise typer.Exit(2)
    if scheduled and value.period.timezone != "Asia/Shanghai":
        typer.echo("Error: scheduled mode requires Asia/Shanghai", err=True)
        raise typer.Exit(2)
    report_period = _period(value, period or "weekly", from_, to)
    if dry_run:
        typer.echo(json.dumps({"members": len(value.members), "period": report_period.to_json(), "sources": 6, "output": value.output.directory}, ensure_ascii=False, sort_keys=True))
        return
    observed = datetime.now(UTC).replace(microsecond=0)
    effective = [member for member in value.members if __import__("github_member_activity.period", fromlist=["effective_window"]).effective_window(report_period, member.active_from, member.active_until)]
    if not effective:
        reason = "no_applicable_members"
        statuses = empty_statuses(value, report_period, observed_at=observed)
    elif (observed - report_period.end_utc).total_seconds() < 86400:
        reason = "stability_gap_not_met"
        statuses = [type(row)(row.member_id, row.source, row.criticality, "not_run", "stability_gap_not_met") if row.status != "not_applicable" else row for row in empty_statuses(value, report_period, observed_at=observed)]
    else:
        try:
            token = token_for(value)
            with GitHubClient(token, api_version=value.github.api_version) as client:
                result = collect(value, report_period, client, observed_at=observed)
            statuses = result.statuses
            reason = "core_source_incomplete" if any(row.criticality == "core" and row.status != "complete" for row in statuses) else None
        except ValueError as exc:
            typer.echo("Error: configured token is unavailable", err=True)
            raise typer.Exit(2) from exc
        except Exception:
            reason = "run_aborted"
            statuses = empty_statuses(value, report_period, observed_at=observed)
    rid = run_id(format_z(observed))
    status_obj = source_status_object(statuses)
    summary = {"core_complete": False, "optional_complete": False, "noncomplete": []}
    manifest = {
        "schema_version": "1.0", "run_id": rid, "collector": {"version": __version__, "git_commit": "unknown"},
        "github_rest_api_version": value.github.api_version, "period": report_period.to_json(), "observed_at": format_z(observed),
        "publish_visibility_verified_at": None, "safe_resolved_config_sha256": None, "member_config_sha256": None,
        "repository_policy_summary": {"public_only": True, "first_party_owners": value.repository_policy.first_party_owners, "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []},
        "source_status_summary": source_summary(status_obj), "semantic_ledger_sha256": None, "run_status": "diagnostic",
        "run_reason": reason, "publishable": False,
        "artifacts": {key: {"present": False, "sha256": None} for key in ARTIFACTS}, "diagnostic_source_status": status_obj,
        "validator_result": {"status": "not_run", "reason": None},
    }
    root = Path(value.output.directory)
    diagnostics = root.parent / "diagnostics"
    path = write_diagnostic(diagnostics, manifest)
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"schema_version": "1.0", "period_id": report_period.id, "period_utc_slug": f"{format_z(report_period.start_utc).replace('-', '').replace(':', '').replace('T', 't').replace('Z', 'z')}--{format_z(report_period.end_utc).replace('-', '').replace(':', '').replace('T', 't').replace('Z', 'z')}", "run_id": rid, "run_dir": str(path), "manifest_sha256": digest_file(path / "run-manifest.json")}
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as stream:
                stream.write("artifact_ready=true\n")
                stream.write(f"artifact_path={path}\n")
                stream.write(f"period_id={report_period.id}\n")
                stream.write(f"period_utc_slug={receipt['period_utc_slug']}\n")
                stream.write(f"manifest_sha256={receipt['manifest_sha256']}\n")
    typer.echo(f"diagnostic: {path}", err=True)
    raise typer.Exit(3 if reason in {"stability_gap_not_met", "no_applicable_members"} else 4)


@app.command("verify")
def verify(run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False), expected_manifest_sha256: str | None = typer.Option(None, "--expected-manifest-sha256")) -> None:
    try:
        manifest, code = verify_directory(run_dir)
        if expected_manifest_sha256 and digest_file(run_dir / "run-manifest.json") != expected_manifest_sha256:
            raise ValueError("manifest hash mismatch")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo("Error: verification failed", err=True)
        raise typer.Exit(4) from exc
    typer.echo(f"verified: {manifest['run_id']}")
    if code:
        raise typer.Exit(code)


@app.command("render")
def render(run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False)) -> None:
    try:
        _, code = verify_directory(run_dir)
        if code:
            raise typer.Exit(code)
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        statuses = json.loads((run_dir / "source-status.json").read_text(encoding="utf-8"))
        rendered = render_markdown(summary, statuses)
        if rendered != (run_dir / "report.md").read_text(encoding="utf-8"):
            raise ValueError("report mismatch")
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo("Error: render verification failed", err=True)
        raise typer.Exit(4) from exc
    typer.echo(rendered, nl=False)


if __name__ == "__main__":
    app()
