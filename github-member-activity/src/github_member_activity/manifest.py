from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes, sha256_bytes
from .models import LEDGER_FIELDS, SCHEMA_VERSION, SourceStatus

ARTIFACTS = ("resolved_config", "event_ledger", "source_status", "summary_json", "summary_csv", "report_md")
ARTIFACT_FILES = {"resolved_config": "resolved-config.json", "event_ledger": "event-ledger.jsonl", "source_status": "source-status.json", "summary_json": "summary.json", "summary_csv": "summary.csv", "report_md": "report.md"}
RUN_ID_RE = re.compile(r"^[a-z0-9._-]+$")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_id(observed_at: str, value: uuid.UUID | None = None) -> str:
    basic = observed_at.replace("-", "").replace(":", "").replace("T", "t").replace("Z", "z")
    return f"{basic}-{str(value or uuid.uuid4()).lower()}"


def ledger_text(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in sorted(rows, key=lambda row: (row["member_id"], row["event_kind"], row["occurred_at"] or "~", row["contribution_day"] or "~", row["event_key"])))


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


def write_diagnostic(root: Path, manifest: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / manifest["run_id"]
    if target.exists():
        raise FileExistsError("output_conflict")
    temp = root / f".{manifest['run_id']}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir()
    (temp / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def write_published(root: Path, period_id: str, rid: str, files: dict[str, bytes], manifest_base: dict[str, Any]) -> Path:
    """Atomically publish the six bound artifacts and their manifest.

    Callers must provide already validated, public-only bytes. The function never
    overwrites an immutable run directory.
    """
    if set(files) != set(ARTIFACT_FILES.values()):
        raise ValueError("artifact set mismatch")
    period_root = root / period_id
    period_root.mkdir(parents=True, exist_ok=True)
    target = period_root / rid
    if target.exists():
        raise FileExistsError("output_conflict")
    temp = period_root / f".{rid}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir()
    bindings: dict[str, dict[str, Any]] = {}
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
    (temp / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    if {path.name for path in temp.iterdir()} != {"run-manifest.json", *ARTIFACT_FILES.values()}:
        shutil.rmtree(temp)
        raise ValueError("directory_shape_invalid")
    temp.replace(target)
    return target


def verify_directory(run_dir: Path) -> tuple[dict[str, Any], int]:
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ValueError("invalid run directory")
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("missing manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_invalid")
    expected = {"run-manifest.json"} if manifest.get("run_status") == "diagnostic" else {"run-manifest.json", *ARTIFACT_FILES.values()}
    names = {path.name for path in run_dir.iterdir()}
    if names != expected:
        raise ValueError("directory_shape_invalid")
    if manifest.get("run_status") == "diagnostic":
        if manifest.get("publishable") is not False or any(manifest.get("artifacts", {}).get(key, {}).get("present") for key in ARTIFACTS):
            raise ValueError("manifest_binding_mismatch")
        return manifest, 3
    for key, filename in ARTIFACT_FILES.items():
        path = run_dir / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("artifact_shape_invalid")
        if manifest["artifacts"][key]["sha256"] != digest_file(path):
            raise ValueError("artifact_binding_mismatch")
    return manifest, 0
