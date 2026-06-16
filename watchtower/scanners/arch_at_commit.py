"""Architecture of a repo's tree as it stood at a past commit.

The tree is materialized **read-only** with `git archive <sha>` (no working-tree
checkout, no `git worktree` admin writes), extracted into a throwaway temp dir,
and run through the same `scan_architecture` dispatch the live view uses. A
commit's tree is immutable, so the full payload is cached forever per (repo, sha)
in `CommitArchCache` - the first look at a commit pays the scan, every later one
is instant.

This backs the bundle architectural-delta: scan at the bundle's base parent and
at its head, then `drift.diff_arch` the two.
"""

import dataclasses
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import DATA_DIR, RepoConfig
from ..db import get_session_factory
from ..models import CommitArchCache
from . import scan_architecture

_TMP_ROOT = DATA_DIR / "_arch_tmp"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024  # skip pathologically large trees
ARCHIVE_TIMEOUT_SECONDS = 120


def _cached(repo_id: str, sha: str) -> dict | None:
    with get_session_factory()() as session:
        row = session.execute(
            select(CommitArchCache).where(
                CommitArchCache.repo_id == repo_id, CommitArchCache.commit_sha == sha
            )
        ).scalar_one_or_none()
        return json.loads(row.payload) if row else None


def _store(repo_id: str, sha: str, payload: dict) -> None:
    with get_session_factory()() as session:
        exists = session.execute(
            select(CommitArchCache.id).where(
                CommitArchCache.repo_id == repo_id, CommitArchCache.commit_sha == sha
            )
        ).first()
        if exists:
            return
        session.add(
            CommitArchCache(
                repo_id=repo_id,
                commit_sha=sha,
                payload=json.dumps(payload),
                generated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


def _archive_bytes(repo_path: Path, sha: str) -> bytes | None:
    """Read-only tar of the tree at `sha`. Binary, so it bypasses the text-mode
    `_git` helper but stays an explicit-args, non-shell, non-mutating git call."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "archive", "--format=tar", sha],
            capture_output=True,
            timeout=ARCHIVE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def arch_at_commit(repo: RepoConfig, sha: str) -> dict | None:
    """Full architecture payload of `repo` at commit `sha`, or None if the tree
    can't be scanned. Cached per (repo, sha). Never raises."""
    try:
        cached = _cached(repo.id, sha)
        if cached is not None:
            return cached

        data = _archive_bytes(repo.path, sha)
        if not data or len(data) > MAX_ARCHIVE_BYTES:
            return None

        _TMP_ROOT.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(dir=_TMP_ROOT))
        try:
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                tar.extractall(tmp_dir, filter="data")  # 3.12 safe-extract filter
            scoped = dataclasses.replace(repo, path=tmp_dir)
            result = scan_architecture(scoped)
            if result is None:
                return None
            _store(repo.id, sha, result)
            return result
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        return None
