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
