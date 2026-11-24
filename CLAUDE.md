# Cerberus Watchtower

Internal dashboard giving an eagle's-eye view over all Cerberus Labs / PayTable
repos: review queue of uncommitted work, live agent activity, architecture maps
with call-flow drill-down, drift detection, and AI narratives.

## Commands

Windows:

```powershell
.venv\Scripts\python.exe -m watchtower          # run server -> http://127.0.0.1:8765
scripts\Start-Watchtower.ps1                    # idempotent launcher (-Stop | -NoBrowser); `watchtower` profile fn
.venv\Scripts\python.exe -m pytest tests/       # run tests (148 passing)
cd frontend; npm run dev                        # frontend dev mode (proxies /api to :8765)
cd frontend; npm run build                      # production build -> frontend/dist
scripts\Enable-LanAccess.ps1 [-Disable]         # (elevated) LAN + Tailscale access on :8766
```

**LAN / iPhone access** (Windows): the server stays on 127.0.0.1:8765. A
`netsh interface portproxy` mapping (0.0.0.0:8766 -> 127.0.0.1:8765, lives in
the IP Helper service, survives reboots) plus the firewall rule "Cerberus
Watchtower (TCP 8766, LAN + Tailscale)" (RemoteAddress LocalSubnet +
100.64.0.0/10, Profile Any - the Wi-Fi is classified Public) expose it at
`http://192.168.4.22:8766` and `http://m0thership:8766` on the tailnet. There
is no login, so keep that address scope. Same-origin POSTs from the phone pass
the JSON-body CSRF guard; "Open in Chrome" opens on the PC, so the Artifacts
view hides it unless the page was loaded from localhost (`onThisMachine`).

**Phone / iPad layout.** The bottom tab bar replaces the sidebar (App.vue,
safe-area padding, `h-dvh` because iOS Safari's `vh` includes the area under
its toolbar) on phones and on touch-first tablets at any width: the `tabnav:`
variant in `style.css` is `(max-width: 47.99rem), (pointer: coarse)`, so an
iPad keeps two panes side by side but loses the sidebar. Anything fixed to the
bottom must clear the bar under `tabnav:`. The rest of the phone layout is
below Tailwind `md`, 48rem (`composables/useNarrow.js`). `SplitPane` shows one
pane at a time with a two-way toggle named by `labels`; `v-model:pane` lets a
view jump panes (Artifacts/History open the detail on select, Architecture
returns to the map when a list sets a scope). Panes use `v-show`, so each keeps
its state. Elsewhere it's `max-md:` utilities: grids use
`minmax(min(Npx,100%),1fr)`, headers wrap, view subtitles hide (not
Architecture's - it is the breadcrumb), the 3D legend and the Architecture
stats row become one swipeable line, the sessions table drops to four columns.
On a phone, picking an artifact opens it full screen (Teleported overlay over
the tab bar; the Full screen button elsewhere, esc exits). Check at 390x844
with Playwright (`is_mobile=True, has_touch=True` - that is what makes Chromium
match `pointer: coarse`), at an iPad size (834x1194 and 1194x834, touch), and at
1920x1080 for regressions, since desktop must stay as it was.

**History's diff reader** (`components/FileReader.vue`, commits and bundles):
the files in a rail grouped by folder, one file's diff filling the height
(`DiffView fill` - no 70vh box inside a scrolling list), j / k between files,
diffs cached with the next one prefetched; under ~640px of its own width the
rail turns into a picker. The expand button (esc back) sets `SplitPane
maximized`, hiding the commit list and page header without remounting, and
folds the commit message just for reading.

macOS:

```bash
.venv/bin/python -m watchtower                  # run server -> http://127.0.0.1:8765
scripts/watchtower.sh                           # idempotent launcher (--stop | --status | --no-browser); `watchtower` zsh fn
scripts/install-macos.sh                        # LaunchAgent (start at login) + shell fn (--uninstall)
.venv/bin/python -m pytest tests/               # run tests (148 passing)
(cd frontend && npm run build)                  # production build -> frontend/dist
```

The Mac runs the server under a LaunchAgent (`tech.cerberuslabs.watchtower`),
so **restarting after a backend change means `launchctl kickstart -k
gui/$UID/tech.cerberuslabs.watchtower`**, or `watchtower --stop` followed by
`watchtower`. Logs land in `~/Library/Logs/CerberusWatchtower/`.

## Architecture

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy/SQLite, port 8765
  - `watchtower/api.py` - all routes; serves `frontend/dist` as SPA when built
  - `watchtower/config.py` - repo registry loaded from `repos.toml`; roots for
    every machine live there and non-existent ones are skipped (19 repos on the
    Mac, 15 on the Windows desktop)
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
  - `watchtower/browser.py` - default-browser name (registry) + `open_url`
    (ShellExecute) for the Artifacts "Open in Chrome" action
  - `watchtower/system_ports.py` - live OS socket table via psutil, falling back
    to parsing `lsof` on macOS, where the system-wide table is root-only (Ports tab)
  - `watchtower/live_processes.py` - Processes tab: joins Claude Code's
    running-session registry (`~/.claude/sessions/<pid>.json`), the OS
    process trees under each claude pid, and the transcript index's in-flight
    calls / background tasks / subagents into one container per process
  - `watchtower/process_history.py` - Processes tab history: resource samples
    recorded per snapshot (trend charts, card sparklines) and a per-session
    activity timeline binned from hook events + recorded states
  - `watchtower/ai/` - `summarizer` (diff summaries), `narrator` (project
    narratives); both shell out to `claude -p` headless
  - `watchtower/drift.py` - architecture snapshots + structural diffing
  - `watchtower/events.py` - hook event log tail + SSE streaming
  - `hooks/emit_event.py` - Claude Code hook emitter (registered in
    `~/.claude/settings.json`)
- **Frontend**: Vue 3 (`<script setup>`) + Vite + Tailwind v4 + vue-router +
  three.js (3D architecture map, `utils/archScene.js`, lazy chunk) + Cytoscape
  (2D graph, lazy-loaded) + Shiki (diff syntax colors, `utils/highlighter.js`,
  core and each grammar lazy). No Pinia - composables only. `?popout=1` on any
  route renders it without the sidebar (the Processes pop-out window).
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
- **Headless `claude -p` runs are isolated** (`summarizer.ISOLATION_FLAGS`):
  hooks disabled, no MCP servers, no tools, no transcript. Global Stop hooks
  (the PayTable wrap-up check) otherwise block the stop and the reply to the
  hook gets cached as the summary/narrative. `--bare` is not an option: it
  ignores the OAuth login.
- **`run_claude` must run in a neutral cwd** - a headless `claude -p` started
  inside a git checkout reads that repo's CLAUDE.md and working tree and
  summarizes *it* instead of the payload on stdin. The server runs from the
  Watchtower checkout, so every summary came back describing Watchtower until
  the subprocess got `cwd=NEUTRAL_CWD` (an empty scratch dir).
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
- **Light/dark previews** (`/api/artifacts/{id}/view/{auto|light|dark}/...`):
  Chromium won't let an embedder change `prefers-color-scheme` inside an
  iframe, so a forced theme is applied to the served text - `data-theme` on
  the root (claude.ai's own toggle, which almost every artifact honors) plus
  every `prefers-color-scheme: X` in HTML/CSS/JS rewritten to an always/never
  query (`artifacts.force_scheme`). The theme is a path segment so a
  multi-file page's relative CSS/JS stay themed. `/content/` = unthemed.
- **Pins live in `artifact_pins`** (own table - `create_all` never alters
  existing tables, and pins are user state that must survive a re-index).
  Sorted by an integer `position` (new pins go last), not `pinned_at` -
  timestamps tie within a clock tick.
- **Open in browser** (`POST /api/artifacts/{id}/open`): the server launches
  the default browser because the launcher's Edge app window sends every
  `target=_blank` link to Edge. URLs are built server-side from the index
  (`artifacts.browser_url`), never accepted from the client, and the JSON body
  is the CSRF guard (non-JSON content types are rejected; cross-origin JSON
  needs a preflight only the Vite origin passes) - don't move `target` to a
  query param or form field.
- **Linked artifacts** (`artifacts.link`, `POST /api/artifacts/link`): for
  artifacts whose publishing transcript was cleaned up before indexing. A link
  is one `ArtifactOp` with `action="link"`, `tool="watchtower:link"`,
  `tool_use_id="link:<uuid>"` and **`session_id=""`** - code that walks ops by
  session must skip the empty id. Links don't count as versions (`op_count`
  stays 0), never get a local copy, and take dates from the version id: claude.ai
  version ids lead with the publish time in unix seconds (`version_time`).
  Watchtower can't read claude.ai itself - an agent with the claude-artifacts
  MCP supplies title/favicon/sharing/version (`get_artifact`).

## Processes (live)

- **Registry, not guesswork**: `~/.claude/sessions/<pid>.json` maps each
  running Claude process to its session id, cwd, name and busy/idle/shell
  status. Read only the `.json` files (the `.key` files hold peer tokens) and
  keep only `REGISTRY_FIELDS`. An entry is live only if its pid exists *and*
  its create time matches `procStart` (a Windows FILETIME) - pids get reused.
- **Never read process environments** - Claude's shells carry
  `CLAUDE_CODE_MESSAGING_TOKEN`. Commands come from the transcript, or from
  the bash wrapper's `eval '<cmd>'` (there's no `< /dev/null` when the
  command reads stdin, e.g. heredocs). The PowerShell launcher passes its
  script via an env var, so pwsh shells get their command from the transcript.
- **Only claude subtrees are opened**: `process_table` takes one system-wide
  ppid map (psutil's private `ppid_map`, with a public fallback) and opens
  only descendants of registry pids. `process_iter` over the whole machine
  took 140s on a paging machine. `ProcessCache` keeps psutil handles across
  polls because `cpu_percent()` measures between calls on the same handle.
- **In-flight tracking** lives in `TranscriptState.inflight` / `background`.
  A tool_result resolves its call by the unescaped `"tool_use_id":"..."` key
  (it can't match inside a JSON string; error results put it after the
  content). Background launches ("running in background with ID", "Monitor
  started (task", "Task ID:", "Async agent launched ... agentId:") stay until a
  `<task-notification>` with a final status, matched by tool-use id or task id.
- **Subagent transcripts are all `isSidechain`**: subagent states now consume
  their own sidechain records (main transcripts still skip inlined ones).
  `agent-<id>.meta.json` gives agentType/description/toolUseId/workflowPhase.
  Forks copy the parent's spawning Agent call into their transcript - skip
  the meta `toolUseId`.
- **Stale entries**: calls/tasks from before the process's `startedAt` belong
  to a previous run (crash, resume) and are dropped. A background shell task
  with no matching process has ended (killed with its turn).
- **Shell → call matching**: task id in the output redirect, then command
  text, then the latest unclaimed call started before the shell. Leftovers
  are "orphan" processes.
- **History only exists while watched**: `process_history` records a sample
  per `/api/processes` call (in memory, 60 min), so trend charts have gaps
  for time nobody had the page open. `?history=1` (first load only) returns
  the buffer; later polls append client-side. The first snapshot after a
  start is not charted (`sampled: false`) - psutil's first CPU reading is 0.
- **Timeline = hooks, overridden by samples**: 30s bins; a bin with recorded
  states uses them, any other bin replays hook events (prompt → working, Stop
  / idle ping → idle, other Notification → waiting). SessionStart is no
  change (it fires on compaction mid-turn). Hooks can't see a permission
  being granted for a non-edit tool, so hook-only "waiting" can run long.
- **Cards are masonry** (`MasonryGrid.vue`): absolutely positioned, measured
  with a ResizeObserver, each card under the shortest column. Keeps card
  state on column moves (no remount); ops start expanded.

## Architecture 3D

- `utils/archScene.js` owns pixels/layout/camera/picking, `ArchGraph3D.vue`
  owns labels/HUD/controls (same split as the cerberuslabsllc sphere).
  Labels are DOM, positioned per frame with no Vue reactivity, culled when
  they overlap (priority: selection, neighbours, degree).
- **Every build needs a fresh `<canvas>`** (`canvasKey`): `dispose()` calls
  `forceContextLoss()`, and a new renderer on that canvas fails.
- **UI overlays on the stage must `@pointerdown.stop`**: OrbitControls
  captures the pointer on the stage, which retargets clicks away from buttons.
- Headless screenshots: Edge `--screenshot` doesn't drive rAF. Use Playwright
  (system Python has it) with `--use-angle=swiftshader --enable-unsafe-swiftshader`.
- **Folder isolation** (legend click, `legendAction="isolate"`) happens in
  ArchitectureView, not the scene: the folder's projects (`role: member`) plus
  what they reference (`linked`, Direct/All) and optionally their users
  (`user`), each with `bundle` = its folder. Nodes with a `bundle` switch the
  layout to clusters: the bundle holding most primary nodes is the hub in the
  middle, the others ring it in wedges, and `layout.bundles` anchors the tags.
  Secondary roles draw smaller and fainter (`emphasis` in the scene).
- **One palette and legend for the whole solution** (`palette` prop, from
  `folderColorMap(arch.graph.nodes)`): extra-folder colors are assigned by
  sorted name, so computing them from a subset recolors folders, and a legend
  built from the subset moves the chips under the cursor (hover previews then
  dim the wrong folder).
- **Scopes** (`utils/archScope.js`): below the project level, dotnet only. A
  namespace / file / class scope draws classes (links: method calls, sends ->
  handler, service uses, ctor injection; interfaces stay as hops to their
  implementations); a method / endpoint scope draws methods (calls, interface
  dispatch, sends -> `Handle`). Downstream to the chosen depth, callers one
  hop (12 per node, the rest counted), `SCOPE_LIMIT` 160 nodes. Node ids are
  prefixed (`t:`, `m:`, `e:`) so they never collide with project ids. A
  traced flow (`role: flow`) draws full strength and starts fitted
  (`frameFocus=false`) - flying to the endpoint cut the path off.
- **The lists drive scopes**: ArchitectureView `provide('archScope', {active,
  set})`; ProjectMethodsPanel (namespace -> file -> type -> methods),
  MethodNode (its "3D" button) and DataFlowTable (row click; "Inspect" opens
  the drawer) call `set`. Picking the active scope again steps out. Esc steps
  out one level: drawer, scope, isolation, focus.
- **Types carry `namespace` and `line`** (dotnet scanner). Scans cached before
  that (History architectural deltas, cached forever per sha) don't - code
  must fall back (`archScope.namespaceOf` derives it from the folder path).

## Diff reader (`DiffView.vue`)

- Shared by History (commit and bundle files) and the Review Queue. Parsed by
  `utils/diffParse.js`: hunks end by their own line counts (so a commit
  message's indented lines aren't read as context), git show headers fold to
  one line, untracked files arrive as a synthetic `--- /dev/null` hunk.
- **Each side of a hunk is tokenized as the code it was** (context + removed
  vs context + added), so multi-line strings and comments color correctly on
  both sides. VS Code Dark+ over VS Code's TextMate grammars (Shiki, JS regex
  engine - no wasm); a hunk side over 6000 lines stays plain.
- Word-level marks pair each removed run with the added run after it (token
  LCS); a line that changed by more than 60% gets no marks. Unified / Split
  and Wrap are remembered in localStorage.
- History's commit message box scrolls inside a height dragged from its
  bottom edge (`useEdgeResize` with `edge: 'bottom'`; double-click resets) and
  folds to the subject line; both are remembered.

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
  should report ~52 projects / ~380 handlers / ~554 endpoints / ~5k methods in
  ~10s, with a JSON payload around 1.8 MB. (The solution grows - the point of
  the check is that the numbers stay in the same neighbourhood, not that they
  match exactly.)
- The Git History architectural delta scans two whole trees (`git archive` ->
  temp dir -> `scan_architecture`), so budget ~11s per uncached commit on
  payTableDotnet - then it's cached forever per (repo, sha) and re-requests are
  instant. The archive streams to disk, so peak memory stays flat (~40 MB) no
  matter how big the tree is; `MAX_ARCHIVE_BYTES` (1 GB) only exists to skip a
  pathological repo, and is checked before extraction.
