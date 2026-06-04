# Cerberus Watchtower

An eagle's-eye dashboard over a multi-repo codebase. Watchtower auto-discovers
the repositories under your project roots and answers the questions that get
harder as a platform grows: what's changing right now, what's waiting for
review, what does the architecture actually look like today, and what's live
on this machine.

Built to watch the Cerberus Labs / PayTable portfolio - a 44-project .NET
solution, several Vue apps, Python services, and infrastructure repos - from a
single screen, sized to whatever viewport it's given.

## Views

### Review Queue
Uncommitted work across every registered repo, refreshed every 30 seconds:
branch, ahead/behind upstream, per-file diffstat, last commit. Click any file
for its colorized unified diff. One button generates an AI summary of the
pending changes - intent, modules touched, risk flags - cached by diff hash so
it only regenerates when the changes change.

### Activity
Live feed of Claude Code agent activity on the machine, streamed over SSE from
lifecycle hooks (session start/stop, file edits, subagent completion). Session
cards backfilled from transcript parsing: what each session worked on, which
files it edited, tool usage, the prompt that started it, and whether it's
active right now.

### Architecture
Re-derived from source on every scan, so the map cannot go stale. Every stack
gets a view relevant to its language:

- **.NET solutions** - project dependency graph from `.sln`/`.csproj`
  (solution-folder layering, NuGet packages), a MediatR handler index, and full
  data flows: endpoint → request → handler → services → implementations.
  Handles both constructor injection and service-locator/base-class field
  patterns.
- **Python apps** - internal-module import graph, declared dependencies,
  FastAPI/Flask endpoints, entry points, and the config-file inventory that is
  the real architecture of config-driven apps.
- **Vue apps** - view/component/store import graph, routes, npm dependencies,
  API call sites.
- **Everything else** (config/terraform/android/web/mixed) - structural
  inventory: language breakdown, directory map, docs index. No stack 501s.
- **Portfolio overview** - "All repos" scans the entire registry concurrently
  (~12s for 15 repos) into per-repo stat cards with drift badges.
- **Interactivity** - double-click any node to focus on its dependency
  neighborhood (depth-selectable, screen-wide). Click any .NET endpoint to open
  the call-flow inspector and drill from controller to the services that do the
  work, including nested MediatR dispatches.
- **Drift detection** - every structural change records a snapshot; the diff
  between the last two (new/removed dependency edges, endpoints, modules,
  routes, package bumps) surfaces as a "changes detected" chip. The day a
  module grows an edge it shouldn't have, you see it.
- **AI narratives** - per-project explanations ("what is this module
  responsible for, and why is it shaped this way") generated from structural
  facts and cached until the structure changes.

### Ports
Live OS socket visibility, polled every 10 seconds: TCP listeners and UDP
bound sockets with full process identity (name, pid, command line, uptime,
memory), LAN-exposed vs local-only bind classification, known-service labels,
and established connections grouped by process and remote host with
external/LAN flags.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy + SQLite, port 8765 |
| Frontend | Vue 3, Vite, Tailwind CSS v4, Cytoscape.js (dagre layout) |
| AI | Claude Code CLI in headless mode (`claude -p`), hash-keyed caching |
| Agent feed | Claude Code hooks → append-only JSONL → SSE |

Static analysis is heuristic by design - regex and XML parsing, no compiler
services. The trade is deliberate: an always-current map beats a perfect map
that's expensive to rebuild. Against the primary .NET solution it traces 431
endpoints to handlers with one miss, in about six seconds.

## Quick start

```powershell
# backend
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# frontend
cd frontend
npm install
npm run build
cd ..

# run - serves the built frontend at http://127.0.0.1:8765
.venv\Scripts\python.exe -m watchtower

# or use the launcher (idempotent: starts if needed, opens the dashboard)
scripts\Start-Watchtower.ps1          # also: -Stop | -NoBrowser
```

Repos are **auto-discovered**: point `repos.toml` at your project roots and
every child git repository registers itself, with its stack inferred from
contents (`.sln` → dotnet, `package.json`+vue → vue, `pyproject`/`requirements`
→ python, `.tf` → terraform, gradle → android). "Refresh list" in the
Architecture view re-discovers without a restart.

```toml
[[roots]]
path = 'C:\path\to\projects'     # children with .git are picked up automatically
group = "myteam"                 # dashboard section grouping

[overrides.myRepoFolder]         # optional per-folder customization
id = "my-api"                    # keep stable - caches/snapshots key on it
name = "My API"
stack = "dotnet"
include = true                   # register a non-git project directory
```

### Agent feed hooks (optional)

`hooks/emit_event.py` is a stdlib-only, append-and-exit emitter designed to be
registered in `~/.claude/settings.json` for `SessionStart`, `PostToolUse`
(matcher `Edit|Write|NotebookEdit`), `Stop`, and `SubagentStop` - async
exec-form, so it adds zero latency to agent sessions. Events land in
`~/.cerberus-watchtower/events.jsonl`.

## API

| Route | Purpose |
|---|---|
| `GET /api/repos?refresh=` | registry; `refresh=true` re-discovers under roots |
| `GET /api/review-queue` | repos with uncommitted changes |
| `GET /api/review-queue/{id}/diff?path=` | unified diff for one pending file |
| `POST /api/review-queue/{id}/summarize` | AI summary of pending diff (cached) |
| `GET /api/activity` | recent agent sessions + events |
| `GET /api/events/stream` | SSE live event feed |
| `GET /api/ports` | live socket table with process identity |
| `GET /api/architecture-overview` | stats-level scan of every repo |
| `GET /api/architecture/{id}` | full architecture snapshot (any stack) |
| `GET /api/architecture/{id}/drift` | structural diff vs previous snapshot |
| `POST /api/architecture/{id}/narrate/{node}` | AI project narrative (cached) |

## Development

```powershell
.venv\Scripts\python.exe -m pytest tests/    # 37 tests; scanners run against synthetic fixtures
cd frontend; npm run dev                     # hot reload on :5173, /api proxied to :8765
```

Data lives in `~/.cerberus-watchtower/` (summary/narrative caches, architecture
snapshots, event log). Delete it to reset - everything regenerates.
