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
for its diff - in the file's own syntax colors (the VS Code Dark+ theme, on
the removed side too), with line numbers, the changed words marked inside
substituted lines, and a unified or side-by-side layout. One button generates
an AI summary of the
pending changes - intent, modules touched, risk flags - cached by diff hash so
it only regenerates when the changes change.

### Activity
Live feed of Claude Code agent activity on the machine, streamed over SSE from
lifecycle hooks (session start/stop, prompts, file edits, artifact publishes,
subagent completion). Session cards come from an incremental transcript index:
what each session worked on, which repos it edited (sessions launched from a
workspace root are labelled by where their edits landed), tool usage, subagent
count, the artifacts it published, and its live state - **working**, **needs
input** (a permission/approval prompt), **finished** (turn ended, including
the idle ping that follows), or idle. Transcripts of any size are indexed:
each file is read once, then only its appended tail.

### Processes
Every running Claude Code process as a container: what it is executing right
now. Each card shows the session's state (working, **needs you** - an open
question or permission prompt - shell, background, idle), its CPU trend and
memory, and one lane per agent: the main agent, running subagents and forks,
and workflow runs grouped by phase. Every in-flight tool call is listed with
its description, command and a ticking timer, with the OS processes it started
underneath (the shell, then `dotnet test` → vstest → testhost, and so on, each
with CPU and memory). Background tasks show the last lines they wrote, so a
long test run reports progress in place. Processes that outlived their call
are flagged as orphans. Everything is color-coded by kind of work - script,
build, test, shell, network, service, agent, tool - and the color key doubles
as a filter. Host RAM is shown and turns amber past 90%. "Pop out" opens the
view in its own window (`/processes?popout=1`, no sidebar).

Sources: Claude Code's own registry of running sessions
(`~/.claude/sessions/<pid>.json` - pid, session id, busy/idle status), the OS
process table under each Claude process (psutil), and the transcript index
(tool calls without a result yet, background launches until their completion
notice, subagent metadata). Polled every 3 seconds.

### Artifacts
Everything agent sessions publish - claude.ai artifacts (from Claude Code's
built-in `Artifact` tool or the claude-artifacts MCP) and Claude Docs - in one
searchable list, filterable by kind and project. Each artifact shows its
version history (label, time, and the session that made each change, linked
back to Activity), an "Open in Claude" link, and - where Watchtower captured
the content - a local copy previewed in a sandboxed frame. "Open in Chrome"
(named after the default browser) launches the page itself in the default
browser - the local HTML copy when there is one, otherwise the claude.ai page -
since links from the Edge app window would otherwise land in Edge. Pin
artifacts to keep them at the top of the list as a workdesk; pins keep their
order as artifacts get edited. The index and pins persist in SQLite, so they
outlive Claude Code's transcript cleanup. Artifacts published before that -
whose transcripts are already gone - can be added with "+ Link artifact"
(paste the claude.ai link; title and project optional). Linked artifacts have
no local copy; ask Claude to link them if you want the title, icon, sharing
and dates filled in from claude.ai.

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
- **3D map** (default; the 2D dagre graph is one click away and is the
  fallback without WebGL) - a three.js scene in the style of the
  cerberuslabs.tech portfolio sphere. **Layered** stacks dependency ranks as
  horizontal planes, with entry points on top and each dependency below
  everything that uses it. **Free** is a 3D force layout. Packets run along
  every line from a project to what it uses. Select a node and its dependencies
  light up coral, its dependents blue, while the camera flies to it. Click a
  solution folder in the legend (Infrastructure, Core, ...) to redraw the map
  around it: its projects in the middle, the libraries they link to grouped by
  folder around them (direct or all the way down, optionally with who uses
  it). Drag to orbit, right-drag to pan, scroll to zoom, arrow keys step
  through nodes.
- **Interactivity** - double-click any node to focus on its dependency
  neighborhood (depth-selectable, screen-wide). Below a focused .NET project,
  its code is listed as namespaces -> files -> types -> methods: click any of
  them to narrow the map to it - classes and what they call, inject and send,
  or a method's exact call path - with the scope full size, what it reaches
  and who calls it around it, grouped by project. Click an endpoint in the
  data flows to trace its request: route -> handler -> services ->
  implementations, layer by layer. "Inspect" opens the call-flow drawer to
  drill from controller to the services that do the work, including nested
  MediatR dispatches.
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
| Frontend | Vue 3, Vite, Tailwind CSS v4, three.js (3D architecture map), Cytoscape.js (2D dagre graph), Shiki (diff syntax colors) |
| AI | Claude Code CLI in headless mode (`claude -p`), hash-keyed caching |
| Agent feed | Claude Code hooks → append-only JSONL → SSE |

Static analysis is heuristic by design - regex and XML parsing, no compiler
services. The trade is deliberate: an always-current map beats a perfect map
that's expensive to rebuild. Against the primary .NET solution it traces 431
endpoints to handlers with one miss, in about six seconds.

## Quick start

Windows:

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

macOS / Linux:

```bash
# backend (3.12+)
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# frontend
(cd frontend && npm install && npm run build)

# run - serves the built frontend at http://127.0.0.1:8765
.venv/bin/python -m watchtower

# or use the launcher (idempotent: starts if needed, opens the dashboard)
scripts/watchtower.sh                 # also: --stop | --status | --no-browser

# optional: start at login + a `watchtower` shell function
scripts/install-macos.sh              # also: --no-agent | --uninstall
```

**Ports on macOS**: the system-wide socket table is root-only there, so
`system_ports` falls back to `lsof`, which reports the current user's
processes. Listeners owned by other users won't appear.

`repos.toml` carries the roots for **every** machine at once - a root that
doesn't exist on the box you're running is skipped, and overrides key on folder
name rather than path, so the same committed file drives the Windows desktop
and the Mac.

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
registered in `~/.claude/settings.json` for `SessionStart`, `UserPromptSubmit`,
`PostToolUse`, `Notification`, `Stop`, and `SubagentStop` - async exec-form,
so it adds zero latency to agent sessions. Events land in
`~/.cerberus-watchtower/events.jsonl`. The `PostToolUse` matcher covers edits
plus artifact publishing:

```
Edit|Write|NotebookEdit|Artifact|mcp__claude-artifacts__publish_version|mcp__claude-artifacts__publish_files|mcp__claude-artifacts__edit_version|mcp__claude-artifacts__create_from_type|mcp__claude_ai_Claude_Docs__batch|mcp__claude_ai_Claude_Docs__create|mcp__claude_ai_Claude_Docs__update
```

Hooks only drive the live feed and session states; session cards and the
artifact index come from transcripts and work without them.

## API

| Route | Purpose |
|---|---|
| `GET /api/repos?refresh=` | registry; `refresh=true` re-discovers under roots |
| `GET /api/review-queue` | repos with uncommitted changes |
| `GET /api/review-queue/{id}/diff?path=` | unified diff for one pending file |
| `POST /api/review-queue/{id}/summarize` | AI summary of pending diff (cached) |
| `GET /api/activity` | recent agent sessions (state, repos, artifacts) + events |
| `GET /api/events/stream` | SSE live event feed |
| `GET /api/artifacts` | every published artifact / doc - pinned first, then newest first - plus the default browser's name |
| `PUT` / `DELETE /api/artifacts/{id}/pin` | pin / unpin an artifact |
| `POST /api/artifacts/link` | index an artifact by its claude.ai link; JSON `{url, title?, favicon?, sharing?, version?, description?, repo_id? or cwd?}` |
| `POST /api/artifacts/{id}/open` | open it in the default browser; JSON body `{"target": "page" \| "claude"}` |
| `GET /api/artifacts/{id}` | one artifact + its version history |
| `GET /api/artifacts/{id}/content/{path}` | local copy, served under a CSP sandbox |
| `GET /api/processes` | every live Claude process: state, lanes (main/subagents/workflows) with in-flight calls and their OS process trees, background tasks with output tails, MCP servers, host CPU/RAM |
| `GET /api/ports` | live socket table with process identity |
| `GET /api/architecture-overview` | stats-level scan of every repo |
| `GET /api/architecture/{id}` | full architecture snapshot (any stack) |
| `GET /api/architecture/{id}/drift` | structural diff vs previous snapshot |
| `POST /api/architecture/{id}/narrate/{node}` | AI project narrative (cached) |

## Development

```powershell
.venv\Scripts\python.exe -m pytest tests/    # scanners run against synthetic fixtures
cd frontend; npm run dev                     # hot reload on :5173, /api proxied to :8765
```

```bash
.venv/bin/python -m pytest tests/            # same suite on macOS / Linux
(cd frontend && npm run dev)
```

Data lives in `~/.cerberus-watchtower/` (summary/narrative caches, architecture
snapshots, artifact index + local copies under `artifacts/`, event log).
Deleting it resets everything; the artifact index rebuilds from whatever
transcripts still exist, but artifacts whose transcripts were already cleaned
up are gone for good.
