# Cerberus Watchtower

Internal dashboard giving an eagle's-eye view over all Cerberus Labs / PayTable
repos: review queue of uncommitted work, live agent activity, architecture maps
with call-flow drill-down, drift detection, and AI narratives.

## Commands

```powershell
.venv\Scripts\python.exe -m watchtower          # run server -> http://127.0.0.1:8765
scripts\Start-Watchtower.ps1                    # idempotent launcher (-Stop | -NoBrowser); `watchtower` profile fn
.venv\Scripts\python.exe -m pytest tests/       # run tests (37 passing)
cd frontend; npm run dev                        # frontend dev mode (proxies /api to :8765)
cd frontend; npm run build                      # production build -> frontend/dist
```

## Architecture

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy/SQLite, port 8765
  - `watchtower/api.py` - all routes; serves `frontend/dist` as SPA when built
  - `watchtower/config.py` - repo registry loaded from `repos.toml` (15 repos)
  - `watchtower/scanners/` - `git_scanner` (review queue), `dotnet_scanner`
    (.sln/.csproj graph + MediatR flows), `vue_scanner` (component/store graph,
    routes, deps), `python_scanner` (module import graph, deps, endpoints,
    config inventory), `generic_scanner` (fallback inventory),
    `transcript_parser` (Claude Code session history)
  - `watchtower/system_ports.py` - live OS socket table via psutil (Ports tab)
  - `watchtower/ai/` - `summarizer` (diff summaries), `narrator` (project
    narratives); both shell out to `claude -p` headless
  - `watchtower/drift.py` - architecture snapshots + structural diffing
  - `watchtower/events.py` - hook event log tail + SSE streaming
  - `hooks/emit_event.py` - Claude Code hook emitter (registered in
    `~/.claude/settings.json`)
- **Frontend**: Vue 3 (`<script setup>`) + Vite + Tailwind v4 + vue-router +
  Cytoscape (lazy-loaded). No Pinia - composables only.
- **Data dir**: `~/.cerberus-watchtower/` - `watchtower.db` (caches, snapshots,
  narratives) and `events.jsonl` (hook event log, append-only)

## Critical conventions

- **Restart required after backend changes** - uvicorn runs without reload.
  Frontend changes need `npm run build` before they appear at :8765 (dev mode
  on :5173 hot-reloads).
- **Scanners are heuristic by design** - regex/XML parsing, no Roslyn/compiler.
  The goal is an always-current map, not compiler-grade accuracy. Never let a
  scanner raise out of `scan_repo` (return None instead).
- **PayTable handler pattern**: handlers extend a `ServiceInjection` base class
  (IServiceProvider ctor) and access services via `_xService` fields. Constructor
  parsing alone sees nothing - that's why `dotnet_scanner` resolves field usages
  through the inheritance chain (`class_service_uses`).
- **All git access is read-only** - `git -C <path>` subprocess with explicit
  args, never `shell=True`, never mutating commands inside scanners.
- **AI caching discipline**: summaries keyed by diff sha256, narratives by
  structure hash. Never cache failures. `claude` CLI must be on PATH.
- **Snapshots are a change log, not a scan log** - `drift.record_snapshot` only
  inserts when the structural signature changes.
- **`hooks/emit_event.py` must stay stdlib-only and never fail** - it runs on
  every tool use in every Claude Code session on this machine. Append and exit 0.
- **API contract is fixed** - the frontend is built against the exact response
  shapes in `api.py`; change both sides together.

## Registry (`repos.toml`)

Auto-discovery: `[[roots]]` entries are scanned for child git repos with stack
inference; `[overrides.<folder>]` customizes id/name/stack/group or includes a
non-git dir (`include = true`). **Keep override ids stable** - caches and
snapshots are keyed by repo id. `/api/repos?refresh=true` (or the "Refresh
list" button) re-discovers without a restart.

Architecture scanners by stack: dotnet, vue, python get dedicated scanners
with graphs; every other stack falls back to `generic_scanner` (languages /
directories / docs inventory) - nothing 501s.

## Testing

- Scanner tests use synthetic fixtures (temp git repos, hand-written .sln/.csproj/
  .cs content in `tests/test_dotnet_scanner.py`) - never depend on real repos.
- AI calls are always monkeypatched in tests (`run_claude`).
- After scanner changes, sanity-check against the real thing: payTableDotnet
  should report ~44 projects / ~263 handlers / ~431 endpoints in ~6s.
