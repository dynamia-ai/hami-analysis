import hashlib
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "weekly-hami-org-highlights"
    / "scripts"
    / "record_candidate_pool.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, list[str], list[str]]:
    issues = [f"Project-HAMi/HAMi#{number}" for number in range(1, 26)]
    pull_requests = [f"Project-HAMi/HAMi#{number}" for number in range(101, 126)]
    evidence = tmp_path / "evidence.md"
    evidence.write_text(
        "".join(
            f"<!-- ITEM_START issue {item_id} -->\n<!-- ITEM_END issue {item_id} -->\n"
            for item_id in issues
        )
        + "".join(
            f"<!-- ITEM_START pull_request {item_id} -->\n"
            f"<!-- ITEM_END pull_request {item_id} -->\n"
            for item_id in pull_requests
        ),
        encoding="utf-8",
    )
    trace = tmp_path / "index-trace.jsonl"
    evidence_sha256 = _sha256(evidence)
    trace.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "view": "index",
                "evidence_sha256": evidence_sha256,
                "kind": "issue",
                "item_ids": issues,
            }
        )
        + "\n"
        + json.dumps(
            {
                "schema_version": "1.0",
                "view": "index",
                "evidence_sha256": evidence_sha256,
                "kind": "pull_request",
                "item_ids": pull_requests,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence, trace, issues, pull_requests


def _run(
    tmp_path: Path,
    issue_candidates: list[str],
    *,
    all_pull_requests: bool,
) -> subprocess.CompletedProcess[str]:
    evidence, trace, _, _ = _fixture(tmp_path)
    output = tmp_path / "candidate-pool.json"
    command = [
        sys.executable,
        str(SCRIPT),
        "--evidence",
        str(evidence),
        "--index-trace",
        str(trace),
        "--output",
        str(output),
    ]
    for item_id in issue_candidates:
        command.extend(("--candidate", item_id))
    if all_pull_requests:
        command.append("--all-pull-requests")
    return subprocess.run(command, check=False, capture_output=True, text=True)


def test_candidate_pool_includes_all_pull_requests_beyond_old_cap(tmp_path: Path) -> None:
    _, _, issues, pull_requests = _fixture(tmp_path)
    result = _run(tmp_path, issues[:24], all_pull_requests=True)

    assert result.returncode == 0, result.stderr
    pool = json.loads((tmp_path / "candidate-pool.json").read_text(encoding="utf-8"))
    assert pool["schema_version"] == "1.1"
    assert pool["pull_request_scope"] == "all_evidence"
    assert [candidate["id"] for candidate in pool["candidates"]] == [
        *issues[:24],
        *pull_requests,
    ]


def test_candidate_pool_rejects_missing_pull_requests(tmp_path: Path) -> None:
    _, _, issues, _ = _fixture(tmp_path)
    result = _run(tmp_path, issues[:1], all_pull_requests=False)

    assert result.returncode != 0
    assert "must include every pull request" in result.stderr


def test_candidate_pool_keeps_issue_cap(tmp_path: Path) -> None:
    _, _, issues, _ = _fixture(tmp_path)
    result = _run(tmp_path, issues, all_pull_requests=True)

    assert result.returncode != 0
    assert "maximum is 24" in result.stderr
