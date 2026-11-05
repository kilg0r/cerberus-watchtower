# Cerberus Watchtower

Internal dashboard giving an eagle's-eye view over all Cerberus Labs / PayTable
repos: review queue of uncommitted work, live agent activity, architecture maps
with call-flow drill-down, drift detection, and AI narratives.

## Commands

```powershell
.venv\Scripts\python.exe -m watchtower          # run server -> http://127.0.0.1:8765
scripts\Start-Watchtower.ps1                    # idempotent launcher (-Stop | -NoBrowser); `watchtower` profile fn
.venv\Scripts\python.exe -m pytest tests/       # run tests (93 passing)
cd frontend; npm run dev                        # frontend dev mode (proxies /api to :8765)
cd frontend; npm run build                      # production build -> frontend/dist
```

## Architecture

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy/SQLite, port 8765
  - `watchtower/api.py` - all routes; serves `frontend/dist` as SPA when built
  - `watchtower/config.py` - repo registry loaded from `repos.toml` (15 repos)
  - `watchtower/scanners/` - `git_scanner` (review queue), `dotnet_scanner`
    (.sln/.csproj graph + MediatR flows + method-level call graph in
    `class_methods`), `vue_scanner` (component/store graph,
    routes, deps), `python_scanner` (module import graph, deps, endpoints,
    config inventory), `generic_scanner` (fallback inventory),
    `transcript_parser` (incremental `TranscriptState` per transcript file:
    session summary + resolved artifact tool calls)
  - `watchtower/sessions.py` - `SessionIndex`: one `TranscriptState` per
    main/subagent/workflow-agent transcript, advanced every 5s by a background
    loop started in the API lifespan; feeds Activity and the artifact index
  - `watchtower/artifacts.py` - artifact index (claude.ai artifacts via the
    built-in `Artifact` tool or claude-artifacts MCP, plus Claude Docs):
    normalize ops, idempotent ingest, local copies, sandboxed serving
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
  narratives, artifact index), `artifacts/<uuid>/current/` (local artifact
  copies) and `events.jsonl` (hook event log, append-only)

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

## Transcripts, activity and artifacts

- **No size caps on transcripts** - busy sessions pass 500 MB. Parsing is
  incremental (byte offset per file) and json.loads only runs on lines that
  pass byte pre-filters, which assume Claude Code's compact JSON
  (`"type":"user"`, no spaces) - test fixtures must use
  `json.dumps(..., separators=(",", ":"))`.
- **Skip `api-request-blob` records** - they replay whole requests (tool calls
  included); counting them doubles tool counts.
- **Subagent transcripts** live at `<session>/subagents/agent-*.jsonl` and
  `<session>/subagents/workflows/<run>/agent-*.jsonl`; both roll up to the
  parent session (`session_id_for`).
- **Session state**: the Notification hook fires for permission prompts AND a
  ~60s idle ping ("Claude is waiting for your input") after every turn - only
  the former is "waiting"; the idle ping means "done". A transcript written
  >30s after the last hook event means the session moved on (e.g. a permission
  was granted and a non-edit tool is running).
- **Artifact ids are uuids** - claude.ai's `/artifact/<short>` id is the uuid
  in base58 (`artifacts.canonical_id`). The built-in `Artifact` tool and the
  claude-artifacts MCP address the same objects, so both are kind `artifact`.
  Only results that published something are ops (reads/watch/list/open are
  not); ingestion is idempotent by `tool_use_id` and replays chronologically.
- **Local copies are captured at ingest time** - built-in publishes and
  `publish_files` point at local (usually scratchpad) files, copied when
  indexed; `publish_version` HTML and `edit_version` edits come from the
  transcript. `content_status`: ok | stale (an edit couldn't be replayed or a
  file was missing) | missing (source gone) | none (docs).
- **Serving local copies**: always under the CSP `sandbox` header (opaque
  origin - artifact scripts must not reach this API); HTML gets
  `STORAGE_SHIM` injected because sandboxed localStorage access throws and
  blanks many artifacts. Headless-Edge screenshots render sandboxed iframes
  blank (out-of-process) - use `--disable-site-isolation-trials` to verify.

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
  should report ~44 projects / ~263 handlers / ~431 endpoints / ~3.8k methods
  in ~8s, with a JSON payload around 1.5 MB.
