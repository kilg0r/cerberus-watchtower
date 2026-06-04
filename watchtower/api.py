"""Cerberus Watchtower API - serves the dashboard on 127.0.0.1:8765."""

import asyncio

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from . import config, drift, events, system_ports
from .ai import narrator, summarizer
from .db import get_session_factory
from .scanners import (
    dotnet_scanner,
    generic_scanner,
    git_scanner,
    python_scanner,
    transcript_parser,
    vue_scanner,
)

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


_ARCHITECTURE_SCANNERS = {
    "dotnet": dotnet_scanner.analyze,
    "vue": vue_scanner.analyze,
    "python": python_scanner.analyze,
}


def _scan_architecture(repo: config.RepoConfig) -> dict | None:
    scanner = _ARCHITECTURE_SCANNERS.get(repo.stack)
    result = scanner(repo.path) if scanner else None
    if result is None:  # no dedicated scanner, or it found nothing analyzable
        result = generic_scanner.analyze(repo.path, stack=repo.stack)
    return result


@app.get("/api/architecture-overview")
async def architecture_overview() -> list[dict]:
    """Stats-level scan of every registered repo, concurrently."""
    repos = [r for r in config.registry() if r.path.is_dir()]
    results = await asyncio.gather(
        *(asyncio.to_thread(_scan_architecture, repo) for repo in repos)
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
    result = await asyncio.to_thread(_scan_architecture, repo)
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


# --- SPA static hosting (production: serve frontend/dist if built) ---

if config.FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = config.FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(config.FRONTEND_DIST / "index.html")
