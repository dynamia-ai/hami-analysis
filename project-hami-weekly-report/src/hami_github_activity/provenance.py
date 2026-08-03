from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

GIT_TIMEOUT_SECONDS = 30.0


def _git_text(directory: Path, *arguments: str) -> str | None:
    """Return command output, preserving successful empty output as ``""``."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip()


def _git_bytes(directory: Path, *arguments: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=directory,
            check=True,
            capture_output=True,
            text=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout


def _git_output(directory: Path, *arguments: str) -> str | None:
    output = _git_text(directory, *arguments)
    return output or None


def capture_worktree_snapshot(directory: Path) -> dict[str, object] | None:
    """Capture source provenance before any GitHub request is started."""
    root = _git_output(directory, "rev-parse", "--show-toplevel")
    if root is None:
        return None
    root_path = Path(root)
    head = _git_output(root_path, "rev-parse", "HEAD")
    if head is None:
        # A repository without a commit has no immutable collector source
        # identity.  Treat it as uncapturable so the caller marks collection
        # partial instead of emitting an unverifiable success-looking digest.
        return None
    tracked_diff = _git_bytes(root_path, "diff", "--binary", "HEAD")
    untracked = _git_bytes(root_path, "ls-files", "--others", "--exclude-standard", "-z")
    status = _git_text(root_path, "status", "--porcelain=v1")
    if tracked_diff is None or untracked is None or status is None:
        return None
    untracked_hashes: list[dict[str, str]] = []
    relative_paths = sorted(
        (os.fsdecode(raw_path) for raw_path in untracked.split(b"\0") if raw_path),
        key=os.fsencode,
    )
    for relative in relative_paths:
        path = root_path / relative
        if path.is_file():
            digest = _sha256(path)
            if digest is None:
                return None
            untracked_hashes.append({"path": relative, "sha256": digest})
    tracked_diff_sha256 = hashlib.sha256(tracked_diff).hexdigest()
    untracked_sha256 = _normalized_json_sha256(untracked_hashes)
    dirty = bool(status)
    return {
        "root": str(root_path),
        "head": head,
        "dirty": dirty,
        "tracked_diff_sha256": tracked_diff_sha256,
        "untracked": untracked_hashes,
        "untracked_sha256": untracked_sha256,
        "worktree_snapshot_sha256": worktree_snapshot_digest(
            head=head,
            dirty=dirty,
            tracked_diff_sha256=tracked_diff_sha256,
            untracked_sha256=untracked_sha256,
        ),
    }


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _normalized_json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def worktree_snapshot_digest(
    *,
    head: str | None,
    dirty: bool,
    tracked_diff_sha256: str,
    untracked_sha256: str,
) -> str:
    """Return the normalized source snapshot digest used in evidence metadata.

    A clean worktree's diff is empty at every commit, so the commit identity and
    dirty state are deliberate digest inputs rather than presentation-only
    provenance.  The component hashes retain a compact, independently
    checkable description of source changes present at collection start.
    """
    return _normalized_json_sha256(
        {
            "dirty": dirty,
            "head": head,
            "tracked_diff_sha256": tracked_diff_sha256,
            "untracked_sha256": untracked_sha256,
        }
    )
