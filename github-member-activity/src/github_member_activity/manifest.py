from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .canonical import canonical_bytes, canonical_json, sha256_bytes, sha256_json
from .metrics import aggregate
from .models import LEDGER_FIELDS, SCHEMA_VERSION, SOURCE_ORDER, SourceStatus
from .period import ReportPeriod, _local_midnight, basic_utc, effective_window, format_z, parse_rfc3339
from .validation import validate_evidence_url

ARTIFACTS = ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")
ARTIFACT_FILES = {"resolved_config": "resolved-config.json", "event_ledger": "event-ledger.jsonl", "source_status": "source-status.json", "summary_json": "summary.json", "summary_csv": "summary.csv", "report_md": "report.md"}
RUN_ID_RE = re.compile(r"^[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
STATUS_REASON = {
    "failed": {"identity_resolution_failed", "identity_node_mismatch", "identity_type_mismatch", "identity_login_mismatch", "authentication_failed", "search_candidate_conflict", "graphql_cardinality_mismatch", "cursor_invalid", "api_contract_violation", "visibility_unverified", "repository_binding_changed"},
    "partial": {"search_capped", "search_incomplete_results", "search_cardinality_mismatch", "search_snapshot_unstable", "graphql_partial_response", "graphql_snapshot_unstable", "pagination_incomplete", "rate_limited", "transport_retry_exhausted", "commit_context_unavailable"},
    "not_applicable": {"member_window_empty", "commit_period_not_day_aligned"},
    "not_run": {"stability_gap_not_met", "run_aborted"},
}
IDENTITY_REASONS = {"identity_resolution_failed", "identity_node_mismatch", "identity_type_mismatch", "identity_login_mismatch", "authentication_failed"}
VALIDATOR_REASONS = {"schema_invalid", "artifact_binding_mismatch", "ledger_invalid", "source_status_invalid", "aggregate_mismatch", "privacy_invariant_failed"}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_id(observed_at: str, value: uuid.UUID | None = None) -> str:
    basic = observed_at.replace("-", "").replace(":", "").replace("T", "t").replace("Z", "z")
    return f"{basic}-{str(value or uuid.uuid4()).lower()}"


def _rename_noreplace(source: Path, target: Path) -> None:
    if os.name != "posix":
        if target.exists() or target.is_symlink():
            raise FileExistsError("output_conflict")
        os.rename(source, target)
        return
    import ctypes
    import errno
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError("output_conflict")
        raise OSError(error, os.strerror(error))


def ledger_text(rows: list[dict[str, Any]]) -> str:
    return "".join(canonical_json(row) + "\n" for row in sorted(rows, key=lambda row: (row["member_id"], row["event_kind"], row["occurred_at"] or "~", row["contribution_day"] or "~", row["event_key"])))


def source_status_object(rows: list[SourceStatus | dict[str, Any]]) -> dict[str, Any]:
    values = [row.to_dict() if isinstance(row, SourceStatus) else row for row in rows]
    return {"schema_version": SCHEMA_VERSION, "rows": sorted(values, key=lambda row: (row["member_id"], ("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context").index(row["source"]))) }


def source_summary(status: dict[str, Any]) -> dict[str, Any]:
    rows = status["rows"]
    applicable = [row for row in rows if row["status"] != "not_applicable"]
    core_complete = bool(applicable) and all(row["status"] == "complete" for row in applicable if row["criticality"] == "core")
    optional_complete = bool(applicable) and all(row["status"] in {"complete", "not_applicable"} for row in applicable if row["source"] == "commit_context")
    noncomplete = [{key: row[key] for key in ("member_id", "source", "status", "reason")} for row in rows if row["status"] in {"partial", "failed", "not_run"}]
    return {"core_complete": core_complete, "optional_complete": optional_complete, "noncomplete": noncomplete}


def _assert_no_symlink_components(path: Path) -> None:
    if ".." in path.parts:
        raise ValueError("unsafe path component")
    absolute = path.absolute()
    for component in reversed(absolute.parents):
        if component.exists() and component.is_symlink():
            raise ValueError("path escapes through symlink")
    current = absolute.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("path escapes through symlink")
        current = current.parent


def write_diagnostic(root: Path, manifest: dict[str, Any]) -> Path:
    _assert_no_symlink_components(root)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValueError("unsafe diagnostics root")
    root.mkdir(parents=True, exist_ok=True)
    target = root / manifest["run_id"]
    if target.exists() or target.is_symlink():
        raise FileExistsError("output_conflict")
    temp = root / f".{manifest['run_id']}.tmp"
    if temp.exists():
        raise FileExistsError("output_conflict")
    temp.mkdir()
    try:
        (temp / "run-manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        if {path.name for path in temp.iterdir()} != {"run-manifest.json"}:
            raise ValueError("directory_shape_invalid")
        verify_directory(temp, allow_temp=True)
    except Exception:
        shutil.rmtree(temp)
        raise
    try:
        _rename_noreplace(temp, target)
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise
    return target


def write_published(root: Path, period_id: str, rid: str, files: dict[str, bytes], manifest_base: dict[str, Any]) -> Path:
    """Atomically publish the six bound artifacts and their manifest.

    Callers must provide already validated, public-only bytes. The function never
    overwrites an immutable run directory.
    """
    _assert_no_symlink_components(root)
    if not re.fullmatch(r"(?:weekly|monthly)-[0-9]{8}--[0-9]{8}|explicit-[0-9]{8}t[0-9]{6}z--[0-9]{8}t[0-9]{6}z", period_id) or not RUN_ID_RE.fullmatch(rid):
        raise ValueError("unsafe run path")
    if set(files) != set(ARTIFACT_FILES.values()):
        raise ValueError("artifact set mismatch")
    period_root = root / period_id
    if period_root.exists() and (period_root.is_symlink() or not period_root.is_dir()):
        raise ValueError("unsafe period root")
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValueError("unsafe output root")
    root.mkdir(parents=True, exist_ok=True)
    period_root.mkdir(parents=True, exist_ok=True)
    target = period_root / rid
    if target.exists() or target.is_symlink():
        raise FileExistsError("output_conflict")
    temp = period_root / f".{rid}.tmp"
    if temp.exists():
        raise FileExistsError("output_conflict")
    temp.mkdir()
    bindings: dict[str, dict[str, Any]] = {}
    try:
        for key, filename in ARTIFACT_FILES.items():
            payload = files[filename]
            path = temp / filename
            path.write_bytes(payload)
            bindings[key] = {"present": True, "sha256": digest_file(path)}
        manifest = dict(manifest_base)
        manifest["artifacts"] = bindings
        manifest["run_status"] = "published"
        manifest["publishable"] = True
        manifest["run_reason"] = None
        manifest["validator_result"] = {"status": "passed", "reason": None}
        (temp / "run-manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(temp)
        raise
    if {path.name for path in temp.iterdir()} != {"run-manifest.json", *ARTIFACT_FILES.values()}:
        shutil.rmtree(temp)
        raise ValueError("directory_shape_invalid")
    try:
        verify_directory(temp, allow_temp=True)
    except Exception:
        shutil.rmtree(temp)
        raise
    try:
        _rename_noreplace(temp, target)
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise
    return target


def verify_directory(run_dir: Path, *, allow_temp: bool = False) -> tuple[dict[str, Any], int]:
    _assert_no_symlink_components(run_dir)
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ValueError("invalid run directory")
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("missing manifest")
    manifest = _strict_json(manifest_path)
    required = {"schema_version", "run_id", "collector", "github_rest_api_version", "period", "observed_at", "publish_visibility_verified_at", "safe_resolved_config_sha256", "member_config_sha256", "repository_policy_summary", "source_status_summary", "semantic_ledger_sha256", "run_status", "run_reason", "publishable", "artifacts", "diagnostic_source_status", "validator_result"}
    if set(manifest) != required or manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("run_id"), str) or not RUN_ID_RE.fullmatch(manifest["run_id"]):
        raise ValueError("schema_invalid")
    _validate_manifest_header(manifest)
    expected = {"run-manifest.json"} if manifest.get("run_status") == "diagnostic" else {"run-manifest.json", *ARTIFACT_FILES.values()}
    if not allow_temp and (run_dir.name != manifest["run_id"] or (manifest.get("run_status") == "published" and run_dir.parent.name != manifest["period"]["id"])):
        raise ValueError("run_path_binding_mismatch")
    entries = list(run_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 for path in entries):
        raise ValueError("directory_shape_invalid")
    names = {path.name for path in entries}
    if names != expected:
        raise ValueError("directory_shape_invalid")
    validator = manifest.get("validator_result")
    if not isinstance(validator, dict) or set(validator) != {"status", "reason"} or validator.get("status") not in {"passed", "failed", "not_run"} or (validator.get("reason") is not None and validator.get("reason") not in VALIDATOR_REASONS):
        raise ValueError("schema_invalid")
    if manifest.get("run_status") == "diagnostic":
        validator_result = manifest.get("validator_result")
        validator_valid = (validator_result.get("status") == "failed" and validator_result.get("reason") in VALIDATOR_REASONS) if manifest.get("run_reason") == "validation_failed" else validator_result == {"status": "not_run", "reason": None}
        if manifest.get("publishable") is not False or manifest.get("run_reason") not in {"stability_gap_not_met", "no_applicable_members", "run_aborted", "core_source_incomplete", "validation_failed", "artifact_write_failed", "output_conflict"} or not validator_valid or not isinstance(manifest.get("diagnostic_source_status"), dict):
            raise ValueError("manifest_binding_mismatch")
        if any(manifest.get(field) is not None for field in ("publish_visibility_verified_at", "safe_resolved_config_sha256", "member_config_sha256", "semantic_ledger_sha256")):
            raise ValueError("manifest_binding_mismatch")
        policy = manifest["repository_policy_summary"]
        if policy["applied_public_excluded_owner_ids"] or policy["applied_public_excluded_repo_ids"]:
            raise ValueError("privacy_invariant_failed")
        _validate_status(manifest["diagnostic_source_status"])
        _validate_diagnostic_state(manifest)
        if manifest.get("source_status_summary") != source_summary(manifest["diagnostic_source_status"]):
            raise ValueError("source_status_invalid")
        artifacts = manifest.get("artifacts")
        if set(artifacts or {}) != set(ARTIFACTS) or any(value != {"present": False, "sha256": None} for value in artifacts.values()):
            raise ValueError("manifest_binding_mismatch")
        return manifest, 3
    if manifest.get("run_status") != "published" or manifest.get("publishable") is not True or manifest.get("run_reason") is not None or manifest.get("validator_result") != {"status": "passed", "reason": None} or manifest.get("diagnostic_source_status") is not None:
        raise ValueError("manifest_binding_mismatch")
    if manifest.get("publish_visibility_verified_at") is None or not isinstance(manifest.get("artifacts"), dict) or set(manifest["artifacts"]) != set(ARTIFACTS):
        raise ValueError("manifest_binding_mismatch")
    for key, filename in ARTIFACT_FILES.items():
        path = run_dir / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("artifact_shape_invalid")
        binding = manifest["artifacts"][key]
        if binding != {"present": True, "sha256": digest_file(path)}:
            raise ValueError("artifact_binding_mismatch")
    _validate_published(run_dir, manifest)
    return manifest, 0


def _validate_diagnostic_state(manifest: dict[str, Any]) -> None:
    rows = manifest["diagnostic_source_status"]["rows"]
    reason = manifest["run_reason"]
    by_member: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_member.setdefault(row["member_id"], []).append(row)
    for member_rows in by_member.values():
        core_rows = [row for row in member_rows if row["criticality"] == "core"]
        if any(row["status"] == "not_applicable" for row in core_rows) and any(row["status"] != "not_applicable" for row in core_rows):
            raise ValueError("diagnostic_state_invalid")
        commit_row = next(row for row in member_rows if row["source"] == "commit_context")
        if all(row["status"] == "not_applicable" and row["reason"] == "member_window_empty" for row in core_rows):
            if commit_row["status"] != "not_applicable" or commit_row["reason"] != "member_window_empty":
                raise ValueError("diagnostic_state_invalid")
        elif commit_row["status"] == "not_applicable" and commit_row["reason"] == "member_window_empty":
            raise ValueError("diagnostic_state_invalid")
        identity_reasons = {row["reason"] for row in member_rows if row["reason"] in IDENTITY_REASONS}
        if identity_reasons:
            if len(identity_reasons) != 1 or any(row["status"] != "failed" or row["reason"] != next(iter(identity_reasons)) for row in member_rows):
                raise ValueError("diagnostic_state_invalid")
    if any(row["reason"] == "authentication_failed" for row in rows):
        for member_rows in by_member.values():
            if any(row["status"] != "not_applicable" for row in member_rows) and any(row["status"] != "failed" or row["reason"] != "authentication_failed" for row in member_rows):
                raise ValueError("diagnostic_state_invalid")
    if reason == "no_applicable_members" and any(row["status"] != "not_applicable" or row["reason"] != "member_window_empty" for row in rows):
        raise ValueError("diagnostic_state_invalid")
    if reason == "stability_gap_not_met" and any(row["status"] not in {"not_applicable", "not_run"} or (row["status"] == "not_run" and row["reason"] != "stability_gap_not_met") for row in rows):
        raise ValueError("diagnostic_state_invalid")
    if reason == "core_source_incomplete" and not any(row["criticality"] == "core" and row["status"] in {"partial", "failed", "not_run"} for row in rows):
        raise ValueError("diagnostic_state_invalid")
    if reason == "run_aborted" and not any(row["reason"] == "run_aborted" for row in rows):
        raise ValueError("diagnostic_state_invalid")


def _validate_manifest_header(manifest: dict[str, Any]) -> None:
    collector = manifest.get("collector")
    if not isinstance(collector, dict) or set(collector) != {"version", "git_commit"} or not isinstance(collector["version"], str) or not collector["version"] or not isinstance(collector["git_commit"], str) or not (collector["git_commit"] == "unknown" or re.fullmatch(r"[0-9a-f]{40}", collector["git_commit"])):
        raise ValueError("schema_invalid")
    if not isinstance(manifest.get("github_rest_api_version"), str) or not manifest["github_rest_api_version"]:
        raise ValueError("schema_invalid")
    if not isinstance(manifest.get("observed_at"), str):
        raise ValueError("schema_invalid")
    try:
        observed = parse_rfc3339(manifest["observed_at"])
    except ValueError:
        raise ValueError("schema_invalid") from None
    if format_z(observed) != manifest["observed_at"] or not RUN_ID_RE.fullmatch(str(manifest.get("run_id", ""))) or not manifest["run_id"].startswith(basic_utc(observed) + "-"):
        raise ValueError("schema_invalid")
    period = manifest.get("period")
    if not isinstance(period, dict) or set(period) != {"id", "timezone", "start_local", "end_local", "start_utc", "end_utc"} or not all(isinstance(period[key], str) and period[key] for key in period):
        raise ValueError("period_invalid")
    try:
        zone = ZoneInfo(period["timezone"])
        start = parse_rfc3339(period["start_utc"])
        end = parse_rfc3339(period["end_utc"])
        local_start = datetime.fromisoformat(period["start_local"])
        local_end = datetime.fromisoformat(period["end_local"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("period_invalid") from None
    explicit_id = f"explicit-{basic_utc(start)}--{basic_utc(end)}"
    calendar_id = re.fullmatch(r"(weekly|monthly)-(\d{8})--(\d{8})", period["id"])
    id_valid = period["id"] == explicit_id or (calendar_id is not None and local_start.strftime("%Y%m%d") == calendar_id.group(2) and local_end.strftime("%Y%m%d") == calendar_id.group(3))
    calendar_shape = True
    if calendar_id is not None:
        if calendar_id.group(1) == "weekly":
            calendar_shape = local_start.weekday() == 0 and (local_end.date() - local_start.date()).days == 7
        else:
            next_month = (local_start.date().replace(day=28) + timedelta(days=4)).replace(day=1)
            calendar_shape = local_start.day == 1 and local_end.day == 1 and local_end.date() == next_month
    if end <= start or end - start > timedelta(days=365) or local_start.tzinfo is None or local_end.tzinfo is None or local_start.microsecond or local_end.microsecond or format_z(start) != period["start_utc"] or format_z(end) != period["end_utc"] or local_start.astimezone(UTC) != start or local_end.astimezone(UTC) != end or local_start.astimezone(zone).isoformat() != period["start_local"] or local_end.astimezone(zone).isoformat() != period["end_local"] or not id_valid or not calendar_shape:
        raise ValueError("period_invalid")
    if calendar_id is not None:
        try:
            if local_start.astimezone(UTC) != _local_midnight(local_start.date(), zone).astimezone(UTC) or local_end.astimezone(UTC) != _local_midnight(local_end.date(), zone).astimezone(UTC):
                raise ValueError("period_invalid")
        except ValueError:
            raise ValueError("period_invalid") from None
    policy = manifest.get("repository_policy_summary")
    if not isinstance(policy, dict) or set(policy) != {"public_only", "first_party_owners", "applied_public_excluded_owner_ids", "applied_public_excluded_repo_ids"} or policy.get("public_only") is not True:
        raise ValueError("schema_invalid")
    for key in ("first_party_owners", "applied_public_excluded_owner_ids", "applied_public_excluded_repo_ids"):
        if not isinstance(policy[key], list) or any(not isinstance(item, str) or not item for item in policy[key]) or policy[key] != sorted(set(policy[key])):
            raise ValueError("schema_invalid")


def _strict_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ValueError("invalid JSON") from exc
    if not raw.endswith(b"\n") or raw[:-1].endswith(b"\n"):
        raise ValueError("non-canonical JSON framing")
    value = _strict_json_bytes(raw[:-1])
    if canonical_bytes(value) + b"\n" != raw:
        raise ValueError("non-canonical JSON")
    return value


def _strict_json_bytes(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON root must be object")
    return value


def _strict_json_line(raw: bytes) -> dict[str, Any]:
    value = _strict_json_bytes(raw)
    if canonical_bytes(value) != raw:
        raise ValueError("non-canonical JSON")
    return value


def _validate_status(value: dict[str, Any]) -> None:
    if set(value) != {"schema_version", "rows"} or value["schema_version"] != SCHEMA_VERSION or not isinstance(value["rows"], list):
        raise ValueError("source_status_invalid")
    seen: set[tuple[str, str]] = set()
    allowed = {"member_id", "source", "criticality", "status", "reason", "pagination_complete", "partition_complete", "snapshot_complete", "visibility_complete", "snapshot_completed_at"}
    source_order = {name: index for index, name in enumerate(("prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged", "commit_context"))}
    reasons = set().union(*STATUS_REASON.values())
    previous: tuple[str, int] | None = None
    for row in value["rows"]:
        if not isinstance(row, dict) or set(row) != allowed:
            raise ValueError("source_status_invalid")
        if not isinstance(row["member_id"], str) or not row["member_id"] or not isinstance(row["source"], str) or not isinstance(row["criticality"], str) or not isinstance(row["status"], str):
            raise ValueError("source_status_invalid")
        key = (row["member_id"], row["source"])
        if key in seen or row["source"] not in source_order or row["criticality"] not in {"core", "optional"} or row["status"] not in {"complete", "partial", "failed", "not_applicable", "not_run"}:
            raise ValueError("source_status_invalid")
        seen.add(key)
        order_key = (row["member_id"], source_order[row["source"]])
        if previous is not None and order_key < previous:
            raise ValueError("source_status_invalid")
        previous = order_key
        if row["source"] == "commit_context" and row["criticality"] != "optional":
            raise ValueError("source_status_invalid")
        if row["source"] != "commit_context" and row["criticality"] != "core":
            raise ValueError("source_status_invalid")
        if row["reason"] is not None and (not isinstance(row["reason"], str) or row["reason"] not in reasons):
            raise ValueError("source_status_invalid")
        if any(row[field] is not None and not isinstance(row[field], bool) for field in ("pagination_complete", "partition_complete", "snapshot_complete", "visibility_complete")):
            raise ValueError("source_status_invalid")
        if row["status"] in {"partial", "failed", "not_run"} and row["reason"] is None:
            raise ValueError("source_status_invalid")
        if row["source"] == "commit_context" and row["status"] in {"partial", "failed"} and row["reason"] != "commit_context_unavailable":
            raise ValueError("source_status_invalid")
        if row["reason"] is not None and row["status"] in STATUS_REASON and row["reason"] not in STATUS_REASON[row["status"]]:
            raise ValueError("source_status_invalid")
        if row["reason"] in {"commit_context_unavailable", "commit_period_not_day_aligned"} and row["source"] != "commit_context":
            raise ValueError("source_status_invalid")
        if row["reason"] in {"search_capped", "search_incomplete_results", "search_cardinality_mismatch", "search_snapshot_unstable", "search_candidate_conflict"} and row["source"] not in {"prs_opened", "issues_opened", "authored_prs_merged"}:
            raise ValueError("source_status_invalid")
        if row["status"] == "complete" and not all(row[field] is True for field in ("pagination_complete", "snapshot_complete", "visibility_complete")):
            raise ValueError("source_status_invalid")
        if row["status"] == "complete" and row["source"] not in {"issue_replies", "prs_reviewed"} and row["partition_complete"] is not True:
            raise ValueError("source_status_invalid")
        if row["source"] in {"issue_replies", "prs_reviewed"} and row["status"] == "complete" and row["partition_complete"] is not None:
            raise ValueError("source_status_invalid")
        if row["status"] == "not_applicable" and row["reason"] != "member_window_empty" and row["source"] != "commit_context":
            raise ValueError("source_status_invalid")
        if row["status"] == "not_applicable" and row["source"] == "commit_context" and row["reason"] not in {"member_window_empty", "commit_period_not_day_aligned"}:
            raise ValueError("source_status_invalid")
        if row["status"] == "complete" and row["reason"] is not None:
            raise ValueError("source_status_invalid")
        if row["snapshot_complete"] is True and not isinstance(row["snapshot_completed_at"], str):
            raise ValueError("source_status_invalid")
        if row["snapshot_complete"] is True:
            try:
                timestamp = parse_rfc3339(row["snapshot_completed_at"])
                if format_z(timestamp) != row["snapshot_completed_at"]:
                    raise ValueError
            except ValueError:
                raise ValueError("source_status_invalid") from None
        if row["snapshot_complete"] is False and row["snapshot_completed_at"] is not None:
            raise ValueError("source_status_invalid")
        if row["status"] == "complete":
            if not isinstance(row["snapshot_completed_at"], str):
                raise ValueError("source_status_invalid")
        elif row["status"] in {"not_applicable", "not_run"} and (row["snapshot_complete"] is not None or row["snapshot_completed_at"] is not None):
            raise ValueError("source_status_invalid")
        if row["partition_complete"] is True and row["pagination_complete"] is not True:
            raise ValueError("source_status_invalid")
        if row["snapshot_complete"] is True and row["pagination_complete"] is not True:
            raise ValueError("source_status_invalid")
        if row["status"] in {"not_applicable", "not_run"} or (row["status"] != "complete" and (row["reason"] in IDENTITY_REASONS or row["source"] == "commit_context")):
            if any(row[field] is not None for field in ("pagination_complete", "partition_complete", "snapshot_complete", "visibility_complete")):
                raise ValueError("source_status_invalid")
        elif row["status"] in {"partial", "failed"}:
            applicable_proofs = [row["pagination_complete"], row["snapshot_complete"], row["visibility_complete"]]
            if row["source"] not in {"issue_replies", "prs_reviewed"}:
                applicable_proofs.append(row["partition_complete"])
            if not any(proof is False for proof in applicable_proofs):
                raise ValueError("source_status_invalid")
            if row["source"] in {"issue_replies", "prs_reviewed"} and row["partition_complete"] is not None:
                raise ValueError("source_status_invalid")
            if row["visibility_complete"] is True:
                raise ValueError("source_status_invalid")
    members = {member for member, _ in seen}
    if len(seen) != len(members) * 6:
        raise ValueError("source_status_invalid")


def _validate_published(run_dir: Path, manifest: dict[str, Any]) -> None:
    resolved = _strict_json(run_dir / "resolved-config.json")
    if set(resolved) != {"schema_version", "timezone", "members", "repository_policy"} or resolved["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_invalid")
    try:
        resolved_zone = ZoneInfo(resolved["timezone"])
    except (KeyError, TypeError, ZoneInfoNotFoundError):
        raise ValueError("schema_invalid") from None
    members = resolved["members"]
    if not isinstance(members, list) or any(not isinstance(member, dict) or set(member) != {"member_id", "github_login", "github_node_id", "active_from", "active_until"} for member in members):
        raise ValueError("schema_invalid")
    member_ids: set[str] = set()
    member_logins: set[str] = set()
    member_nodes: set[str] = set()
    for member in members:
        if any(not isinstance(member[key], str) or not member[key] for key in ("member_id", "github_login", "github_node_id", "active_from")) or (member["active_until"] is not None and (not isinstance(member["active_until"], str) or not member["active_until"])):
            raise ValueError("schema_invalid")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", member["member_id"]) or not re.fullmatch(r"(?!-)[A-Za-z0-9-]{1,39}(?<!-)", member["github_login"]):
            raise ValueError("schema_invalid")
        try:
            active_from = date.fromisoformat(member["active_from"])
            active_until = date.fromisoformat(member["active_until"]) if member["active_until"] else None
            _local_midnight(active_from, resolved_zone)
            if active_until is not None:
                _local_midnight(active_until, resolved_zone)
        except ValueError:
            raise ValueError("schema_invalid") from None
        if active_until is not None and active_until <= active_from:
            raise ValueError("schema_invalid")
        if member["member_id"] in member_ids or member["github_login"].lower() in member_logins or member["github_node_id"] in member_nodes:
            raise ValueError("schema_invalid")
        member_ids.add(member["member_id"]); member_logins.add(member["github_login"].lower()); member_nodes.add(member["github_node_id"])
    if members != sorted(members, key=lambda member: member["member_id"]):
        raise ValueError("schema_invalid")
    policy = resolved.get("repository_policy")
    if not isinstance(policy, dict) or set(policy) != {"public_only", "first_party_owners", "applied_public_excluded_owner_ids", "applied_public_excluded_repo_ids"} or policy.get("public_only") is not True:
        raise ValueError("schema_invalid")
    if manifest.get("repository_policy_summary") != policy:
        raise ValueError("artifact_binding_mismatch")
    status = _strict_json(run_dir / "source-status.json")
    _validate_status(status)
    if len(status["rows"]) != len(members) * 6:
        raise ValueError("source_status_invalid")
    if {member.get("member_id") for member in members} != {row["member_id"] for row in status["rows"]}:
        raise ValueError("source_status_invalid")
    local_start = datetime.fromisoformat(manifest["period"]["start_local"])
    local_end = datetime.fromisoformat(manifest["period"]["end_local"])
    period_value = ReportPeriod(manifest["period"]["id"].split("-", 1)[0], manifest["period"]["timezone"], local_start, local_end)
    rows_by_member = {member_id: {row["source"]: row for row in status["rows"] if row["member_id"] == member_id} for member_id in member_ids}
    applicable_ids: set[str] = set()
    for member in members:
        member_rows = rows_by_member[member["member_id"]]
        window = effective_window(period_value, date.fromisoformat(member["active_from"]), date.fromisoformat(member["active_until"]) if member["active_until"] else None)
        if window is None:
            if any(row["status"] != "not_applicable" or row["reason"] != "member_window_empty" for row in member_rows.values()):
                raise ValueError("source_status_invalid")
            continue
        applicable_ids.add(member["member_id"])
        if any(member_rows[source]["status"] != "complete" for source in SOURCE_ORDER[:-1]):
            raise ValueError("core_source_incomplete")
        commit_row = member_rows["commit_context"]
        if any(value.hour or value.minute or value.second for value in window):
            if commit_row["status"] != "not_applicable" or commit_row["reason"] != "commit_period_not_day_aligned":
                raise ValueError("source_status_invalid")
        elif commit_row["status"] == "not_applicable":
            raise ValueError("source_status_invalid")
        elif commit_row["status"] not in {"complete", "partial", "failed"}:
            raise ValueError("source_status_invalid")
    if manifest.get("source_status_summary") != source_summary(status):
        raise ValueError("source_status_invalid")
    if manifest["source_status_summary"].get("core_complete") is not True:
        raise ValueError("core_source_incomplete")
    if manifest.get("safe_resolved_config_sha256") != digest_file(run_dir / "resolved-config.json"):
        raise ValueError("artifact_binding_mismatch")
    if manifest.get("member_config_sha256") != sha256_json(members):
        raise ValueError("artifact_binding_mismatch")
    rows: list[dict[str, Any]] = []
    keys: set[str] = set()
    digests: set[str] = set()
    previous_ledger_key: tuple[str, str, str, str, str] | None = None
    ledger_path = run_dir / "event-ledger.jsonl"
    ledger_raw = ledger_path.read_bytes()
    if ledger_raw and (not ledger_raw.endswith(b"\n") or ledger_raw[:-1].endswith(b"\n")):
        raise ValueError("ledger framing invalid")
    for line in (ledger_raw[:-1].split(b"\n") if ledger_raw else []):
        if not line:
            raise ValueError("ledger framing invalid")
        try:
            value = _strict_json_line(line)
            event = __import__("github_member_activity.models", fromlist=["LedgerEvent"]).LedgerEvent.from_dict(value)
            if event.event_key in keys or event.normalized_row_digest in digests:
                raise ValueError("ledger_invalid")
            keys.add(event.event_key); digests.add(event.normalized_row_digest)
            if event.member_id not in {member["member_id"] for member in members}:
                raise ValueError("ledger_invalid")
            validate_evidence_url(event, next(member["github_login"] for member in members if member["member_id"] == event.member_id))
            source_row = next(row for row in status["rows"] if row["member_id"] == event.member_id and row["source"] == event.source)
            if source_row["status"] != "complete":
                raise ValueError("ledger_invalid")
            snapshot_at = source_row["snapshot_completed_at"]
            if not isinstance(snapshot_at, str) or parse_rfc3339(event.collected_at) > parse_rfc3339(snapshot_at):
                raise ValueError("ledger_invalid")
            ledger_key = (event.member_id, event.event_kind, event.occurred_at or "~", event.contribution_day or "~", event.event_key)
            if previous_ledger_key is not None and ledger_key < previous_ledger_key:
                raise ValueError("ledger_invalid")
            previous_ledger_key = ledger_key
            rows.append(event.to_dict())
        except (json.JSONDecodeError, StopIteration, ValueError, KeyError) as exc:
            raise ValueError("ledger_invalid") from exc
    observed = parse_rfc3339(manifest["observed_at"])
    visibility = parse_rfc3339(manifest["publish_visibility_verified_at"])
    if visibility < observed or format_z(visibility) != manifest["publish_visibility_verified_at"]:
        raise ValueError("ledger_invalid")
    for status_row in status["rows"]:
        snapshot_at = status_row.get("snapshot_completed_at")
        if snapshot_at is not None and not observed <= parse_rfc3339(snapshot_at) <= visibility:
            raise ValueError("source_status_invalid")
    period_start = parse_rfc3339(manifest["period"]["start_utc"])
    period_end = parse_rfc3339(manifest["period"]["end_utc"])
    if observed < period_end + timedelta(days=1):
        raise ValueError("period_invalid")
    member_map = {member["member_id"]: member for member in members}
    zone = resolved_zone
    if manifest["period"]["timezone"] != resolved["timezone"]:
        raise ValueError("period_invalid")
    policy = resolved["repository_policy"]
    for key in ("first_party_owners", "applied_public_excluded_owner_ids", "applied_public_excluded_repo_ids"):
        if not isinstance(policy[key], list) or any(not isinstance(item, str) or not item for item in policy[key]) or policy[key] != sorted(set(policy[key])):
            raise ValueError("schema_invalid")
    excluded_repos = set(policy["applied_public_excluded_repo_ids"])
    excluded_owners = set(policy["applied_public_excluded_owner_ids"])
    for row in rows:
        collected = parse_rfc3339(row["collected_at"])
        verified = parse_rfc3339(row["visibility_verified_at"])
        if not observed <= collected <= verified <= visibility or row["visibility_verified_at"] != manifest["publish_visibility_verified_at"]:
            raise ValueError("ledger_invalid")
        member = member_map[row["member_id"]]
        window = effective_window(period_value, date.fromisoformat(member["active_from"]), date.fromisoformat(member["active_until"]) if member["active_until"] else None)
        if window is None:
            raise ValueError("ledger_invalid")
        if row["event_kind"] in {"pr_opened", "issue_opened", "pr_merged"}:
            expected_partition = f"search-{row['source']}-{basic_utc(window[0].astimezone(UTC))}--{basic_utc(window[1].astimezone(UTC))}"
            if row["query_partition"] != expected_partition:
                raise ValueError("ledger_invalid")
        elif row["event_kind"] in {"issue_replied", "pr_reviewed"}:
            if row["query_partition"] != "root":
                raise ValueError("ledger_invalid")
        elif row["query_partition"] != f"commit-root-{window[0].date().isoformat()}--{window[1].date().isoformat()}":
            raise ValueError("ledger_invalid")
        if row["actor_node_id"] != member["github_node_id"] or row["repo_node_id"] in excluded_repos or row["owner_node_id"] in excluded_owners:
            raise ValueError("ledger_invalid")
        if row["event_kind"] != "commit_day":
            occurred = parse_rfc3339(row["occurred_at"])
            if not period_start <= occurred < period_end:
                raise ValueError("ledger_invalid")
            active_start = datetime.fromisoformat(member["active_from"]).date()
            active_end = datetime.fromisoformat(member["active_until"]).date() if member["active_until"] else None
            local_day = occurred.astimezone(zone).date()
            if local_day < active_start or (active_end is not None and local_day >= active_end):
                raise ValueError("ledger_invalid")
        else:
            contribution_day = date.fromisoformat(row["contribution_day"])
            local_start = period_start.astimezone(zone).date()
            local_end = period_end.astimezone(zone).date()
            active_start = date.fromisoformat(member["active_from"])
            active_end = date.fromisoformat(member["active_until"]) if member["active_until"] else None
            if not local_start <= contribution_day < local_end or contribution_day < active_start or (active_end is not None and contribution_day >= active_end):
                raise ValueError("ledger_invalid")
    if sha256_json(sorted(digests)) != manifest["semantic_ledger_sha256"]:
        raise ValueError("artifact_binding_mismatch")
    summary = _strict_json(run_dir / "summary.json")
    if set(summary) != {"schema_version", "run_id", "period", "observed_at", "publishable", "members", "team"} or summary.get("schema_version") != SCHEMA_VERSION or not isinstance(summary.get("members"), list):
        raise ValueError("schema_invalid")
    metric_keys = {"prs_opened", "issues_opened", "issue_replies_created", "issues_replied_to", "prs_reviewed", "authored_prs_merged", "repositories_touched", "owners_touched", "external_repositories_touched", "repositories_accepting_prs", "commit_contributions", "commit_days", "repositories_with_commits"}
    for member in summary["members"]:
        if not isinstance(member, dict) or set(member) != {"member_id", "github_login", "metrics"} or set(member["metrics"]) != metric_keys:
            raise ValueError("schema_invalid")
    if not isinstance(summary.get("team"), dict) or set(summary["team"]) != {"by_dimension"} or set(summary["team"]["by_dimension"]) != {"prs_opened", "issues_opened", "issue_replies", "prs_reviewed", "authored_prs_merged"}:
        raise ValueError("schema_invalid")
    if summary.get("run_id") != manifest["run_id"] or summary.get("period") != manifest["period"] or summary.get("observed_at") != manifest["observed_at"] or summary.get("publishable") is not True:
        raise ValueError("aggregate_mismatch")
    if {member["member_id"] for member in summary["members"]} != applicable_ids:
        raise ValueError("aggregate_mismatch")
    logins = {member["member_id"]: member["github_login"] for member in members if member["member_id"] in applicable_ids}
    commit_available = {row["member_id"]: row["status"] == "complete" for row in status["rows"] if row["source"] == "commit_context"}
    expected = aggregate([__import__("github_member_activity.models", fromlist=["LedgerEvent"]).LedgerEvent.from_dict(row) for row in rows], list(logins), logins, set(policy["first_party_owners"]), commit_available)
    if summary.get("members") != expected["members"] or summary.get("team") != expected["team"]:
        raise ValueError("aggregate_mismatch")
    source_text = (run_dir / "source-status.json").read_text(encoding="utf-8")
    rendered = __import__("github_member_activity.renderers.markdown", fromlist=["render_markdown"]).render_markdown(summary, status)
    if rendered != (run_dir / "report.md").read_text(encoding="utf-8"):
        raise ValueError("aggregate_mismatch")
    expected_csv = __import__("github_member_activity.renderers.csv", fromlist=["render_csv"]).render_csv(summary["members"])
    if expected_csv != (run_dir / "summary.csv").read_text(encoding="utf-8"):
        raise ValueError("aggregate_mismatch")
