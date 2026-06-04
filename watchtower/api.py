"""Cerberus Watchtower API - serves the dashboard on 127.0.0.1:8765."""

import asyncio

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from . import config, drift, events
from .ai import narrator, summarizer
from .db import get_session_factory
from .scanners import dotnet_scanner, git_scanner, transcript_parser, vue_scanner

# last successful architecture scan per repo - feeds narrate without rescanning
_arch_cache: dict[str, dict] = {}

app = FastAPI(title="Cerberus Watchtower")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/repos")
async def list_repos() -> list[dict]:
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
    cwd_lower = cwd.lower().replace("/", "\\")
    for repo in config.registry():
        if cwd_lower.startswith(str(repo.path).lower()):
            return {"project": repo.name, "repo_id": repo.id}
    return {"project": cwd.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1], "repo_id": None}


@app.get("/api/activity")
async def activity() -> dict:
    sessions = await asyncio.to_thread(transcript_parser.recent_sessions)
    recent = events.read_recent_events(limit=100)

    # sessions whose last hook event is Stop are done; recent activity = live
    stopped = {
        e["session_id"]
        for e in recent
        if e.get("event") == "Stop" and e.get("session_id")
    }
    now = datetime.now(timezone.utc)
    for session in sessions:
        session.update(_project_label(session.get("cwd")))
        try:
            last = datetime.fromisoformat(session["last_activity"].replace("Z", "+00:00"))
            fresh = now - last < timedelta(minutes=5)
        except (ValueError, AttributeError, TypeError):
            fresh = False
        session["active"] = fresh and session["session_id"] not in stopped
    for event in recent:
        event.update(_project_label(event.get("cwd")))
    return {"sessions": sessions, "events": recent}


@app.get("/api/events/stream")
async def event_stream() -> StreamingResponse:
    return StreamingResponse(
        events.stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_ARCHITECTURE_SCANNERS = {
    "dotnet": dotnet_scanner.analyze,
    "vue": vue_scanner.analyze,
}


@app.get("/api/architecture/{repo_id}")
async def architecture(repo_id: str) -> dict:
    repo = config.repo_by_id(repo_id)
    if repo is None:
        raise HTTPException(404, f"unknown repo: {repo_id}")
    scanner = _ARCHITECTURE_SCANNERS.get(repo.stack)
    if scanner is None:
        raise HTTPException(501, f"architecture scanning not yet supported for stack: {repo.stack}")
    result = await asyncio.to_thread(scanner, repo.path)
    if result is None:
        raise HTTPException(404, f"no analyzable {repo.stack} project found in {repo.id}")
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


# --- SPA static hosting (production: serve frontend/dist if built) ---

if config.FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = config.FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(config.FRONTEND_DIST / "index.html")
