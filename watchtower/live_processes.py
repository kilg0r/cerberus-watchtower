"""Live Claude Code processes - what every running session is doing right now.

Three sources, joined per Claude process (one "container" per process):

- `~/.claude/sessions/<pid>.json`: Claude Code's own registry of running
  sessions (pid -> session id, cwd, name, busy/idle/shell status). Only the
  `.json` files are read - the `.key` files beside them hold peer tokens.
- the OS process table (psutil): each claude.exe's descendants - the shells
  its tool calls run in and everything they started (dotnet test, python...).
  Direct children without Claude's shell-wrapper signature are MCP servers.
- the session's transcripts (`SessionIndex`): tool calls in flight, background
  tasks, and subagents with their own in-flight calls.

Shells are matched to the tool call that started them (background tasks by
the task id in their output redirect, foreground calls by command text, then
by start time), so a `dotnet test` shows up under the subagent that ran it.
Process environments are never read: they carry Claude's messaging tokens.
"""

import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psutil

SESSIONS_DIR = Path.home() / ".claude" / "sessions"
TASK_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "claude"
FILETIME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01
SUBAGENT_RECENT_SECONDS = 60  # a subagent written this recently counts as running
CMD_CHARS = 400
TAIL_BYTES = 6 * 1024
TAIL_LINES = 4
SKIP_NAMES = {"conhost.exe", "openconsole.exe"}
REGISTRY_FIELDS = (
    "pid", "sessionId", "cwd", "name", "status", "kind", "entrypoint",
    "version", "startedAt", "updatedAt", "statusUpdatedAt", "procStart",
)

# ---------------------------------------------------------------- categories
# Seven identities (the view gives each a fixed color) plus neutral "tool".
# Order matters: the first matching rule wins.
_RULES: list[tuple[str, re.Pattern]] = [
    ("test", re.compile(
        r"\b(dotnet(\.exe)?\s+test|vstest|testhost|pytest|py\.test|-m\s+pytest|-m\s+unittest|vitest|jest|mocha"
        r"|playwright\s+test|cypress\s+run|(npm|yarn|pnpm)\s+(run\s+)?test|go\s+test|cargo\s+test|Invoke-Pester"
        r"|stryker|mutants?\b)", re.I)),
    ("build", re.compile(
        r"\b(dotnet(\.exe)?\s+(build|publish|restore|pack|msbuild|ef)|msbuild|csc\.exe|vbcscompiler"
        r"|(npm|yarn|pnpm)\s+run\s+build|vite\s+build|tsc\b|webpack|esbuild|rollup|pyinstaller"
        r"|cargo\s+build|cmake|gradle|mvn\b|make\b)", re.I)),
    ("service", re.compile(
        r"\b(uvicorn|gunicorn|flask\s+run|http\.server|(npm|yarn|pnpm)\s+run\s+(dev|serve|start)"
        r"|dotnet(\.exe)?\s+(run|watch)|nodemon|tail\s+-f|Get-Content\b.*-Wait|watch\b)", re.I)),
    ("network", re.compile(
        r"\b(curl|wget|ssh|scp|rsync|sftp|Invoke-WebRequest|Invoke-RestMethod|iwr|irm|gh\s|az\s|aws\s"
        r"|gcloud|kubectl|docker|podman|ping|nslookup|Resolve-DnsName|Test-NetConnection|git\s+(push|pull|fetch|clone))\b",
        re.I)),
    ("script", re.compile(
        r"(\b(python[0-9.]*|py|node|deno|bun|ruby|npx|uv\s+run)(\.exe)?\b|\.(py|ps1|js|mjs|ts)\b|-File\s)", re.I)),
]
_TAGS: list[tuple[str, re.Pattern]] = [
    ("git", re.compile(r"\b(git|gh)(\.exe)?\s", re.I)),
    ("install", re.compile(r"\b((npm|pnpm|yarn)\s+(i|install|ci|add)\b|pip\s+install|uv\s+(pip|sync|add)\b|winget|choco)", re.I)),
    ("docker", re.compile(r"\b(docker|podman)\b", re.I)),
    ("ssh", re.compile(r"\b(ssh|scp|rsync)\b", re.I)),
    ("wait", re.compile(r"\b(sleep|Start-Sleep|timeout\s+/t)\b", re.I)),
]

_FILE_TOOLS = {"Read", "Write", "Edit", "NotebookEdit", "Grep", "Glob", "LSP", "ToolSearch", "Skill"}
_SHELL_TOOLS = {"Bash", "PowerShell"}


def classify_command(text: str | None) -> tuple[str, str | None]:
    """(category, tag) of a shell command line."""
    text = text or ""
    category = next((name for name, rule in _RULES if rule.search(text)), "shell")
    # tags refine plain commands only - "test · docker" from a path is noise
    tag = next((name for name, rule in _TAGS if rule.search(text)), None) if category in ("shell", "network") else None
    return category, tag


def classify_tool(tool: str, detail: str | None = None) -> tuple[str, str | None]:
    """(category, tag) of a tool call."""
    if tool in _SHELL_TOOLS:
        return classify_command(detail)
    if tool == "Monitor":
        return "service", "monitor"
    if tool in ("Agent", "Workflow"):
        return "agent", tool.lower()
    if tool == "AskUserQuestion":
        return "waiting", None
    if tool in ("WebFetch", "WebSearch"):
        return "network", "web"
    if tool.startswith("mcp__"):
        server = tool.split("__")[1] if tool.count("__") >= 2 else tool
        return ("tool", "serena") if server == "serena" else ("network", "mcp")
    return "tool", None


# ---------------------------------------------------------------- registry

def filetime_to_unix(value) -> float | None:
    """Windows FILETIME (100ns ticks since 1601) -> unix seconds."""
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return None
    return ticks / 1e7 - FILETIME_EPOCH_OFFSET if ticks > 0 else None


def read_registry(directory: Path = SESSIONS_DIR) -> list[dict]:
    """Claude Code's running-session registry, one entry per process. Stale
    files (dead pids) are filtered later against the process table."""
    entries = []
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("pid"), int) or not isinstance(raw.get("sessionId"), str):
            continue
        entries.append({key: raw.get(key) for key in REGISTRY_FIELDS})
    return entries


# ---------------------------------------------------------------- process table

@dataclass
class Proc:
    pid: int
    ppid: int | None
    name: str
    create_time: float | None
    children: list["Proc"] = field(default_factory=list)


def _ppid_map() -> dict[int, int]:
    """pid -> parent pid for the whole machine. psutil's platform helper is
    one system call on Windows; process_iter would open every process (on a
    paging machine that took minutes)."""
    try:
        from psutil import _psplatform

        return dict(_psplatform.ppid_map())
    except Exception:  # noqa: BLE001 - private API; fall back to the public, slower path
        return {p.pid: p.info["ppid"] for p in psutil.process_iter(["ppid"])}


MAX_DEPTH = 12


def process_table(roots, procs: "ProcessCache") -> dict[int, Proc]:
    """The process subtrees under `roots` (the Claude pids), parent links
    resolved. Only these processes are ever opened. A child must not predate
    its parent: Windows reuses pids, and a reused parent pid would otherwise
    adopt unrelated processes."""
    ppids = _ppid_map()
    kids: dict[int, list[int]] = {}
    for pid, ppid in ppids.items():
        if ppid != pid:
            kids.setdefault(ppid, []).append(pid)
    table: dict[int, Proc] = {}

    def visit(pid: int, depth: int) -> Proc | None:
        identity = procs.identity(pid)
        if identity is None:
            return None
        proc = table[pid] = Proc(pid, ppids.get(pid), *identity)
        if depth < MAX_DEPTH:
            for child_pid in kids.get(pid, []):
                if child_pid in table:
                    continue
                child = visit(child_pid, depth + 1)
                if child and not (proc.create_time and child.create_time and child.create_time + 1 < proc.create_time):
                    proc.children.append(child)
        return proc

    for root in roots:
        if root in ppids and root not in table:
            visit(root, 0)
    procs.prune(set(ppids))
    return table


class ProcessCache:
    """psutil handles kept across polls: cpu_percent() measures since the
    previous call on the same handle, and names/command lines never change."""

    def __init__(self) -> None:
        self._handles: dict[int, psutil.Process] = {}
        self._cmdlines: dict[tuple[int, float | None], list[str]] = {}
        self.cpu_count = psutil.cpu_count() or 1

    def prune(self, live_pids: set[int]) -> None:
        for pid in [p for p in self._handles if p not in live_pids]:
            del self._handles[pid]
        for key in [k for k in self._cmdlines if k[0] not in live_pids]:
            del self._cmdlines[key]

    def _handle(self, pid: int) -> psutil.Process | None:
        handle = self._handles.get(pid)
        if handle is not None and not handle.is_running():  # pid reused
            handle = None
        if handle is None:
            try:
                handle = psutil.Process(pid)
                handle.cpu_percent(None)  # prime: the first reading is always 0
            except (psutil.Error, OSError):
                self._handles.pop(pid, None)
                return None
            self._handles[pid] = handle
        return handle

    def identity(self, pid: int) -> tuple[str, float | None] | None:
        handle = self._handle(pid)
        if handle is None:
            return None
        try:
            return handle.name(), handle.create_time()
        except (psutil.Error, OSError):
            return None

    def cmdline(self, proc: Proc) -> list[str]:
        key = (proc.pid, proc.create_time)
        if key not in self._cmdlines:
            handle = self._handle(proc.pid)
            try:
                self._cmdlines[key] = handle.cmdline() if handle else []
            except (psutil.Error, OSError):
                self._cmdlines[key] = []
        return self._cmdlines[key]

    def usage(self, proc: Proc) -> tuple[float, float]:
        """(cpu as % of the whole machine, resident memory in MB)."""
        handle = self._handle(proc.pid)
        if handle is None:
            return 0.0, 0.0
        try:
            cpu = handle.cpu_percent(None) / self.cpu_count
            mem = handle.memory_info().rss / (1024 * 1024)
        except (psutil.Error, OSError):
            return 0.0, 0.0
        return round(cpu, 1), round(mem, 1)


cache = ProcessCache()


# ---------------------------------------------------------------- commands

# Claude's bash wrapper: `... && eval '<cmd>' [< /dev/null] && pwd -P >| <file>`
# (no /dev/null redirect when the command reads stdin, e.g. a heredoc)
_BASH_EVAL_RE = re.compile(r"&& eval '(.*)'(?: < /dev/null)? && pwd -P >\| \S+\s*$", re.S)
_BASH_EVAL_LOOSE_RE = re.compile(r"&& eval '(.*)'(?: < /dev/null)?", re.S)
_TASK_OUTPUT_RE = re.compile(r"[\\/]tasks[\\/]([A-Za-z0-9_-]+)\.output")
_SHELL_NAMES = {"bash.exe", "sh.exe", "cmd.exe", "pwsh.exe", "powershell.exe", "bash", "sh", "zsh"}


def is_tool_shell(cmdline: list[str]) -> bool:
    """A shell Claude Code started for a tool call (vs an MCP server)."""
    text = " ".join(cmdline)
    return "shell-snapshots" in text or "CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT" in text or "claude-pwd-ps" in text


def shell_command(cmdline: list[str]) -> str | None:
    """The user-level command inside Claude's shell wrapper. Bash wrappers
    carry it as `eval '<cmd>'`; the PowerShell launcher passes it in an
    environment variable (never read), so None - the transcript has it."""
    text = " ".join(cmdline)
    match = _BASH_EVAL_RE.search(text) or _BASH_EVAL_LOOSE_RE.search(text)
    if match:
        return match.group(1).replace("'\"'\"'", "'").replace("'\\''", "'")
    if "CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT" in text:
        return None
    return None


def task_id_of(cmdline: list[str]) -> str | None:
    """Background tasks redirect into <tmp>/claude/.../tasks/<id>.output."""
    match = _TASK_OUTPUT_RE.search(" ".join(cmdline))
    return match.group(1) if match else None


def display_command(name: str, cmdline: list[str]) -> str:
    """A readable command line: executable basename + args, clipped."""
    if not cmdline:
        return name
    exe = re.split(r"[\\/]", cmdline[0])[-1]
    text = " ".join([exe, *cmdline[1:]])
    return text if len(text) <= CMD_CHARS else text[: CMD_CHARS - 1] + "…"


def service_label(name: str, cmdline: list[str]) -> str:
    """A short name for an MCP server / helper process."""
    text = " ".join(cmdline)
    for pattern in (r"([A-Za-z0-9_.-]+)[\\/]dist[\\/]index\.js", r"[\\/]([A-Za-z0-9_.-]+-mcp)[\\/]",
                    r"\b([A-Za-z0-9_.-]+)(\.exe)?\s+start-mcp-server", r"@([A-Za-z0-9_./-]+mcp[A-Za-z0-9_.-]*)",
                    r"([A-Za-z0-9_.-]*mcp[A-Za-z0-9_.-]*)"):
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return name.removesuffix(".exe")


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _unix(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


# ---------------------------------------------------------------- trees

def _visible_children(proc: Proc) -> list[Proc]:
    return [c for c in proc.children if c.name.lower() not in SKIP_NAMES]


def build_tree(proc: Proc, procs: ProcessCache, inherited_cmd: str | None = None) -> dict:
    """A process subtree for display. Wrapper layers (Git Bash re-execs
    itself; cmd.exe wraps pwsh) collapse into one node so the tree shows the
    command, then what it actually started."""
    cmdline = procs.cmdline(proc)
    command = shell_command(cmdline) or inherited_cmd
    kids = _visible_children(proc)
    lowered = proc.name.lower()
    # collapse a shell whose only child is another shell layer
    while lowered in _SHELL_NAMES and len(kids) == 1 and kids[0].name.lower() in _SHELL_NAMES:
        proc = kids[0]
        command = shell_command(procs.cmdline(proc)) or command
        kids = _visible_children(proc)
        lowered = proc.name.lower()
    cpu, mem = procs.usage(proc)
    is_shell = lowered in _SHELL_NAMES
    if is_shell:
        # a wrapper without a readable command (the PowerShell launcher) is
        # just "a shell"; its command comes from the transcript
        text = command
        category, tag = classify_command(command) if command else ("shell", None)
    else:
        text = display_command(proc.name, procs.cmdline(proc))
        category, tag = classify_command(text)
    node = {
        "pid": proc.pid,
        "name": proc.name,
        "cmd": text,
        "category": category,
        "tag": tag,
        "cpu": cpu,
        "mem_mb": mem,
        "started_at": _iso(proc.create_time),
        "children": [build_tree(child, procs) for child in kids],
    }
    node["total_cpu"] = round(cpu + sum(c["total_cpu"] for c in node["children"]), 1)
    node["total_mem_mb"] = round(mem + sum(c["total_mem_mb"] for c in node["children"]), 1)
    return node


def _subtree_mem(proc: Proc, procs: ProcessCache, depth: int = 0) -> float:
    if depth > 6:
        return 0.0
    _, mem = procs.usage(proc)
    return mem + sum(_subtree_mem(c, procs, depth + 1) for c in proc.children)


def tree_text(node: dict) -> str:
    """Every command in a tree, for classification and task-id lookups."""
    return " ".join(filter(None, [node.get("cmd") or "", *(tree_text(c) for c in node["children"])]))


def strongest_category(node: dict) -> tuple[str, str | None]:
    """A shell running `dotnet test` is a test, whatever wraps it: the most
    specific category anywhere in the tree wins."""
    rank = {"test": 0, "build": 1, "service": 2, "network": 3, "script": 4, "shell": 5}
    best = (node["category"], node.get("tag"))
    stack = list(node["children"])
    while stack:
        child = stack.pop()
        if rank.get(child["category"], 9) < rank.get(best[0], 9):
            best = (child["category"], child.get("tag") or best[1])
        stack.extend(child["children"])
    return best


# ---------------------------------------------------------------- output tails

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def tail_output(path_text: str | None, root: Path = TASK_OUTPUT_ROOT) -> list[str]:
    """Last few lines a background task wrote. Paths come from transcripts,
    so only `.output` files under Claude's temp dir are read."""
    if not path_text:
        return []
    try:
        path = Path(path_text).resolve()
        root = root.resolve()
    except (OSError, ValueError):
        return []
    if path.suffix != ".output" or not path.is_relative_to(root):
        return []
    try:
        with path.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    text = _ANSI_RE.sub("", data.decode("utf-8", errors="replace")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n") if line.strip()]
    if size > TAIL_BYTES and lines:
        lines = lines[1:]  # the first line was cut mid-way
    return [line if len(line) <= 220 else line[:219] + "…" for line in lines[-TAIL_LINES:]]


# ---------------------------------------------------------------- joining

def _op(entry: dict, started: float | None, now: float) -> dict:
    category, tag = classify_tool(entry["tool"], entry.get("detail"))
    begun = _unix(entry.get("ts")) or started
    return {
        "id": entry["id"],
        "tool": entry["tool"],
        "label": entry.get("label"),
        "detail": entry.get("detail"),
        "category": category,
        "tag": tag,
        "agent_type": entry.get("agent_type"),
        "started_at": entry.get("ts"),
        "elapsed_s": round(now - begun) if begun else None,
        "processes": [],
    }


def _fresh(ts: str | None, since: float | None) -> bool:
    """Entries from before this process started belong to a previous run of
    the session (a crash or a resume) - their results will never arrive."""
    if since is None:
        return True
    stamp = _unix(ts)
    return stamp is None or stamp >= since - 5


def match_shells(shells: list[dict], ops: list[dict]) -> list[dict]:
    """Attach shell trees to the calls that started them; return the rest.
    `ops` holds in-flight calls and background tasks alike.

    1. a background task's id appears in its shell's output redirect
    2. a bash call's command is in its wrapper verbatim
    3. otherwise the latest unclaimed shell call started before the shell
    """
    by_task = {o["task_id"]: o for o in ops if o.get("task_id")}
    shell_ops = [o for o in ops if o["tool"] in _SHELL_TOOLS or o["tool"] == "Monitor"]
    claimed: set[str] = set()

    def claim(op: dict, shell: dict) -> None:
        claimed.add(op["id"])
        op["processes"].append(shell["tree"])

    pending = []
    for shell in shells:
        task = by_task.get(shell["task_id"]) if shell.get("task_id") else None
        if task is not None:
            claim(task, shell)
        else:
            pending.append(shell)
    leftovers = []
    for shell in pending:
        command = shell["tree"].get("cmd") or ""
        head = command[:120]
        hit = next(
            (o for o in shell_ops if o["id"] not in claimed and o.get("detail") and head
             and (command.startswith(o["detail"].rstrip("…")[:120]) or o["detail"].startswith(head))),
            None,
        )
        if hit:
            claim(hit, shell)
        else:
            leftovers.append(shell)
    rest = []
    for shell in sorted(leftovers, key=lambda s: s["created"] or 0):
        candidates = [
            o for o in shell_ops
            if o["id"] not in claimed and (o.get("_ts") or 0) <= (shell["created"] or 0) + 5
        ]
        hit = max(candidates, key=lambda o: o.get("_ts") or 0, default=None)
        if hit:
            claim(hit, shell)
        else:
            rest.append(shell)
    return rest


def session_view(entry: dict, proc: Proc, states: list, procs: ProcessCache, now: float, attach_transcript: bool = True) -> dict:
    """One Claude process as a container: its lanes (main agent + running
    subagents), background tasks, tool processes and MCP services."""
    started = (entry.get("startedAt") or 0) / 1000 or proc.create_time
    main = next((s for s in states if not s.is_subagent), None)
    subs = [s for s in states if s.is_subagent]

    main_ops: list[dict] = []
    backgrounds: list[dict] = []
    lanes: list[dict] = []
    if attach_transcript and main is not None:
        live = main.live()
        inflight = [e for e in live["inflight"] if _fresh(e.get("ts"), started)]
        bg = [e for e in live["background"] if _fresh(e.get("launched_at"), started)]
        inflight_ids = {e["id"] for e in inflight}
        bg_by_id = {e["id"]: e for e in bg}
        running_runs = {e.get("workflow_run") for e in bg if e.get("workflow_run")}

        for sub in subs:
            spawn = sub.meta.get("toolUseId")
            last = _unix(sub.last_ts)
            if last is not None and started and last < started - 5:
                continue
            recent = last is not None and now - last < SUBAGENT_RECENT_SECONDS
            sub_live = sub.live()
            # a subagent's background tasks outlive its turn: always collect them
            via = sub.meta.get("description") or sub.meta.get("agentType") or "subagent"
            backgrounds.extend(
                {**b, "_lane": sub.agent_id, "_via": via}
                for b in sub_live["background"] if _fresh(b.get("launched_at"), started)
            )
            if spawn:
                # its Agent call is still open (foreground) or its task still
                # running - or it was resumed later (SendMessage) and is writing
                running = spawn in inflight_ids or spawn in bg_by_id or recent
            elif sub.workflow_run and running_runs:
                running = sub.workflow_run in running_runs and (recent or bool(sub_live["inflight"]))
            else:
                running = recent
            if not running:
                continue
            ops = []
            for e in sub_live["inflight"]:
                if _fresh(e.get("ts"), started):
                    ops.append({**_op(e, started, now), "_ts": _unix(e.get("ts"))})
            meta = sub.meta
            agent_type = meta.get("agentType") or "subagent"
            lanes.append({
                "id": sub.agent_id,
                "kind": "workflow" if sub.workflow_run else ("fork" if meta.get("isFork") or agent_type == "fork" else "subagent"),
                "agent_type": agent_type,
                "label": meta.get("description") or agent_type,
                "phase": meta.get("workflowPhase"),
                "workflow_run": sub.workflow_run,
                "background": meta.get("requestShape") == "background" or spawn in bg_by_id,
                "model": meta.get("model"),
                "started_at": sub.first_ts,
                "last_activity": sub.last_ts,
                "tool_total": sum(sub.tool_counts.values()),
                "spawn_id": spawn,
                "ops": ops,
            })

        lane_spawns = {lane["spawn_id"] for lane in lanes if lane.get("spawn_id")}
        for e in inflight:
            if e["tool"] == "Agent" and e["id"] in lane_spawns:
                continue  # shown as its own lane
            o = _op(e, started, now)
            o["_ts"] = _unix(e.get("ts"))
            main_ops.append(o)
        backgrounds.extend(bg_by_id.values())

    # background tasks, shaped like ops; agent/workflow launches are lanes
    bg_items = []
    lane_spawns = {lane.get("spawn_id") for lane in lanes}
    for b in backgrounds:
        if b["tool"] == "Agent" and b["id"] in lane_spawns:
            continue  # its lane shows it
        item = _op(b, started, now)
        launched = _unix(b.get("launched_at"))
        item.update({
            "task_id": b.get("task_id"),
            "lane": b.get("_lane"),
            "via": b.get("_via"),
            "workflow_run": b.get("workflow_run"),
            "started_at": b.get("launched_at") or b.get("ts"),
            "elapsed_s": round(now - launched) if launched else item["elapsed_s"],
            "tail": tail_output(b.get("output_file")),
            "_ts": _unix(b.get("ts")),
        })
        bg_items.append(item)

    # the OS side: tool shells and services under this claude process
    shells, services = [], []
    for child in _visible_children(proc):
        cmdline = procs.cmdline(child)
        if is_tool_shell(cmdline):
            tree = build_tree(child, procs)
            category, tag = strongest_category(tree)
            tree["category"], tree["tag"] = category, tag or tree.get("tag")
            shells.append({"tree": tree, "task_id": task_id_of(cmdline), "created": child.create_time})
        else:
            cpu, _ = procs.usage(child)
            services.append({
                "pid": child.pid,
                "label": service_label(child.name, cmdline),
                "mem_mb": round(_subtree_mem(child, procs), 1),
                "cpu": cpu,
            })

    all_ops = main_ops + [o for lane in lanes for o in lane["ops"]]
    orphans = match_shells(shells, all_ops + bg_items)
    # a background shell task with no process left has ended - killed with
    # the turn, or its notification is still on the way
    bg_items = [b for b in bg_items if b["tool"] not in ("Bash", "PowerShell", "Monitor") or b["processes"]]
    for shell in orphans:  # e.g. a `!` command typed into the session
        tree = shell["tree"]
        main_ops.append({
            "id": f"pid:{tree['pid']}",
            "tool": "process",
            "label": None,
            "detail": tree.get("cmd") or tree["name"],
            "category": tree["category"],
            "tag": tree.get("tag"),
            "agent_type": None,
            "started_at": tree["started_at"],
            "elapsed_s": round(now - shell["created"]) if shell["created"] else None,
            "processes": [tree],
        })

    # a shell call's category follows what its processes turned out to run
    for o in all_ops + bg_items:
        o.pop("_ts", None)
        if o["tool"] in _SHELL_TOOLS and o["processes"]:
            best = strongest_category(o["processes"][0])
            if best[0] != "shell" or o["category"] == "shell":
                o["category"], o["tag"] = best[0], best[1] or o["tag"]
        if o["tool"] in _SHELL_TOOLS and not o.get("detail") and o["processes"]:
            o["detail"] = o["processes"][0].get("cmd")
    for lane in lanes:
        lane.pop("spawn_id", None)

    cpu, mem = procs.usage(proc)
    tool_cpu = sum(t["tree"]["total_cpu"] for t in shells)
    tool_mem = sum(t["tree"]["total_mem_mb"] for t in shells)
    lanes.insert(0, {
        "id": "main",
        "kind": "main",
        "agent_type": None,
        "label": "Main agent",
        "phase": None,
        "workflow_run": None,
        "background": False,
        "model": None,
        "started_at": _iso(started),
        "last_activity": main.last_ts if main else None,
        "tool_total": sum(main.tool_counts.values()) if main else 0,
        "ops": main_ops,
    })
    return {
        "pid": entry["pid"],
        "session_id": entry["sessionId"],
        "name": entry.get("name"),
        "status": entry.get("status"),
        "kind": entry.get("kind"),
        "entrypoint": entry.get("entrypoint"),
        "version": entry.get("version"),
        "cwd": entry.get("cwd") or (main.cwd if main else None),
        "title": main.title if main else None,
        "last_prompt": main.last_prompt if main else None,
        "started_at": _iso(started),
        "status_since": _iso((entry.get("statusUpdatedAt") or 0) / 1000 or None),
        "last_activity": main.last_ts if main else None,
        "transcript": attach_transcript,
        "cpu": round(cpu + tool_cpu, 1),
        "mem_mb": round(mem + tool_mem, 1),
        "claude_mem_mb": mem,
        "lanes": lanes,
        "background": bg_items,
        "services": services,
    }


def live_entries(registry: list[dict], table: dict[int, Proc]) -> list[tuple[dict, Proc]]:
    """Registry entries whose process is still the one that wrote them (the
    pid exists and was created when the file says)."""
    out = []
    for entry in registry:
        proc = table.get(entry["pid"])
        if proc is None:
            continue
        expected = filetime_to_unix(entry.get("procStart"))
        if expected and proc.create_time and abs(proc.create_time - expected) > 2:
            continue  # pid reused by another process
        out.append((entry, proc))
    return out


def transcript_owner(entries: list[tuple[dict, Proc]]) -> dict[str, int]:
    """When two processes share a session id (the same session open twice),
    transcript activity belongs to the busy one, else the latest updated."""
    owner: dict[str, tuple] = {}
    for entry, _ in entries:
        key = (entry.get("status") == "busy", entry.get("statusUpdatedAt") or entry.get("updatedAt") or 0)
        current = owner.get(entry["sessionId"])
        if current is None or key > current[0]:
            owner[entry["sessionId"]] = (key, entry["pid"])
    return {sid: pid for sid, (_, pid) in owner.items()}


def snapshot(index, registry_dir: Path = SESSIONS_DIR) -> dict:
    """Every live Claude process with what it is running."""
    now = time.time()
    registry = read_registry(registry_dir)
    table = process_table([e["pid"] for e in registry], cache)
    entries = live_entries(registry, table)
    owners = transcript_owner(entries)
    states = index.states_for({e["sessionId"] for e, _ in entries}, advance=True)
    views = [
        session_view(entry, proc, states.get(entry["sessionId"], []), cache, now,
                     attach_transcript=owners.get(entry["sessionId"]) == entry["pid"])
        for entry, proc in entries
    ]
    memory = psutil.virtual_memory()
    return {
        "sessions": views,
        "host": {
            "cpu": round(psutil.cpu_percent(None), 1),
            "cpu_count": cache.cpu_count,
            "mem_percent": memory.percent,
            "mem_used_gb": round((memory.total - memory.available) / 1024**3, 1),
            "mem_total_gb": round(memory.total / 1024**3, 1),
        },
        "generated_at": _iso(now),
    }
