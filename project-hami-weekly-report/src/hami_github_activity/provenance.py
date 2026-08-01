from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


def _git_output(directory: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=directory, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


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
    tracked_diff = _git_output(root_path, "diff", "--binary", "HEAD") or ""
    untracked = _git_output(root_path, "ls-files", "--others", "--exclude-standard") or ""
    untracked_hashes: list[dict[str, str]] = []
    for relative in sorted(filter(None, untracked.splitlines())):
        path = root_path / relative
        if path.is_file():
            untracked_hashes.append({"path": relative, "sha256": _sha256(path)})
    tracked_diff_sha256 = hashlib.sha256(tracked_diff.encode("utf-8")).hexdigest()
    untracked_sha256 = _normalized_json_sha256(untracked_hashes)
    dirty = bool(_git_output(root_path, "status", "--porcelain=v1"))
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
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
