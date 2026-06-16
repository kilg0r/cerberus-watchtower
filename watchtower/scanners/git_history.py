"""Committed git history: the read-only backbone of the History view.

Everything here goes through `git_scanner._git` (the single read-only git entry
point) and never mutates a repository. Functions return plain dicts/strings and
never raise - they degrade to empty results so a flaky repo can't 500 the API.

Three jobs:
- list commits (per repo or, via `history_all`, across the whole registry),
- describe a single commit (metadata + per-file status/numstat + diff),
- describe a *bundle* of commits - either a contiguous range (`range_*`) or an
  arbitrary cherry-picked set (`set_*`) - for combined review.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import RepoConfig
from .git_scanner import _git, _normalize_numstat_path, _status_label

# git's canonical empty-tree object - diffing against it yields "everything
# added", which is what the parent of a root commit should look like.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# %x1e (record sep) starts each commit, %x1f (unit sep) splits its fields. The
# subject (%s) never contains newlines, so numstat lines parse cleanly after it.
_LOG_FORMAT = "%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%cI%x1f%P%x1f%s"
_DETAIL_FORMAT = "%H%x1f%h%x1f%an%x1f%ae%x1f%cI%x1f%P%x1f%s%x1f%b"


# --------------------------------------------------------------------------- #
# low-level parsing helpers
# --------------------------------------------------------------------------- #
def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_log(stdout: str) -> list[dict]:
    """Parse `git log --numstat --format=_LOG_FORMAT` output into commit dicts."""
    commits = []
    for record in stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        lines = record.split("\n")
        fields = lines[0].split("\x1f")
        if len(fields) != 7:
            continue
        full, short, author, email, date, parents, subject = fields
        files, adds, dels = [], 0, 0
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add, delete, raw = parts
            a = int(add) if add.isdigit() else 0
            d = int(delete) if delete.isdigit() else 0
            adds += a
            dels += d
            files.append(
                {"path": _normalize_numstat_path(raw), "additions": a, "deletions": d}
            )
        commits.append(
            {
                "sha": full,
                "short": short,
                "author": author,
                "email": email,
                "date": date,
                "parents": parents.split() if parents else [],
                "subject": subject,
                "files": files,
                "additions": adds,
                "deletions": dels,
                "files_changed": len(files),
            }
        )
    return commits


def _numstat(repo_path: Path, *args: str) -> dict[str, tuple[int, int]]:
    """path -> (additions, deletions) from a `git ... --numstat` invocation."""
    result = _git(repo_path, *args)
    counts: dict[str, tuple[int, int]] = {}
    if result.returncode != 0:
        return counts
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, delete, raw = parts
        counts[_normalize_numstat_path(raw)] = (
            int(add) if add.isdigit() else 0,
            int(delete) if delete.isdigit() else 0,
        )
    return counts


def _name_status(repo_path: Path, *args: str) -> dict[str, str]:
    """path -> status_label from a `git ... --name-status -z` invocation."""
    result = _git(repo_path, *args)
    out: dict[str, str] = {}
    if result.returncode != 0:
        return out
    tokens = iter(result.stdout.split("\0"))
    for code in tokens:
        if not code:
            continue
        if code[0] in ("R", "C"):  # rename/copy: code, old-path, new-path
            next(tokens, "")
            path = next(tokens, "")
        else:
            path = next(tokens, "")
        if path:
            out[path] = _status_label(code)
    return out


def _file_list(repo_path: Path, numstat_args: list[str], status_args: list[str]) -> list[dict]:
    """Merge numstat (+/- counts) and name-status (M/A/D/R) into one file list."""
    counts = _numstat(repo_path, *numstat_args)
    status = _name_status(repo_path, *status_args)
    files = []
    for path in sorted(set(counts) | set(status)):
        additions, deletions = counts.get(path, (0, 0))
        files.append(
            {
                "path": path,
                "status": status.get(path, "modified"),
                "additions": additions,
                "deletions": deletions,
            }
        )
    return files


def _rev_parse(repo_path: Path, rev: str) -> str | None:
    result = _git(repo_path, "rev-parse", "--verify", "--quiet", rev)
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def _range_endpoints(repo_path: Path, base: str, head: str) -> tuple[str, str]:
    """(diff-base, head) for a contiguous range whose oldest commit is `base`.
    The diff base is base's parent, or the empty tree when base is a root commit."""
    return _rev_parse(repo_path, f"{base}^") or EMPTY_TREE, head


def _one_line(repo_path: Path, sha: str) -> str:
    result = _git(repo_path, "show", "-s", "--format=%h %s", sha)
    return result.stdout.strip() if result.returncode == 0 else sha


# --------------------------------------------------------------------------- #
# commit listing
# --------------------------------------------------------------------------- #
def list_commits(
    repo_path: Path,
    *,
    limit: int = 50,
    skip: int = 0,
    since: str | None = None,
    until: str | None = None,
    author: str | None = None,
    paths: list[str] | None = None,
    rev_range: str | None = None,
) -> list[dict]:
    """Commits newest-first. `rev_range` (e.g. 'a..b') scopes the walk; `paths`
    restricts to commits touching those paths."""
    args = ["log", f"--max-count={limit}", f"--skip={skip}", "--numstat", f"--format={_LOG_FORMAT}"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    if author:
        args.append(f"--author={author}")
    if rev_range:
        args.append(rev_range)
    if paths:
        args += ["--", *paths]
    result = _git(repo_path, *args)
    if result.returncode != 0:
        return []
    return _parse_log(result.stdout)


def history_all(repos: list[RepoConfig], limit: int = 50) -> list[dict]:
    """Universal feed: newest `limit` commits merged across every registered repo."""

    def _one(repo: RepoConfig) -> list[dict]:
        if not repo.path.is_dir():
            return []
        commits = list_commits(repo.path, limit=limit)
        for commit in commits:
            commit["repo_id"] = repo.id
            commit["repo_name"] = repo.name
            commit["group"] = repo.group
        return commits

    if not repos:
        return []
    with ThreadPoolExecutor(max_workers=min(len(repos), 9) or 1) as pool:
        results = list(pool.map(_one, repos))
    merged = [commit for sub in results for commit in sub]
    merged.sort(key=lambda c: _parse_iso(c["date"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return merged[:limit]


# --------------------------------------------------------------------------- #
# single commit
# --------------------------------------------------------------------------- #
def commit_detail(repo_path: Path, sha: str) -> dict | None:
    """Full metadata + per-file status/numstat for one commit, or None if unknown."""
    meta = _git(repo_path, "show", "-s", f"--format={_DETAIL_FORMAT}", sha)
    if meta.returncode != 0 or not meta.stdout.strip():
        return None
    fields = meta.stdout.split("\x1f")
    if len(fields) < 8:
        return None
    full, short, author, email, date, parents, subject = fields[:7]
    body = fields[7].strip("\n")
    files = _file_list(
        repo_path,
        ["show", "--numstat", "--format=", sha],
        ["show", "--name-status", "-z", "--format=", sha],
    )
    return {
        "sha": full,
        "short": short,
        "author": author,
        "email": email,
        "date": date,
        "parents": parents.split() if parents else [],
        "subject": subject,
        "body": body,
        "files": files,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
        "files_changed": len(files),
    }


def commit_diff(repo_path: Path, sha: str, path: str | None = None) -> str:
    """Unified diff for one commit (optionally a single file)."""
    args = ["show", sha]
    if path:
        args += ["--", path]
    result = _git(repo_path, *args)
    return result.stdout if result.returncode == 0 else ""


# --------------------------------------------------------------------------- #
# bundles - contiguous range
# --------------------------------------------------------------------------- #
def range_files(repo_path: Path, base: str, head: str) -> dict:
    """Combined file list + commit list for the contiguous range base..head."""
    diff_base, head_ref = _range_endpoints(repo_path, base, head)
    files = _file_list(
        repo_path,
        ["diff", "--numstat", f"{diff_base}..{head_ref}"],
        ["diff", "--name-status", "-z", f"{diff_base}..{head_ref}"],
    )
    parent = _rev_parse(repo_path, f"{base}^")
    rev_range = f"{parent}..{head}" if parent else head
    commits = list_commits(repo_path, limit=10_000, rev_range=rev_range)
    return {
        "files": files,
        "commits": commits,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }


def range_diff(repo_path: Path, base: str, head: str, path: str | None = None) -> str:
    diff_base, head_ref = _range_endpoints(repo_path, base, head)
    args = ["diff", f"{diff_base}..{head_ref}"]
    if path:
        args += ["--", path]
    result = _git(repo_path, *args)
    return result.stdout if result.returncode == 0 else ""


# --------------------------------------------------------------------------- #
# bundles - arbitrary cherry-picked set (patch union, not a single tree diff)
# --------------------------------------------------------------------------- #
def set_files(repo_path: Path, shas: list[str]) -> dict:
    """Union of the selected commits' files, summing +/- across commits."""
    agg: dict[str, dict] = {}
    commits = []
    for sha in shas:
        detail = commit_detail(repo_path, sha)
        if detail is None:
            continue
        commits.append(detail)
        for f in detail["files"]:
            cur = agg.setdefault(
                f["path"], {"additions": 0, "deletions": 0, "status": f["status"]}
            )
            cur["additions"] += f["additions"]
            cur["deletions"] += f["deletions"]
            cur["status"] = f["status"]
    files = [{"path": p, **vals} for p, vals in sorted(agg.items())]
    return {
        "files": files,
        "commits": commits,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }


def set_diff(repo_path: Path, shas: list[str], path: str | None = None) -> str:
    """Each selected commit's patch, concatenated and headed by `# <short> <subject>`."""
    parts = []
    for sha in shas:
        diff = commit_diff(repo_path, sha, path)
        if diff.strip():
            parts.append(f"# commit {_one_line(repo_path, sha)}\n{diff}")
    return "\n".join(parts)


def base_parent(repo_path: Path, sha: str) -> str | None:
    """Parent of `sha`, or None when `sha` is a root commit (no parent tree).
    Used as the architectural baseline for a contiguous-range bundle."""
    return _rev_parse(repo_path, f"{sha}^")


def order_endpoints(repo_path: Path, shas: list[str]) -> tuple[str | None, str | None]:
    """For an arbitrary set, the (diff-base, head) that captures its *net* effect:
    parent of the oldest selected commit (None if root) and the newest commit."""
    if not shas:
        return None, None
    result = _git(repo_path, "rev-list", "--no-walk=sorted", *shas)
    ordered = [s for s in result.stdout.split() if s]
    if not ordered:
        return None, None
    head, earliest = ordered[0], ordered[-1]  # sorted = newest first
    return _rev_parse(repo_path, f"{earliest}^"), head


# --------------------------------------------------------------------------- #
# session linkage (time window + file overlap)
# --------------------------------------------------------------------------- #
def _relativize(repo_path: Path, files_edited: list[str] | None) -> set[str]:
    """Edited absolute paths -> {repo-relative path, basename} set, lower-cased."""
    base = str(repo_path).replace("\\", "/").rstrip("/").lower()
    out: set[str] = set()
    for f in files_edited or []:
        fp = f.replace("\\", "/").lower()
        if fp.startswith(base + "/"):
            out.add(fp[len(base) + 1 :])
        out.add(fp.rsplit("/", 1)[-1])  # basename fallback
    return out


def commits_for_session(
    repo_path: Path,
    started_at: str | None,
    last_activity: str | None,
    files_edited: list[str] | None,
    grace_minutes: int = 30,
) -> list[dict]:
    """Commits belonging to a session: authored within the session's active
    window (+/- grace), each tagged `matched_by` with 'time' and, when it touches
    a file the session edited, 'files'."""
    start = _parse_iso(started_at)
    if start is None:
        return []
    last = _parse_iso(last_activity) or start
    grace = timedelta(minutes=grace_minutes)
    commits = list_commits(
        repo_path,
        limit=200,
        since=(start - grace).isoformat(),
        until=(last + grace).isoformat(),
    )
    edited = _relativize(repo_path, files_edited)
    for commit in commits:
        commit_paths: set[str] = set()
        for f in commit["files"]:
            p = f["path"].replace("\\", "/").lower()
            commit_paths.add(p)
            commit_paths.add(p.rsplit("/", 1)[-1])
        matched = ["time"]
        if edited & commit_paths:
            matched.append("files")
        commit["matched_by"] = matched
    return commits
