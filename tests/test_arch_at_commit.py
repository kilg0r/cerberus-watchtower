import os
import subprocess
from pathlib import Path

import pytest

from watchtower import db, drift
from watchtower.config import RepoConfig
from watchtower.models import CommitArchCache
from watchtower.scanners import arch_at_commit as aac
from watchtower.scanners.arch_at_commit import arch_at_commit


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-06-10T10:00:00", "GIT_COMMITTER_DATE": "2026-06-10T10:00:00"}
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    )
    return result.stdout.strip()


@pytest.fixture
def py_repo(tmp_path: Path, monkeypatch):
    """A python package committed twice (second commit adds a module + import),
    with the architecture DB + temp-extract dir isolated to tmp_path."""
    db.init_db(tmp_path / "wt.db")  # global factory now has commit_arch_cache
    monkeypatch.setattr(aac, "_TMP_ROOT", tmp_path / "arch_tmp")

    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.local")
    _git(repo, "config", "user.name", "T")

    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "core only")
    c1 = _git(repo, "rev-parse", "HEAD")

    (repo / "pkg" / "util.py").write_text(
        "from pkg import core\n\n\ndef f():\n    return core.VALUE\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add util")
    c2 = _git(repo, "rev-parse", "HEAD")

    config = RepoConfig(id="proj", name="Proj", path=repo, stack="python", group="cerberus")
    return {"config": config, "c1": c1, "c2": c2, "repo": repo, "tmp_root": tmp_path / "arch_tmp"}


def test_arch_at_commit_reflects_committed_tree(py_repo):
    arch = arch_at_commit(py_repo["config"], py_repo["c1"])
    assert arch is not None
    assert arch["stack"] == "python"
    module_ids = {n["id"] for n in arch["graph"]["nodes"]}
    assert "pkg.core" in module_ids
    assert "pkg.util" not in module_ids  # util didn't exist yet at c1


def test_arch_at_commit_ignores_uncommitted_worktree(py_repo):
    # an uncommitted file must NOT appear - we scan the commit's tree, not the worktree
    (py_repo["repo"] / "pkg" / "working_only.py").write_text("X = 1\n", encoding="utf-8")
    arch = arch_at_commit(py_repo["config"], py_repo["c2"])
    module_ids = {n["id"] for n in arch["graph"]["nodes"]}
    assert "pkg.util" in module_ids
    assert not any("working_only" in m for m in module_ids)


def test_arch_at_commit_caches_and_cleans_up(py_repo):
    first = arch_at_commit(py_repo["config"], py_repo["c2"])
    assert first is not None

    with db.get_session_factory()() as session:
        rows = session.query(CommitArchCache).filter_by(
            repo_id="proj", commit_sha=py_repo["c2"]
        ).all()
    assert len(rows) == 1

    second = arch_at_commit(py_repo["config"], py_repo["c2"])
    assert second == first  # served from cache

    # no temp extraction dirs left behind
    tmp_root = py_repo["tmp_root"]
    assert not tmp_root.exists() or not any(tmp_root.iterdir())


def test_diff_arch_between_commits_shows_added_module(py_repo):
    old = arch_at_commit(py_repo["config"], py_repo["c1"])
    new = arch_at_commit(py_repo["config"], py_repo["c2"])
    changes = drift.diff_arch(old, new)
    assert any("util" in m for m in changes["added_projects"])
    assert changes["removed_projects"] == []


def test_diff_arch_root_baseline_is_all_added(py_repo):
    new = arch_at_commit(py_repo["config"], py_repo["c1"])
    changes = drift.diff_arch(None, new)  # no parent tree
    assert "pkg.core" in changes["added_projects"]


def test_arch_at_commit_unknown_sha_returns_none(py_repo):
    assert arch_at_commit(py_repo["config"], "deadbeef") is None
