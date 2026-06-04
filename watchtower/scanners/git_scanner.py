"""Git working-tree scanner: builds the review queue from uncommitted changes.

All git access is read-only subprocess calls (`git -C <path> ...`); nothing
here ever mutates a repository.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..config import RepoConfig

# Cap for line-counting untracked files; larger files report 0 additions.
MAX_UNTRACKED_BYTES = 512 * 1024


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _status_label(code: str) -> str:
    if code == "??":
        return "untracked"
    if "R" in code:
        return "renamed"
    if "D" in code:
        return "deleted"
    if "A" in code:
        return "added"
    return "modified"


def _parse_status(repo: Path) -> list[tuple[str, str]] | None:
    """Return [(status_label, path)], or None if `git status` failed
    (missing repo, not a repo, etc.)."""
    result = _git(repo, "status", "--porcelain=v1", "-z")
    if result.returncode != 0:
        return None
    entries = []
    tokens = iter(result.stdout.split("\0"))
    for token in tokens:
        if len(token) < 4:
            continue
        code, path = token[:2], token[3:]
        if "R" in code or "C" in code:
            next(tokens, None)  # consume the rename/copy origin path
        entries.append((_status_label(code), path))
    return entries


def _normalize_numstat_path(raw: str) -> str:
    """Resolve rename notation: 'a/{old => new}/b.cs' or 'old.cs => new.cs'."""
    if "{" in raw and " => " in raw:
        prefix, rest = raw.split("{", 1)
        change, suffix = rest.split("}", 1)
        new = change.split(" => ")[1]
        return (prefix + new + suffix).replace("//", "/")
    if " => " in raw:
        return raw.split(" => ")[1]
    return raw


def _parse_numstat(repo: Path) -> dict[str, tuple[int, int]]:
    """Map path -> (additions, deletions) for tracked changes vs HEAD.
    Binary files (numstat '-') count as 0. Empty repos return {}."""
    result = _git(repo, "diff", "--numstat", "HEAD")
    if result.returncode != 0:
        return {}
    counts: dict[str, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, delete, raw_path = parts
        counts[_normalize_numstat_path(raw_path)] = (
            int(add) if add.isdigit() else 0,
            int(delete) if delete.isdigit() else 0,
        )
    return counts


def _count_untracked_lines(repo: Path, rel_path: str) -> int:
    try:
        file_path = repo / rel_path
        if file_path.stat().st_size > MAX_UNTRACKED_BYTES:
            return 0
        content = file_path.read_bytes()
    except OSError:
        return 0
    if b"\0" in content[:8192]:  # binary
        return 0
    return content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)


def _branch(repo: Path) -> str:
    result = _git(repo, "symbolic-ref", "--short", "HEAD")
    if result.returncode == 0:
        return result.stdout.strip()
    result = _git(repo, "rev-parse", "--short", "HEAD")  # detached
    return result.stdout.strip() or "unknown"


def _ahead_behind(repo: Path) -> tuple[int, int]:
    result = _git(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if result.returncode != 0:  # no upstream configured
        return 0, 0
    parts = result.stdout.split()
    if len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def _last_commit(repo: Path) -> dict | None:
    result = _git(repo, "log", "-1", "--format=%h%x1f%s%x1f%cI")
    if result.returncode != 0 or not result.stdout.strip():
        return None
    hash_, message, date = result.stdout.strip().split("\x1f")
    return {"hash": hash_, "message": message, "date": date}


def file_diff(repo: Path, rel_path: str) -> str | None:
    """Unified diff for one pending file (vs HEAD); untracked files render as a
    pseudo-diff of all-added lines. Returns None when there is nothing to show."""
    result = _git(repo, "diff", "HEAD", "--", rel_path)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout

    # Tracked but unchanged (or unknown) -> nothing to show.
    if _git(repo, "ls-files", "--error-unmatch", rel_path).returncode == 0:
        return None

    # Untracked: render contents as an all-added pseudo-diff.
    try:
        resolved = (repo / rel_path).resolve()
        if not resolved.is_relative_to(repo.resolve()) or not resolved.is_file():
            return None
        if resolved.stat().st_size > MAX_UNTRACKED_BYTES:
            return f"(untracked file too large to display: {rel_path})"
        content = resolved.read_bytes()
    except OSError:
        return None
    if b"\0" in content[:8192]:
        return f"(binary file: {rel_path})"
    lines = content.decode("utf-8", errors="replace").splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    return f"--- /dev/null\n+++ b/{rel_path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def scan_repo(repo: RepoConfig) -> dict | None:
    """Return a review-queue entry, or None when the repo is clean,
    missing, or not a git repository. Never raises."""
    try:
        if not repo.path.is_dir():
            return None
        status_entries = _parse_status(repo.path)
        if not status_entries:
            return None

        numstat = _parse_numstat(repo.path)
        files = []
        for status, path in status_entries:
            if status == "untracked":
                additions, deletions = _count_untracked_lines(repo.path, path), 0
            else:
                additions, deletions = numstat.get(path, (0, 0))
            files.append(
                {"path": path, "status": status, "additions": additions, "deletions": deletions}
            )

        ahead, behind = _ahead_behind(repo.path)
        return {
            "repo_id": repo.id,
            "name": repo.name,
            "group": repo.group,
            "branch": _branch(repo.path),
            "ahead": ahead,
            "behind": behind,
            "files": files,
            "total_additions": sum(f["additions"] for f in files),
            "total_deletions": sum(f["deletions"] for f in files),
            "last_commit": _last_commit(repo.path),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def scan_all(repos: list[RepoConfig]) -> list[dict]:
    """Scan every repo concurrently; preserves registry order, drops clean repos."""
    with ThreadPoolExecutor(max_workers=min(len(repos), 9) or 1) as pool:
        results = list(pool.map(scan_repo, repos))
    return [entry for entry in results if entry is not None]
