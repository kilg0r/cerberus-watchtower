import os
import subprocess
from pathlib import Path

import pytest

from watchtower.config import RepoConfig
from watchtower.scanners import git_history


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str, date: str, body: str | None = None) -> str:
    """Stage everything and commit with a fixed author/committer date."""
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    _git(repo, "add", "-A")
    args = ["commit", "-m", message]
    if body:
        args += ["-m", body]
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=env
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history_repo(tmp_path: Path) -> dict:
    """A repo with four dated commits; returns paths + the sha of each commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.local")
    _git(repo, "config", "user.name", "Test")

    (repo / "a.py").write_text("1\n", encoding="utf-8")
    (repo / "b.py").write_text("x\n", encoding="utf-8")
    c1 = _commit(repo, "initial", "2026-06-10T10:00:00", body="the very first commit")

    (repo / "a.py").write_text("1\n2\n", encoding="utf-8")
    c2 = _commit(repo, "extend a", "2026-06-10T11:00:00")

    (repo / "c.py").write_text("new\n", encoding="utf-8")
    (repo / "b.py").unlink()
    c3 = _commit(repo, "add c, drop b", "2026-06-12T09:00:00")

    (repo / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
    c4 = _commit(repo, "extend a again", "2026-06-12T15:00:00")

    return {"repo": repo, "c1": c1, "c2": c2, "c3": c3, "c4": c4}


def test_list_commits_order_and_numstat(history_repo):
    commits = git_history.list_commits(history_repo["repo"])
    assert [c["subject"] for c in commits] == [
        "extend a again",
        "add c, drop b",
        "extend a",
        "initial",
    ]
    c4 = commits[0]
    assert c4["sha"] == history_repo["c4"]
    assert c4["additions"] == 1 and c4["deletions"] == 0
    assert c4["files_changed"] == 1
    assert c4["files"][0]["path"] == "a.py"
    # root commit has no parent; later commits do
    assert commits[-1]["parents"] == []
    assert commits[0]["parents"] == [history_repo["c3"]]


def test_list_commits_skip_and_limit(history_repo):
    page = git_history.list_commits(history_repo["repo"], limit=2, skip=1)
    assert [c["subject"] for c in page] == ["add c, drop b", "extend a"]


def test_commit_detail_status_and_body(history_repo):
    detail = git_history.commit_detail(history_repo["repo"], history_repo["c3"])
    assert detail is not None
    by_path = {f["path"]: f for f in detail["files"]}
    assert by_path["c.py"]["status"] == "added"
    assert by_path["b.py"]["status"] == "deleted"

    root = git_history.commit_detail(history_repo["repo"], history_repo["c1"])
    assert root["body"] == "the very first commit"
    assert {f["path"] for f in root["files"]} == {"a.py", "b.py"}


def test_commit_detail_unknown_returns_none(history_repo):
    assert git_history.commit_detail(history_repo["repo"], "deadbeef") is None


def test_commit_diff_single_file(history_repo):
    diff = git_history.commit_diff(history_repo["repo"], history_repo["c2"], "a.py")
    assert "+2" in diff
    assert "a.py" in diff


def test_range_files_aggregates_contiguous(history_repo):
    result = git_history.range_files(
        history_repo["repo"], history_repo["c2"], history_repo["c4"]
    )
    # range c2..c4 = three commits, net effect vs c1
    assert len(result["commits"]) == 3
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["a.py"]["status"] == "modified"
    assert by_path["a.py"]["additions"] == 2  # +1 in c2, +1 in c4
    assert by_path["c.py"]["status"] == "added"
    assert by_path["b.py"]["status"] == "deleted"


def test_range_files_from_root(history_repo):
    # base == root commit: parent is the empty tree, everything reads as added
    result = git_history.range_files(
        history_repo["repo"], history_repo["c1"], history_repo["c2"]
    )
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["a.py"]["status"] == "added"
    assert by_path["b.py"]["status"] == "added"


def test_set_files_union_of_cherry_picks(history_repo):
    result = git_history.set_files(
        history_repo["repo"], [history_repo["c2"], history_repo["c4"]]
    )
    by_path = {f["path"]: f for f in result["files"]}
    assert set(by_path) == {"a.py"}
    assert by_path["a.py"]["additions"] == 2  # summed across both commits
    assert len(result["commits"]) == 2


def test_set_diff_labels_each_commit(history_repo):
    diff = git_history.set_diff(
        history_repo["repo"], [history_repo["c2"], history_repo["c4"]]
    )
    assert diff.count("# commit ") == 2


def test_order_endpoints_picks_oldest_parent_and_newest_head(history_repo):
    base_parent, head = git_history.order_endpoints(
        history_repo["repo"], [history_repo["c4"], history_repo["c2"]]
    )
    assert head == history_repo["c4"]
    assert base_parent == history_repo["c1"]  # parent of the oldest selected (c2)


def test_base_parent_root_is_none(history_repo):
    assert git_history.base_parent(history_repo["repo"], history_repo["c1"]) is None
    assert (
        git_history.base_parent(history_repo["repo"], history_repo["c2"])
        == history_repo["c1"]
    )


def test_commits_for_session_time_and_file_overlap(history_repo):
    repo = history_repo["repo"]
    commits = git_history.commits_for_session(
        repo,
        started_at="2026-06-12T08:30:00",
        last_activity="2026-06-12T16:00:00",
        files_edited=[str(repo / "a.py")],  # absolute path, like a transcript records
    )
    by_sha = {c["sha"]: c for c in commits}
    # window covers c3 and c4 only
    assert set(by_sha) == {history_repo["c3"], history_repo["c4"]}
    # c4 touches a.py (edited) -> files match; c3 does not
    assert by_sha[history_repo["c4"]]["matched_by"] == ["time", "files"]
    assert by_sha[history_repo["c3"]]["matched_by"] == ["time"]


def test_commits_for_session_bad_timestamp_returns_empty(history_repo):
    assert git_history.commits_for_session(history_repo["repo"], None, None, []) == []


def test_history_all_merges_and_tags(history_repo, tmp_path: Path):
    repo_a = RepoConfig(
        id="alpha", name="Alpha", path=history_repo["repo"], stack="python", group="cerberus"
    )
    # a second, newer repo
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _git(other, "config", "user.email", "t@t.local")
    _git(other, "config", "user.name", "T")
    (other / "z.txt").write_text("z\n", encoding="utf-8")
    _commit(other, "newer elsewhere", "2026-06-14T12:00:00")
    repo_b = RepoConfig(
        id="beta", name="Beta", path=other, stack="generic", group="cerberus"
    )

    merged = git_history.history_all([repo_a, repo_b], limit=10)
    assert merged[0]["repo_id"] == "beta"  # 2026-06-14 is newest
    assert all("repo_id" in c and "repo_name" in c for c in merged)
    assert {c["repo_id"] for c in merged} == {"alpha", "beta"}


def test_list_commits_missing_repo_returns_empty(tmp_path: Path):
    assert git_history.list_commits(tmp_path / "nope") == []
