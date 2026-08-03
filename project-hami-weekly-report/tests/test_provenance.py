import hashlib
import os
from pathlib import Path
import subprocess

from hami_github_activity.provenance import capture_worktree_snapshot


def _git(directory: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=directory, check=True, capture_output=True, text=True)


def test_snapshot_requires_a_committed_git_head(tmp_path: Path) -> None:
    repository = tmp_path / "unborn"
    repository.mkdir()
    _git(repository, "init")

    assert capture_worktree_snapshot(repository) is None


def test_clean_commits_have_distinct_worktree_snapshot_digests(tmp_path: Path) -> None:
    repository = tmp_path / "collector"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "collector@example.test")
    _git(repository, "config", "user.name", "Collector Test")
    _git(repository, "config", "commit.gpgsign", "false")
    _git(repository, "config", "core.hooksPath", "")
    _git(repository, "config", "core.quotePath", "false")

    source = repository / "source.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    _git(repository, "add", "source.py")
    _git(repository, "commit", "-m", "first clean commit")
    first = capture_worktree_snapshot(repository)

    source.write_text("VERSION = 2\n", encoding="utf-8")
    _git(repository, "add", "source.py")
    _git(repository, "commit", "-m", "second clean commit")
    second = capture_worktree_snapshot(repository)

    assert first is not None
    assert second is not None
    assert first["dirty"] is False
    assert second["dirty"] is False
    assert first["tracked_diff_sha256"] == second["tracked_diff_sha256"]
    assert first["untracked_sha256"] == second["untracked_sha256"]
    assert first["head"] != second["head"]
    assert first["worktree_snapshot_sha256"] != second["worktree_snapshot_sha256"]


def test_snapshot_hashes_raw_diff_bytes_and_preserves_newline_filenames(tmp_path: Path) -> None:
    repository = tmp_path / "collector-raw"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "collector@example.test")
    _git(repository, "config", "user.name", "Collector Test")
    _git(repository, "config", "commit.gpgsign", "false")
    _git(repository, "config", "core.hooksPath", "")

    source = repository / "source.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    _git(repository, "add", "source.py")
    _git(repository, "commit", "-m", "initial")
    source.write_text("VERSION = 2\n", encoding="utf-8")
    newline_path = repository / "untracked\nname.txt"
    newline_path.write_text("untracked", encoding="utf-8")
    raw_name = b"untracked-\xff.txt"
    non_utf8_path = repository / os.fsdecode(raw_name)
    non_utf8_path.write_bytes(b"non-utf8")

    snapshot = capture_worktree_snapshot(repository)
    assert snapshot is not None
    raw_diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert snapshot["tracked_diff_sha256"] == hashlib.sha256(raw_diff).hexdigest()
    assert any(item["path"] == "untracked\nname.txt" for item in snapshot["untracked"])
    assert any(os.fsencode(item["path"]) == raw_name for item in snapshot["untracked"])
