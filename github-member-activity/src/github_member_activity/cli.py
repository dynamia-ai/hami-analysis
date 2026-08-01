from __future__ import annotations

import json
import os
import shutil
import subprocess
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .canonical import canonical_json, sha256_bytes, sha256_json
from .collector import collect, empty_statuses
from .config import AppConfig, load_config, member_config_sha256, safe_resolved_config, token_for
from .github_client import GitHubClient
from .manifest import ARTIFACTS, ARTIFACT_FILES, digest_file, ledger_text, run_id, source_status_object, source_summary, verify_directory, write_diagnostic, write_published
from .metrics import aggregate
from .period import build_period, effective_window, format_z, validate_local_date
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


def _validate_receipt_path(path: Path) -> None:
    runner_temp = os.environ.get("RUNNER_TEMP")
    expected = Path(runner_temp).resolve() / "github-member-activity-receipt.json" if runner_temp else None
    if expected is None or path != expected or path.parent != expected.parent:
        raise ValueError("receipt path must be the fixed RUNNER_TEMP child")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("receipt target has unsafe type")


def _write_receipt(path: Path, report_period, rid: str, run_dir: Path, base_dir: Path) -> None:
    _validate_receipt_path(path)
    if path.exists() or path.is_symlink():
        path.unlink()
    manifest, _ = verify_directory(run_dir)
    if manifest["run_id"] != rid or manifest["period"]["id"] != report_period.id:
        raise ValueError("receipt manifest binding mismatch")
    try:
        relative_run_dir = run_dir.resolve().relative_to(base_dir.resolve())
    except ValueError as exc:
        raise ValueError("receipt run directory escapes configured root") from exc
    if relative_run_dir.is_absolute() or ".." in relative_run_dir.parts or not relative_run_dir.parts:
        raise ValueError("receipt run directory is unsafe")
    period_slug = f"{report_period.start_utc:%Y%m%dt%H%M%Sz}--{report_period.end_utc:%Y%m%dt%H%M%Sz}"
    manifest_sha = digest_file(run_dir / "run-manifest.json")
    receipt = {"schema_version": "1.0", "period_id": report_period.id, "period_utc_slug": period_slug, "run_id": rid, "run_dir": str(relative_run_dir), "manifest_sha256": manifest_sha}
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".github-member-activity-receipt.", delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(canonical_json(receipt) + "\n")
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
        raise


def _remove_owned_run(run_dir: Path, rid: str) -> None:
    if not run_dir.is_dir() or run_dir.is_symlink() or run_dir.name != rid:
        raise ValueError("owned run binding mismatch")
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("owned run manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("run_id") != rid:
        raise ValueError("owned run manifest mismatch")
    shutil.rmtree(run_dir)


def _safe_output_root(config_path: Path, relative: str) -> Path:
    base = config_path.resolve().parent
    current = base
    for part in Path(relative).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("output path escapes through symlink")
        if current.exists() and not current.is_dir():
            raise ValueError("output path is not a directory")
    return current


def _git_commit(config_path: Path) -> str:
    try:
        value = subprocess.run(["git", "rev-parse", "HEAD"], cwd=config_path.resolve().parent.parent, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        value = ""
    return value if len(value) == 40 and all(char in "0123456789abcdef" for char in value) else "unknown"


@app.command("validate-config")
def validate_config(config: Path = typer.Option(..., "--config", exists=True, dir_okay=False), scheduled: bool = typer.Option(False, "--scheduled")) -> None:
    value = _config(config)
    if scheduled and value.period.timezone != "Asia/Shanghai":
        typer.echo("Error: scheduled mode requires Asia/Shanghai", err=True)
        raise typer.Exit(2)
    try:
        _safe_output_root(config, value.output.directory)
        _safe_output_root(config, "diagnostics")
        for member in value.members:
            validate_local_date(member.active_from, value.period.timezone)
            if member.active_until is not None:
                validate_local_date(member.active_until, value.period.timezone)
    except ValueError as exc:
        typer.echo("Error: configured output path is unsafe", err=True)
        raise typer.Exit(2) from exc
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
    if (from_ is None) != (to is None) or (from_ and period is not None) or (from_ and scheduled) or (scheduled and dry_run) or (receipt_path and not scheduled) or (not from_ and period is None):
        typer.echo("Error: invalid option combination", err=True)
        raise typer.Exit(2)
    if receipt_path:
        try:
            _validate_receipt_path(receipt_path)
        except ValueError as exc:
            typer.echo("Error: receipt path is unsafe", err=True)
            raise typer.Exit(2) from exc
    if scheduled and value.period.timezone != "Asia/Shanghai":
        typer.echo("Error: scheduled mode requires Asia/Shanghai", err=True)
        raise typer.Exit(2)
    report_period = _period(value, period or "explicit", from_, to)
    try:
        _safe_output_root(config, value.output.directory)
        _safe_output_root(config, "diagnostics")
        for member in value.members:
            validate_local_date(member.active_from, value.period.timezone)
            if member.active_until is not None:
                validate_local_date(member.active_until, value.period.timezone)
    except ValueError as exc:
        typer.echo("Error: configured path or local date is unsafe", err=True)
        raise typer.Exit(2) from exc
    if dry_run:
        typer.echo(json.dumps({"members": len(value.members), "period": report_period.to_json(), "sources": 6, "output": value.output.directory}, ensure_ascii=False, sort_keys=True))
        return
    observed = datetime.now(UTC).replace(microsecond=0)
    effective = [member for member in value.members if effective_window(report_period, member.active_from, member.active_until)]
    if not effective:
        reason = "no_applicable_members"
        statuses = empty_statuses(value, report_period, observed_at=observed)
    elif (observed - report_period.end_utc).total_seconds() < 86400:
        reason = "stability_gap_not_met"
        statuses = [type(row)(row.member_id, row.source, row.criticality, "not_run", "stability_gap_not_met") if row.status != "not_applicable" else row for row in empty_statuses(value, report_period, observed_at=observed)]
    else:
        result = None
        try:
            token = token_for(value)
        except ValueError as exc:
            typer.echo("Error: configured token is unavailable", err=True)
            raise typer.Exit(2) from exc
        try:
            with GitHubClient(token, api_version=value.github.api_version) as client:
                result = collect(value, report_period, client, observed_at=observed)
            statuses = result.statuses
            reason = "core_source_incomplete" if any(row.criticality == "core" and row.status != "complete" for row in statuses) else None
            if reason is None:
                status_obj = source_status_object(statuses)
                resolved = safe_resolved_config(value, result.applied_owner_ids, result.applied_repo_ids)
                rows = [event.to_dict() for event in result.events]
                status_summary = source_summary(status_obj)
                members = {member.member_id: member.github_login for member in value.members if effective_window(report_period, member.active_from, member.active_until)}
                commit_available = {row.member_id: row.status == "complete" for row in statuses if row.source == "commit_context"}
                summary_value = render_summary(run_id=run_id(format_z(observed)), period=report_period.to_json(), observed_at=format_z(observed), publishable=True, aggregate=aggregate(result.events, list(members), members, set(value.repository_policy.first_party_owners), commit_available))
                csv_value = render_csv(summary_value["members"])
                markdown_value = render_markdown(summary_value, status_obj)
                rid = summary_value["run_id"]
                resolved_bytes = (canonical_json(resolved) + "\n").encode("utf-8")
                ledger_bytes = ledger_text(rows).encode("utf-8")
                status_bytes = (canonical_json(status_obj) + "\n").encode("utf-8")
                summary_bytes = (canonical_json(summary_value) + "\n").encode("utf-8")
                csv_bytes = csv_value.encode("utf-8")
                report_bytes = markdown_value.encode("utf-8")
                manifest_base = {
                    "schema_version": "1.0", "run_id": rid, "collector": {"version": __version__, "git_commit": _git_commit(config)},
                    "github_rest_api_version": value.github.api_version, "period": report_period.to_json(), "observed_at": format_z(observed),
                    "publish_visibility_verified_at": result.publish_visibility_verified_at, "safe_resolved_config_sha256": sha256_bytes(resolved_bytes), "member_config_sha256": member_config_sha256(value),
                    "repository_policy_summary": resolved["repository_policy"], "source_status_summary": status_summary,
                    "semantic_ledger_sha256": sha256_json(sorted(row["normalized_row_digest"] for row in rows)), "diagnostic_source_status": None,
                }
                files = {"resolved-config.json": resolved_bytes, "event-ledger.jsonl": ledger_bytes, "source-status.json": status_bytes, "summary.json": summary_bytes, "summary.csv": csv_bytes, "report.md": report_bytes}
                path = write_published(_safe_output_root(config, value.output.directory), report_period.id, rid, files, manifest_base)
                if receipt_path:
                    try:
                        _write_receipt(receipt_path, report_period, rid, path, config.resolve().parent)
                    except Exception as receipt_exc:
                        try:
                            _remove_owned_run(path, rid)
                        except Exception as cleanup_exc:
                            typer.echo("Error: published artifact compensation failed; preserving the published artifact as the sole authority", err=True)
                            raise typer.Exit(4) from cleanup_exc
                        raise receipt_exc
                typer.echo(f"published: {path}")
                return
        except typer.Exit:
            raise
        except FileExistsError:
            reason = "output_conflict"
            if result is None:
                statuses = empty_statuses(value, report_period, observed_at=observed)
        except OSError:
            reason = "artifact_write_failed"
            if result is None:
                statuses = empty_statuses(value, report_period, observed_at=observed)
        except ValueError:
            reason = "validation_failed"
            if result is None:
                statuses = empty_statuses(value, report_period, observed_at=observed)
        except Exception:
            reason = "run_aborted"
            if result is None:
                statuses = empty_statuses(value, report_period, observed_at=observed)
    rid = run_id(format_z(observed))
    status_obj = source_status_object(statuses)
    summary = {"core_complete": False, "optional_complete": False, "noncomplete": []}
    manifest = {
        "schema_version": "1.0", "run_id": rid, "collector": {"version": __version__, "git_commit": _git_commit(config)},
        "github_rest_api_version": value.github.api_version, "period": report_period.to_json(), "observed_at": format_z(observed),
        "publish_visibility_verified_at": None, "safe_resolved_config_sha256": None, "member_config_sha256": None,
        "repository_policy_summary": {"public_only": True, "first_party_owners": value.repository_policy.first_party_owners, "applied_public_excluded_owner_ids": [], "applied_public_excluded_repo_ids": []},
        "source_status_summary": source_summary(status_obj), "semantic_ledger_sha256": None, "run_status": "diagnostic",
        "run_reason": reason, "publishable": False,
        "artifacts": {key: {"present": False, "sha256": None} for key in ARTIFACTS}, "diagnostic_source_status": status_obj,
        "validator_result": {"status": "failed", "reason": "schema_invalid"} if reason == "validation_failed" else {"status": "not_run", "reason": None},
    }
    try:
        diagnostics = _safe_output_root(config, "diagnostics")
        path = write_diagnostic(diagnostics, manifest)
    except Exception as exc:
        typer.echo("Error: diagnostic artifact write failed", err=True)
        raise typer.Exit(4) from exc
    if receipt_path:
        try:
            _write_receipt(receipt_path, report_period, rid, path, config.resolve().parent)
        except Exception as exc:
            try:
                _remove_owned_run(path, rid)
            except Exception:
                pass
            typer.echo("Error: receipt path is unsafe", err=True)
            raise typer.Exit(4) from exc
    typer.echo(f"diagnostic: {path}", err=True)
    raise typer.Exit(3 if reason in {"stability_gap_not_met", "no_applicable_members"} else 4)


@app.command("verify")
def verify(run_dir: Path = typer.Option(..., "--run-dir", file_okay=False), expected_manifest_sha256: str | None = typer.Option(None, "--expected-manifest-sha256")) -> None:
    try:
        if expected_manifest_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
            raise ValueError("invalid manifest hash")
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
def render(run_dir: Path = typer.Option(..., "--run-dir", file_okay=False)) -> None:
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
