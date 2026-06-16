"""Cerberus Watchtower API - serves the dashboard on 127.0.0.1:8765."""

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from . import config, drift, events, notifier, system_ports
from .ai import narrator, summarizer
from .db import get_session_factory
from .scanners import git_scanner, scan_architecture, transcript_parser
from .scanners import arch_at_commit, git_history

# last successful architecture scan per repo - feeds narrate without rescanning
_arch_cache: dict[str, dict] = {}

@asynccontextmanager
async def _lifespan(app: FastAPI):
    notify_task = asyncio.create_task(notifier.run())
    yield
    notify_task.cancel()


app = FastAPI(title="Cerberus Watchtower", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/repos")
async def list_repos(refresh: bool = False) -> list[dict]:
    if refresh:
        config.refresh_registry()
    return [
        {
            "id": repo.id,
            "name": repo.name,
            "path": str(repo.path),
            "stack": repo.stack,
            "group": repo.group,
            "exists": repo.path.is_dir(),
            "is_git": (repo.path / ".git").exists(),
        }
        for repo in config.registry()
    ]


@app.get("/api/review-queue")
async def review_queue() -> list[dict]:
    return await asyncio.to_thread(git_scanner.scan_all, list(config.registry()))


@app.get("/api/review-queue/{repo_id}/diff")
async def file_diff(repo_id: str, path: str) -> dict:
    repo = config.repo_by_id(repo_id)
    if repo is None:
        raise HTTPException(404, f"unknown repo: {repo_id}")
    diff = await asyncio.to_thread(git_scanner.file_diff, repo.path, path)
    if diff is None:
        raise HTTPException(404, f"no pending diff for: {path}")
    return {"path": path, "diff": diff}


@app.post("/api/review-queue/{repo_id}/summarize")
async def summarize(repo_id: str) -> dict:
    repo = config.repo_by_id(repo_id)
    if repo is None:
        raise HTTPException(404, f"unknown repo: {repo_id}")
    diff = await asyncio.to_thread(summarizer.collect_diff, repo.path)
    if not diff.strip():
        raise HTTPException(404, "no pending changes to summarize")

    def _summarize() -> dict:
        with get_session_factory()() as session:
            return summarizer.get_or_create_summary(session, repo_id, diff)

    try:
        return await asyncio.to_thread(_summarize)
    except summarizer.SummarizerError as exc:
        raise HTTPException(502, str(exc)) from exc


def _project_label(cwd: str | None) -> dict:
    """Map a session cwd onto the repo registry, falling back to the dir name."""
    if not cwd:
        return {"project": "unknown", "repo_id": None}
    cwd_lower = cwd.lower().replace("/", "\\").rstrip("\\")
    # exact match or boundary-aware prefix (cwd inside the repo); deepest path
    # wins so sibling repos sharing a name prefix can't shadow each other
    best = None
    for repo in config.registry():
        repo_path = str(repo.path).lower().rstrip("\\")
        if cwd_lower == repo_path or cwd_lower.startswith(repo_path + "\\"):
            if best is None or len(repo_path) > len(str(best.path).rstrip("\\")):
                best = repo
    if best is not None:
        return {"project": best.name, "repo_id": best.id}
    return {"project": cwd.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1], "repo_id": None}


# A session's live state is inferred from its most recent hook event:
#   Notification -> blocked on a permission/input prompt  ("waiting")
#   Stop         -> turn finished, awaiting the next prompt ("done")
#   tool/prompt  -> mid-flight                              ("working")
# "waiting"/"done" are the states that need the user's attention, so they stay
# relevant for a while; "working" requires very recent activity to count as live.
_LIVE_WINDOW = timedelta(minutes=5)        # working pulse
_WAITING_WINDOW = timedelta(hours=8)       # blocked sessions matter for a long time
_DONE_WINDOW = timedelta(hours=2)          # finished-but-recent, awaiting your input
_WORKING_EVENTS = {"UserPromptSubmit", "PostToolUse", "SubagentStop", "SessionStart"}


@app.get("/api/activity")
async def activity() -> dict:
    sessions = await asyncio.to_thread(transcript_parser.recent_sessions)
    recent = events.read_recent_events(limit=100)

    # recent is newest-first, so the first event seen per session is its latest
    latest_event: dict[str, str] = {}
    for e in recent:
        sid = e.get("session_id")
        if sid and sid not in latest_event:
            latest_event[sid] = e.get("event")
    now = datetime.now(timezone.utc)
    for session in sessions:
        session.update(_project_label(session.get("cwd")))
        try:
            last = datetime.fromisoformat(session["last_activity"].replace("Z", "+00:00"))
            age = now - last
        except (ValueError, AttributeError, TypeError):
            age = None
        name = latest_event.get(session["session_id"])
        if age is None:
            state = "idle"
        elif name == "Notification" and age < _WAITING_WINDOW:
            state = "waiting"
        elif name == "Stop" and age < _DONE_WINDOW:
            state = "done"
        elif name in _WORKING_EVENTS and age < _LIVE_WINDOW:
            state = "working"
        else:
            state = "idle"
        session["state"] = state
        session["active"] = state == "working"  # back-compat alias
    for event in recent:
        event.update(_project_label(event.get("cwd")))
    return {"sessions": sessions, "events": recent}


@app.post("/api/activity/{session_id}/summarize")
async def summarize_session(session_id: str) -> dict:
    path = transcript_parser.find_transcript(session_id)
    if path is None:
        raise HTTPException(404, f"unknown session: {session_id}")
    conversation = await asyncio.to_thread(transcript_parser.extract_conversation, path)
    if not conversation.strip():
        raise HTTPException(404, "no conversation content in this session")

    def _summarize() -> dict:
        with get_session_factory()() as session:
            return summarizer.get_or_create_session_summary(session, session_id, conversation)

    try:
        return await asyncio.to_thread(_summarize)
    except summarizer.SummarizerError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/activity/{session_id}/commits")
async def session_commits(session_id: str) -> dict:
    """Commits attributable to a session - authored inside its active window,
    flagged 'files' when they also touch a file the session edited."""
    path = transcript_parser.find_transcript(session_id)
    if path is None:
        raise HTTPException(404, f"unknown session: {session_id}")
    info = await asyncio.to_thread(transcript_parser.parse_transcript, path)
    if info is None:
        raise HTTPException(404, "could not parse session transcript")
    label = _project_label(info.get("cwd"))
    repo = config.repo_by_id(label["repo_id"]) if label.get("repo_id") else None
    if repo is None:
        return {
            "repo_id": None,
            "project": label.get("project"),
            "commits": [],
            "reason": "session working directory is not a registered repo",
        }
    commits = await asyncio.to_thread(
        git_history.commits_for_session,
        repo.path,
        info.get("started_at"),
        info.get("last_activity"),
        info.get("files_edited"),
    )
    return {"repo_id": repo.id, "project": repo.name, "commits": commits}


@app.get("/api/ports")
async def ports() -> dict:
    return await asyncio.to_thread(system_ports.snapshot)


@app.get("/api/events/stream")
async def event_stream() -> StreamingResponse:
    return StreamingResponse(
        events.stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/architecture-overview")
async def architecture_overview() -> list[dict]:
    """Stats-level scan of every registered repo, concurrently."""
    repos = [r for r in config.registry() if r.path.is_dir()]
    results = await asyncio.gather(
        *(asyncio.to_thread(scan_architecture, repo) for repo in repos)
    )
    overview = []
    with get_session_factory()() as session:
        for repo, result in zip(repos, results):
            if result is None:
                continue
            _arch_cache[repo.id] = result
            drift.record_snapshot(session, repo.id, result)
            overview.append(
                {
                    "repo_id": repo.id,
                    "name": repo.name,
                    "group": repo.group,
                    "stack": result["stack"],
                    "stats": result["stats"],
                    "scanned_at": result["scanned_at"],
                }
            )
    return overview


@app.get("/api/architecture/{repo_id}")
async def architecture(repo_id: str) -> dict:
    repo = config.repo_by_id(repo_id)
    if repo is None:
        raise HTTPException(404, f"unknown repo: {repo_id}")
    result = await asyncio.to_thread(scan_architecture, repo)
    if result is None:
        raise HTTPException(404, f"nothing analyzable found in {repo.id}")
    _arch_cache[repo.id] = result

    def _record() -> None:
        with get_session_factory()() as session:
            drift.record_snapshot(session, repo.id, result)

    await asyncio.to_thread(_record)
    return {"repo_id": repo.id, "name": repo.name, **result}


@app.get("/api/architecture/{repo_id}/drift")
async def architecture_drift(repo_id: str) -> dict:
    if config.repo_by_id(repo_id) is None:
        raise HTTPException(404, f"unknown repo: {repo_id}")

    def _drift() -> dict:
        with get_session_factory()() as session:
            return drift.compute_drift(session, repo_id)

    return await asyncio.to_thread(_drift)


@app.post("/api/architecture/{repo_id}/narrate/{node_id}")
async def architecture_narrate(repo_id: str, node_id: str) -> dict:
    arch = _arch_cache.get(repo_id)
    if arch is None:
        raise HTTPException(409, "scan the repo first - architecture not in memory")
    signature = narrator.project_signature(arch, node_id)
    if signature is None:
        raise HTTPException(404, f"unknown project node: {node_id}")

    def _narrate() -> dict:
        with get_session_factory()() as session:
            return narrator.get_or_create_narrative(session, repo_id, node_id, signature)

    try:
        return await asyncio.to_thread(_narrate)
    except summarizer.SummarizerError as exc:
        raise HTTPException(502, str(exc)) from exc


# ============================== git history ==============================
# Universal/per-repo commit history, single-commit detail, and "bundle" review
# (a contiguous range or an arbitrary cherry-picked set) with a combined diff
# and an architectural delta drilled the same way as the Architecture view.

_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


def _require_repo(repo_id: str) -> config.RepoConfig:
    repo = config.repo_by_id(repo_id)
    if repo is None:
        raise HTTPException(404, f"unknown repo: {repo_id}")
    return repo


def _validate_sha(sha: str) -> str:
    if not _SHA_RE.match(sha or ""):
        raise HTTPException(400, f"invalid commit sha: {sha}")
    return sha


def _parse_shas(shas: str | None) -> list[str]:
    out = [s for s in (shas or "").split(",") if s]
    if not out:
        raise HTTPException(400, "no commits specified")
    for sha in out:
        _validate_sha(sha)
    return out


def _bundle_endpoints(
    mode: str, base: str | None, head: str | None, shas: str | None
) -> tuple[str | None, str | None, list[str] | None]:
    """Validate a bundle spec and return (base, head, sha_list). For 'set',
    base/head are None (resolved later via git); for 'range', sha_list is None."""
    if mode == "range":
        if not base or not head:
            raise HTTPException(400, "a range bundle needs base and head")
        return _validate_sha(base), _validate_sha(head), None
    if mode == "set":
        return None, None, _parse_shas(shas)
    raise HTTPException(400, f"unknown bundle mode: {mode}")


@app.get("/api/git/history")
async def git_history_feed(limit: int = 50) -> list[dict]:
    repos = [r for r in config.registry() if r.path.is_dir()]
    return await asyncio.to_thread(git_history.history_all, repos, limit)


@app.get("/api/git/{repo_id}/commits")
async def git_commits(
    repo_id: str,
    limit: int = 50,
    skip: int = 0,
    since: str | None = None,
    author: str | None = None,
    path: str | None = None,
) -> list[dict]:
    repo = _require_repo(repo_id)
    paths = [path] if path else None
    return await asyncio.to_thread(
        lambda: git_history.list_commits(
            repo.path, limit=limit, skip=skip, since=since, author=author, paths=paths
        )
    )


@app.get("/api/git/{repo_id}/commit/{sha}")
async def git_commit_detail(repo_id: str, sha: str) -> dict:
    repo = _require_repo(repo_id)
    _validate_sha(sha)
    detail = await asyncio.to_thread(git_history.commit_detail, repo.path, sha)
    if detail is None:
        raise HTTPException(404, f"unknown commit: {sha}")
    return detail


@app.get("/api/git/{repo_id}/commit/{sha}/diff")
async def git_commit_diff(repo_id: str, sha: str, path: str | None = None) -> dict:
    repo = _require_repo(repo_id)
    _validate_sha(sha)
    diff = await asyncio.to_thread(git_history.commit_diff, repo.path, sha, path)
    return {"diff": diff}


@app.get("/api/git/{repo_id}/bundle")
async def git_bundle(
    repo_id: str,
    mode: str = "range",
    base: str | None = None,
    head: str | None = None,
    shas: str | None = None,
) -> dict:
    repo = _require_repo(repo_id)
    base, head, sha_list = _bundle_endpoints(mode, base, head, shas)
    if mode == "range":
        result = await asyncio.to_thread(git_history.range_files, repo.path, base, head)
    else:
        result = await asyncio.to_thread(git_history.set_files, repo.path, sha_list)
    return {"mode": mode, **result}


@app.get("/api/git/{repo_id}/bundle/diff")
async def git_bundle_diff(
    repo_id: str,
    mode: str = "range",
    base: str | None = None,
    head: str | None = None,
    shas: str | None = None,
    path: str | None = None,
) -> dict:
    repo = _require_repo(repo_id)
    base, head, sha_list = _bundle_endpoints(mode, base, head, shas)
    if mode == "range":
        diff = await asyncio.to_thread(git_history.range_diff, repo.path, base, head, path)
    else:
        diff = await asyncio.to_thread(git_history.set_diff, repo.path, sha_list, path)
    return {"diff": diff}


@app.get("/api/git/{repo_id}/bundle/arch-delta")
async def git_bundle_arch_delta(
    repo_id: str,
    mode: str = "range",
    base: str | None = None,
    head: str | None = None,
    shas: str | None = None,
) -> dict:
    """Structural delta across the bundle: scan the architecture at the bundle's
    base parent and at its head, then diff. `head_arch` is the full payload so the
    frontend can drill the call graph exactly like the live Architecture view."""
    repo = _require_repo(repo_id)
    base, head, sha_list = _bundle_endpoints(mode, base, head, shas)

    def _compute() -> dict:
        if mode == "range":
            base_parent, head_sha = git_history.base_parent(repo.path, base), head
        else:
            base_parent, head_sha = git_history.order_endpoints(repo.path, sha_list)
        if head_sha is None:
            return {"changes": None, "head_arch": None, "base_available": False}
        new_arch = arch_at_commit.arch_at_commit(repo, head_sha)
        if new_arch is None:
            # tree exists but isn't analyzable for this stack - no graph to drill
            return {"changes": None, "head_arch": None, "base_available": False, "unsupported": True}
        old_arch = arch_at_commit.arch_at_commit(repo, base_parent) if base_parent else None
        return {
            "changes": drift.diff_arch(old_arch, new_arch),
            "head_arch": new_arch,
            "base_available": old_arch is not None,
        }

    return await asyncio.to_thread(_compute)


@app.post("/api/git/{repo_id}/bundle/summarize")
async def git_bundle_summarize(
    repo_id: str,
    mode: str = "range",
    base: str | None = None,
    head: str | None = None,
    shas: str | None = None,
) -> dict:
    repo = _require_repo(repo_id)
    base, head, sha_list = _bundle_endpoints(mode, base, head, shas)
    if mode == "range":
        diff = await asyncio.to_thread(git_history.range_diff, repo.path, base, head)
    else:
        diff = await asyncio.to_thread(git_history.set_diff, repo.path, sha_list)
    if not diff.strip():
        raise HTTPException(404, "no changes to summarize")
    encoded = diff.encode("utf-8")
    if len(encoded) > summarizer.MAX_DIFF_BYTES:
        diff = encoded[: summarizer.MAX_DIFF_BYTES].decode("utf-8", errors="replace")
        diff += "\n\n[diff truncated - summarize what is visible]"

    def _summarize() -> dict:
        with get_session_factory()() as session:
            return summarizer.get_or_create_summary(session, repo_id, diff)

    try:
        return await asyncio.to_thread(_summarize)
    except summarizer.SummarizerError as exc:
        raise HTTPException(502, str(exc)) from exc


# --- SPA static hosting (production: serve frontend/dist if built) ---

if config.FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = config.FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(config.FRONTEND_DIST / "index.html")
