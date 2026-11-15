"""Claude Code transcript parser - session history for the activity feed.

Transcripts live at ~/.claude/projects/<project-slug>/<session-id>.jsonl, one
JSON object per line, with subagent transcripts beside them at
<session-id>/subagents/agent-*.jsonl. Busy sessions reach hundreds of MB, so
parsing is incremental: a `TranscriptState` remembers the byte offset it has
consumed and each `advance()` reads only what was appended since. Byte-level
substring pre-filters keep json.loads off the hot path - only the handful of
record kinds we summarize are ever parsed.

Besides the session summary, each pass yields the session's artifact
operations (claude.ai artifact publishes/edits and Claude Docs creates/updates)
as resolved tool_use/tool_result pairs, for `watchtower.artifacts` to index.

Each state also tracks what is running right now, for the Processes view:
`inflight` holds tool calls whose result hasn't been written yet, and
`background` holds calls that returned "running in background" (Bash/
PowerShell/Monitor tasks, background agents) until their
<task-notification> reports a final status.
"""

import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
FILE_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_MESSAGE_CHARS = 1500
MAX_CONVERSATION_BYTES = 60 * 1024
READ_BLOCK = 8 * 1024 * 1024
TAIL_LINES = 64  # recent raw lines kept per pass to recover last timestamp/cwd
# conversation extraction reads at most this much of a huge transcript: the
# opening ask lives in the head, the current state in the tail
CONVERSATION_HEAD_BYTES = 8 * 1024 * 1024
CONVERSATION_TAIL_BYTES = 32 * 1024 * 1024

# tools whose calls create or change an artifact (a claude.ai page or a Claude Doc)
ARTIFACT_TOOLS = {
    "Artifact",  # Claude Code's built-in: publishes a local .md/.html file
    "mcp__claude-artifacts__publish_version",
    "mcp__claude-artifacts__publish_files",
    "mcp__claude-artifacts__edit_version",
    "mcp__claude-artifacts__create_from_type",
    "mcp__claude-artifacts__delete_artifact",
    "mcp__claude_ai_Claude_Docs__batch",
    "mcp__claude_ai_Claude_Docs__create",
    "mcp__claude_ai_Claude_Docs__update",
    "mcp__claude_ai_Claude_Docs__delete",
}

# Byte pre-filters assume Claude Code's compact JSON (no space after ':').
# api-request-blob records replay the whole request (tool calls included); they
# are duplicates of the real assistant/user records and must never be counted
_SKIP_PREFIXES = (b'{"type":"api-request-blob"',)
_TOOL_USE = b'"type":"tool_use"'
_TOOL_RESULT = b'"tool_result"'
_USER = b'"type":"user"'
_AI_TITLE = b'"aiTitle"'
_TASK_NOTIFICATION = b"<task-notification>"

# A tool_result's id key. Inside a JSON string every quote is escaped
# (\"tool_use_id\":\"), so this only matches a real key, never an id quoted in
# some tool's output. The key's position varies (error results put it after
# the content), so it isn't anchored to the block header.
_RESULT_ID_RE = re.compile(rb'"tool_use_id":"([A-Za-z0-9_-]+)"')
_NOTIFICATION_RE = re.compile(rb"<task-notification>(.*?)</task-notification>")
_NOTIFY_FIELD_RE = re.compile(rb"<(task-id|tool-use-id|status|output-file)>(.*?)</\1>")
_BG_TASK_RE = re.compile(r"running in background with ID: ([A-Za-z0-9_-]+)")
_BG_OUTPUT_RE = re.compile(r"Output is being written to: (\S+?\.output)")
_AGENT_ID_RE = re.compile(r"agentId: ([A-Za-z0-9_-]+)")
_TASK_ID_RE = re.compile(r"\(task ([A-Za-z0-9_-]+)|Task ID: ([A-Za-z0-9_-]+)")
_WORKFLOW_NAME_RE = re.compile(r"\bname\s*:\s*['\"]([^'\"]{1,80})['\"]")
_WORKFLOW_RUN_RE = re.compile(r"workflows[\\/]+(wf_[A-Za-z0-9_-]+)")
_FINAL_STATUSES = {b"completed", b"failed", b"killed", b"stopped", b"cancelled", b"error"}
# tools whose result can say "started in the background": the result line is
# parsed for these only (they are small - a launch notice, not output)
_BACKGROUND_TOOLS = {"Monitor", "Workflow", "Agent"}
_SHELL_TOOLS = {"Bash", "PowerShell"}
MAX_INFLIGHT = 64  # per transcript; older entries are dropped (crashed turns)
MAX_BACKGROUND = 48
BRIEF_CHARS = 400


def _safe_loads(line: str | bytes) -> dict | None:
    try:
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _message_text(content) -> str:
    """Human-readable text from a message content field (string or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            block.get("text", "").strip()
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t)
    return ""


def _result_text(content) -> str:
    """Flatten a tool_result content field to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _subagents_dir(path: Path) -> Path | None:
    """The `subagents` ancestor of a subagent transcript - either
    <session>/subagents/agent-*.jsonl or, for workflow agents,
    <session>/subagents/workflows/<run>/agent-*.jsonl."""
    for parent in path.parents:
        if parent.name == "subagents":
            return parent
        if parent.parent == parent or parent.name == CLAUDE_PROJECTS_DIR.name:
            break
    return None


def session_id_for(path: Path) -> str:
    """Owning session of a transcript - subagent transcripts belong to their parent."""
    subagents = _subagents_dir(path)
    return subagents.parent.name if subagents else path.stem


def _read_meta(path: Path) -> dict:
    """agent-<id>.meta.json: agentType, description, toolUseId (the spawning
    Agent call), requestShape, workflowPhase, model. Small; {} if absent."""
    try:
        meta = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    keep = ("agentType", "description", "toolUseId", "requestShape", "workflowPhase", "model", "isFork")
    return {k: meta[k] for k in keep if k in meta} if isinstance(meta, dict) else {}


def _clip(value, limit: int = BRIEF_CHARS) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def tool_brief(name: str, tool_input: dict) -> dict:
    """The few fields of a tool call worth showing while it runs: a one-line
    label (usually the call's own description) and the detail behind it
    (command, file, pattern, url). Bounded - inputs can be megabytes."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    get = tool_input.get
    label = _clip(get("description"), 160)
    detail = None
    extra: dict = {}
    if name in _SHELL_TOOLS or name == "Monitor":
        detail = _clip(get("command"))
        if get("run_in_background"):
            extra["background"] = True
    elif name in ("Read", "Write", "Edit", "NotebookEdit"):
        detail = _clip(get("file_path") or get("notebook_path"))
    elif name == "Grep":
        detail = _clip(" ".join(filter(None, (get("pattern"), get("path") or get("glob")))))
    elif name == "Glob":
        detail = _clip(get("pattern"))
    elif name == "WebFetch":
        detail = _clip(get("url"))
    elif name == "WebSearch":
        detail = _clip(get("query"))
    elif name == "Agent":
        extra["agent_type"] = _clip(get("subagent_type"), 60) or "general-purpose"
        detail = _clip(get("prompt"), 240)
        if get("run_in_background"):
            extra["background"] = True
    elif name == "Workflow":
        script = get("script") if isinstance(get("script"), str) else ""
        match = _WORKFLOW_NAME_RE.search(script[:4000])
        label = label or _clip(get("name"), 80) or (match.group(1) if match else None)
    elif name == "Skill":
        detail = _clip(get("skill"), 80)
    elif name == "AskUserQuestion":
        questions = get("questions")
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            detail = _clip(questions[0].get("question"), 240)
    elif name.startswith("mcp__"):
        # first short string argument says what the call is about
        for value in tool_input.values():
            if isinstance(value, str) and 0 < len(value) <= 200:
                detail = _clip(value, 200)
                break
    return {"label": label, "detail": detail, **extra}


@dataclass
class TranscriptState:
    """Running summary of one transcript file, advanced incrementally."""

    path: Path
    session_id: str
    is_subagent: bool = False
    offset: int = 0
    size: int = -1
    mtime: float = 0.0
    start_cwd: str | None = None
    cwd: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    title: str | None = None
    last_prompt: str | None = None
    tool_counts: dict[str, int] = field(default_factory=dict)
    files_edited: list[str] = field(default_factory=list)
    pending: dict[str, dict] = field(default_factory=dict)  # artifact tool_use id -> op
    resolved: set[str] = field(default_factory=set)
    # live work: tool_use id -> brief, insertion (= start) ordered
    inflight: dict[str, dict] = field(default_factory=dict)
    background: dict[str, dict] = field(default_factory=dict)
    # subagents: the <agent>.meta.json written beside the transcript
    meta: dict = field(default_factory=dict)

    @classmethod
    def for_path(cls, path: Path) -> "TranscriptState":
        is_subagent = _subagents_dir(path) is not None
        return cls(
            path=path,
            session_id=session_id_for(path),
            is_subagent=is_subagent,
            meta=_read_meta(path) if is_subagent else {},
        )

    @property
    def agent_id(self) -> str | None:
        return self.path.stem.removeprefix("agent-") if self.is_subagent else None

    @property
    def workflow_run(self) -> str | None:
        """wf_<id> for workflow-agent transcripts (subagents/workflows/<run>/)."""
        parent = self.path.parent
        return parent.name if self.is_subagent and parent.parent.name == "workflows" else None

    def summary(self) -> dict | None:
        if self.first_ts is None:
            return None
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "start_cwd": self.start_cwd,
            "title": self.title,
            "started_at": self.first_ts,
            "last_activity": self.last_ts,
            "tool_counts": dict(self.tool_counts),
            "files_edited": list(self.files_edited),
            "last_prompt": self.last_prompt,
        }

    def live(self) -> dict:
        """What this transcript has running: copies, safe to hand out."""
        strip = lambda e: {k: v for k, v in e.items() if k != "bg_candidate"}  # noqa: E731
        return {
            "inflight": [strip(e) for e in list(self.inflight.values())],
            "background": [dict(e) for e in list(self.background.values())],
        }

    # ------------------------------------------------------------ advancing

    def advance(self) -> list[dict]:
        """Consume bytes appended since the last call; return newly resolved
        artifact operations. Never raises on unreadable files."""
        try:
            stat = self.path.stat()
        except OSError:
            return []
        if stat.st_size == self.size and stat.st_mtime == self.mtime:
            return []
        if stat.st_size < self.offset:  # rewritten/truncated - start over
            fresh = TranscriptState.for_path(self.path)
            self.__dict__.update(fresh.__dict__)
        self.size, self.mtime = stat.st_size, stat.st_mtime

        ops: list[dict] = []
        tail: deque[bytes] = deque(maxlen=TAIL_LINES)
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                carry = b""
                while True:
                    block = f.read(READ_BLOCK)
                    if not block:
                        break
                    lines = (carry + block).split(b"\n")
                    carry = lines.pop()  # incomplete trailing line - wait for more
                    for raw in lines:
                        self.offset += len(raw) + 1
                        if raw.strip():
                            tail.append(raw)
                            self._consume(raw, ops)
                # an unterminated final line is either mid-write (unparseable -
                # wait for the rest) or a complete record missing its newline
                if carry.strip() and _safe_loads(carry) is not None:
                    self.offset += len(carry)
                    tail.append(carry)
                    self._consume(carry, ops)
        except OSError:
            return ops
        self._refresh_tail(tail)
        return ops

    def _consume(self, raw: bytes, ops: list[dict]) -> None:
        if raw.startswith(_SKIP_PREFIXES):
            return
        if _AI_TITLE in raw:
            # the terminal tab title - rewritten as the session evolves, last wins
            record = _safe_loads(raw)
            if record and record.get("aiTitle"):
                self.title = record["aiTitle"]
            return

        if self.background and _TASK_NOTIFICATION in raw:
            self._notifications(raw)

        need_context = self.first_ts is None or self.start_cwd is None
        is_tool_use = _TOOL_USE in raw
        has_result = _TOOL_RESULT in raw
        # tool_result lines are the huge ones - parse only those resolving a
        # pending artifact call or a launch that may have gone to the
        # background; plain user lines are prompts (small, rare)
        is_prompt = _USER in raw and not has_result
        is_result = (
            has_result
            and bool(self.pending)
            and any(tool_id.encode() in raw for tool_id in self.pending)
        )
        launches: dict[str, dict] = {}
        if has_result and self.inflight:
            for match in _RESULT_ID_RE.finditer(raw):
                tool_id = match.group(1).decode()
                entry = self.inflight.pop(tool_id, None)
                if entry is not None and entry.get("bg_candidate"):
                    launches[tool_id] = entry
        if not (need_context or is_tool_use or is_prompt or is_result or launches):
            return
        record = _safe_loads(raw)
        # subagent transcripts are all sidechain; in a main transcript,
        # sidechain records are (older versions') inlined subagent traffic
        if not record or (record.get("isSidechain") and not self.is_subagent):
            return
        if launches:
            self._background_launches(record, launches)

        ts = record.get("timestamp")
        if ts:
            self.first_ts = self.first_ts or ts
        if record.get("cwd"):
            self.start_cwd = self.start_cwd or record["cwd"]
            self.cwd = record["cwd"]

        kind = record.get("type")
        content = (record.get("message") or {}).get("content")
        if kind == "assistant" and is_tool_use and isinstance(content, list):
            self._tool_uses(record, content)
        elif kind == "user":
            if is_result and isinstance(content, list):
                self._tool_results(record, content, ops)
            elif is_prompt and not record.get("isMeta"):
                text = _message_text(content)
                if text and not text.startswith("<"):
                    self.last_prompt = text[:160]

    def _tool_uses(self, record: dict, content: list) -> None:
        spawn_id = self.meta.get("toolUseId")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if spawn_id and block.get("id") == spawn_id:
                continue  # a fork's copy of the parent's Agent call that spawned it
            name = block.get("name", "?")
            self.tool_counts[name] = self.tool_counts.get(name, 0) + 1
            tool_input = block.get("input") or {}
            self._track(block.get("id"), name, tool_input, record.get("timestamp"))
            if name in FILE_EDIT_TOOLS:
                file_path = tool_input.get("file_path")
                if file_path and file_path not in self.files_edited:
                    self.files_edited.append(file_path)
            elif name in ARTIFACT_TOOLS:
                tool_id = block.get("id")
                if tool_id and tool_id not in self.resolved:
                    self.pending[tool_id] = {
                        "tool_use_id": tool_id,
                        "tool": name,
                        "input": tool_input,
                        "ts": record.get("timestamp"),
                        "cwd": record.get("cwd"),
                        "session_id": self.session_id,
                    }

    # ------------------------------------------------------------ live work

    def _track(self, tool_id: str | None, name: str, tool_input: dict, ts: str | None) -> None:
        if not tool_id:
            return
        brief = tool_brief(name, tool_input)
        self.inflight[tool_id] = {
            "id": tool_id,
            "tool": name,
            **brief,
            "ts": ts,
            "bg_candidate": name in _BACKGROUND_TOOLS or (name in _SHELL_TOOLS and bool(brief.get("background"))),
        }
        while len(self.inflight) > MAX_INFLIGHT:  # results never written (crash, kill)
            self.inflight.pop(next(iter(self.inflight)))

    def _background_launches(self, record: dict, launches: dict[str, dict]) -> None:
        """Resolved calls that may have started background work: keep the ones
        whose result says so, keyed by tool id, until a notification ends them."""
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            entry = launches.get(block.get("tool_use_id"))
            if entry is None or block.get("is_error"):
                continue
            text = _result_text(block.get("content"))
            task_id = output_file = None
            if entry["tool"] in _SHELL_TOOLS:
                match = _BG_TASK_RE.search(text)
                task_id = match.group(1) if match else None
                found = _BG_OUTPUT_RE.search(text)
                output_file = found.group(1) if found else None
            elif entry["tool"] == "Agent":
                if "launched" in text.lower():  # a finished foreground agent also reports its id
                    match = _AGENT_ID_RE.search(text)
                    task_id = match.group(1) if match else None
            else:  # Monitor, Workflow
                match = _TASK_ID_RE.search(text)
                task_id = (match.group(1) or match.group(2)) if match else None
                run = _WORKFLOW_RUN_RE.search(text)
                if run:
                    entry = {**entry, "workflow_run": run.group(1)}
            if not task_id:
                continue
            self.background[entry["id"]] = {
                **{k: v for k, v in entry.items() if k != "bg_candidate"},
                "task_id": task_id,
                "output_file": output_file,
                "launched_at": record.get("timestamp") or entry.get("ts"),
            }
            while len(self.background) > MAX_BACKGROUND:
                self.background.pop(next(iter(self.background)))

    def _notifications(self, raw: bytes) -> None:
        """<task-notification> with a final status ends a background entry,
        matched by tool-use id or task id. Read straight from the bytes."""
        for block in _NOTIFICATION_RE.finditer(raw):
            fields = {key: value for key, value in _NOTIFY_FIELD_RE.findall(block.group(1))}
            if fields.get(b"status") not in _FINAL_STATUSES:
                continue
            tool_id = fields.get(b"tool-use-id", b"").decode(errors="replace")
            if tool_id in self.background:
                del self.background[tool_id]
                continue
            task_id = fields.get(b"task-id", b"").decode(errors="replace")
            for key, entry in list(self.background.items()):
                if task_id and entry.get("task_id") == task_id:
                    del self.background[key]

    def _tool_results(self, record: dict, content: list, ops: list[dict]) -> None:
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            op = self.pending.pop(block.get("tool_use_id"), None)
            if op is None:
                continue
            self.resolved.add(op["tool_use_id"])
            if block.get("is_error"):
                continue  # a refused/failed publish changed nothing
            op["result"] = _result_text(block.get("content"))
            op["ts"] = op["ts"] or record.get("timestamp")
            op["cwd"] = op["cwd"] or record.get("cwd") or self.cwd
            ops.append(op)

    def _refresh_tail(self, tail: deque[bytes]) -> None:
        """The newest timestamp and cwd come from the end of what was read."""
        found_ts = found_cwd = False
        for raw in reversed(tail):
            if raw.startswith(_SKIP_PREFIXES):
                continue
            record = _safe_loads(raw)
            if not record:
                continue
            if not found_ts and record.get("timestamp"):
                self.last_ts = record["timestamp"]
                found_ts = True
            if not found_cwd and record.get("cwd") and not record.get("isSidechain"):
                self.cwd = record["cwd"]
                found_cwd = True
            if found_ts and found_cwd:
                break


def parse_transcript(path: Path) -> dict | None:
    """Summarize one session transcript from scratch. None if unreadable/empty."""
    state = TranscriptState.for_path(path)
    state.advance()
    return state.summary()


def find_transcript(session_id: str) -> Path | None:
    """Locate a session transcript by id across all project slugs."""
    if not SESSION_ID_RE.match(session_id) or not CLAUDE_PROJECTS_DIR.is_dir():
        return None
    for path in CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"):
        return path
    return None


def _conversation_lines(path: Path) -> list[bytes]:
    """All lines of a small transcript; head + tail windows of a huge one."""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size <= CONVERSATION_HEAD_BYTES + CONVERSATION_TAIL_BYTES:
            return f.read().split(b"\n")
        head = f.read(CONVERSATION_HEAD_BYTES).split(b"\n")[:-1]  # drop cut line
        f.seek(size - CONVERSATION_TAIL_BYTES)
        tail = f.read().split(b"\n")[1:]  # first line is cut mid-record
        return head + [b'{"type":"gap"}'] + tail


def extract_conversation(path: Path) -> str:
    """The user/assistant dialogue of a session as plain text for summarization.

    Tool calls, tool results, and harness-injected content (system reminders,
    command output - anything starting with '<') are skipped. Long messages are
    truncated individually; if the whole thing still exceeds the cap, the middle
    is dropped so the opening ask and the ending state both survive.
    """
    try:
        lines = _conversation_lines(path)
    except OSError:
        return ""

    turns: list[str] = []
    for line in lines:
        if b'"type"' not in line or line.startswith(_SKIP_PREFIXES):
            continue
        if line == b'{"type":"gap"}':
            turns.append("[... middle of session omitted ...]")
            continue
        if b'"type":"user"' not in line and b'"type":"assistant"' not in line:
            continue
        record = _safe_loads(line)
        if not record or record.get("type") not in ("user", "assistant"):
            continue
        if record.get("isSidechain") or record.get("isMeta"):
            continue  # subagent traffic / injected skill text
        text = _message_text((record.get("message") or {}).get("content"))
        if not text or text.startswith("<"):
            continue
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS] + " [...truncated]"
        role = "USER" if record.get("type") == "user" else "ASSISTANT"
        turns.append(f"{role}: {text}")

    conversation = "\n\n".join(turns)
    encoded = conversation.encode("utf-8")
    if len(encoded) > MAX_CONVERSATION_BYTES:
        head = encoded[: MAX_CONVERSATION_BYTES // 3].decode("utf-8", errors="replace")
        tail = encoded[-2 * MAX_CONVERSATION_BYTES // 3 :].decode("utf-8", errors="replace")
        conversation = f"{head}\n\n[... middle of conversation omitted ...]\n\n{tail}"
    return conversation


def iter_transcripts(since: datetime | None = None) -> list[Path]:
    """Main, subagent and workflow-agent transcripts, newest-modified first."""
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return []
    cutoff = since.timestamp() if since else 0
    stamped: list[tuple[float, Path]] = []
    for pattern in ("*/*.jsonl", "*/*/subagents/**/*.jsonl"):
        for path in CLAUDE_PROJECTS_DIR.glob(pattern):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                stamped.append((mtime, path))
    stamped.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in stamped]


def recent_sessions(days: int = 3, limit: int = 30) -> list[dict]:
    """Sessions with transcript activity in the last `days`, newest first.
    Stateless one-shot parse - the live server uses `watchtower.sessions`."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = []
    for path in iter_transcripts(cutoff):
        if _subagents_dir(path) is not None:
            continue
        summary = parse_transcript(path)
        if summary and summary["tool_counts"]:  # skip empty/agent stub sessions
            sessions.append(summary)
        if len(sessions) >= limit:
            break
    return sessions
