# Cerberus Watchtower

Eagle's-eye dashboard over Cerberus Labs / PayTable codebases:

1. **Review Queue** - uncommitted work across all registered repos, with on-demand AI
   change summaries (cached by diff hash, so summaries only regenerate when the
   pending changes actually change)
2. **Activity** - live agent sessions: Claude Code hooks stream events over SSE,
   transcript parsing backfills session history (tools used, files edited, prompts)
3. **Architecture** - dependency graphs and data-flow maps, re-derived per scan:
   - **.NET**: .sln/.csproj project graph (solution-folder layering, NuGet packages),
     MediatR handler index, endpoint → request → handler data flows, dispatch sites
   - **Vue**: npm deps, router routes → components, stores/views, API call sites
   - Interactive Cytoscape graph with test-project toggle and per-node detail panel
   - Focus mode (double-click a project), endpoint call-flow inspector (handler →
     services → implementations), AI project narratives, and drift detection
     (snapshots recorded per structural change; diffs shown as added/removed
     edges, endpoints, package bumps)

## Stack

- Backend: Python 3.12 + FastAPI + SQLAlchemy/SQLite, port **8765**
- Frontend: Vue 3 + Vite + Tailwind CSS v4
- AI summaries: `claude -p` headless (Claude Code CLI must be on PATH)
- Data dir: `~/.cerberus-watchtower/` (summary cache DB)

## Run

```powershell
# backend (serves frontend/dist too, if built)
.venv\Scripts\python.exe -m watchtower

# frontend dev mode (hot reload, proxies /api to :8765)
cd frontend; npm run dev

# frontend production build
cd frontend; npm run build
```

With `frontend/dist` built, the whole dashboard is just `python -m watchtower` →
http://127.0.0.1:8765

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

## Repo registry — `repos.toml`

```toml
[[repos]]
id = "paytable-dotnet"            # unique slug, used in API routes
name = "PayTable .NET"            # display name
path = 'C:\path\to\repo'          # absolute path (single-quoted TOML literal)
stack = "dotnet"                  # dotnet | vue | python | android | terraform | web | mixed
group = "paytable"                # dashboard section grouping
```

## API

- `GET /api/repos` - registry with exists/is_git checks
- `GET /api/review-queue` - repos with uncommitted changes (branch, ahead/behind, per-file diffstat, last commit)
- `GET /api/review-queue/{repo_id}/diff?path=...` - unified diff for one pending file
- `POST /api/review-queue/{repo_id}/summarize` - AI summary of pending diff (cached)
- `GET /api/activity` - recent agent sessions + events; `GET /api/events/stream` - SSE live feed
- `GET /api/architecture/{repo_id}` - architecture snapshot (dotnet + vue stacks; others 501)
- `GET /api/architecture/{repo_id}/drift` - diff vs previous snapshot
- `POST /api/architecture/{repo_id}/narrate/{node_id}` - AI project narrative (cached by structure)

## Claude Code hooks

`hooks/emit_event.py` powers the live feed - registered in `~/.claude/settings.json`
for SessionStart / PostToolUse (Edit|Write|NotebookEdit) / Stop / SubagentStop,
async exec-form, appends to `~/.cerberus-watchtower/events.jsonl`.
