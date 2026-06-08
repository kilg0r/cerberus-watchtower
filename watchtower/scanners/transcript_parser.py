"""Claude Code transcript parser - session history for the activity feed.

Transcripts live at ~/.claude/projects/<project-slug>/<session-id>.jsonl, one
JSON object per line. We extract per-session summaries: project, time range,
tools used, files edited, last prompt. Substring pre-filters keep json.loads
off the hot path for lines we don't care about.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
FILE_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
MAX_TRANSCRIPT_BYTES = 60 * 1024 * 1024
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_MESSAGE_CHARS = 1500
MAX_CONVERSATION_BYTES = 60 * 1024


def _safe_loads(line: str) -> dict | None:
    try:
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def parse_transcript(path: Path) -> dict | None:
    """Summarize one session transcript. Returns None for unreadable files."""
    try:
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    cwd = None
    first_ts = last_ts = None
    tool_counts: dict[str, int] = {}
    files_edited: list[str] = []
    last_prompt = None
    title = None

    for line in content.splitlines():
        # the terminal tab title - an "ai-title" record with no timestamp,
        # rewritten as the session evolves, so the last one wins
        if '"aiTitle"' in line:
            record = _safe_loads(line)
            if record and record.get("aiTitle"):
                title = record["aiTitle"]
            continue

        if first_ts is None or cwd is None or '"timestamp"' in line:
            record = _safe_loads(line) if ('"timestamp"' in line or cwd is None) else None
            if record:
                ts = record.get("timestamp")
                if ts:
                    first_ts = first_ts or ts
                    last_ts = ts
                cwd = cwd or record.get("cwd")

                if record.get("type") == "user" and '"tool_result"' not in line:
                    message = record.get("message") or {}
                    text = message.get("content")
                    if isinstance(text, str) and text.strip() and not text.startswith("<"):
                        last_prompt = text.strip()[:160]

                if '"tool_use"' in line:
                    for block in (record.get("message") or {}).get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "?")
                            tool_counts[name] = tool_counts.get(name, 0) + 1
                            if name in FILE_EDIT_TOOLS:
                                file_path = (block.get("input") or {}).get("file_path")
                                if file_path and file_path not in files_edited:
                                    files_edited.append(file_path)

    if first_ts is None:
        return None
    return {
        "session_id": path.stem,
        "cwd": cwd,
        "title": title,
        "started_at": first_ts,
        "last_activity": last_ts,
        "tool_counts": tool_counts,
        "files_edited": files_edited,
        "last_prompt": last_prompt,
    }


def find_transcript(session_id: str) -> Path | None:
    """Locate a session transcript by id across all project slugs."""
    if not SESSION_ID_RE.match(session_id) or not CLAUDE_PROJECTS_DIR.is_dir():
        return None
    for path in CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"):
        return path
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


def extract_conversation(path: Path) -> str:
    """The user/assistant dialogue of a session as plain text for summarization.

    Tool calls, tool results, and harness-injected content (system reminders,
    command output - anything starting with '<') are skipped. Long messages are
    truncated individually; if the whole thing still exceeds the cap, the middle
    is dropped so the opening ask and the ending state both survive.
    """
    try:
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    turns: list[str] = []
    for line in content.splitlines():
        if '"type"' not in line:
            continue
        record = _safe_loads(line)
        if not record or record.get("type") not in ("user", "assistant"):
            continue
        if record.get("isSidechain"):
            continue  # subagent traffic - not part of the main conversation
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


def recent_sessions(days: int = 3, limit: int = 30) -> list[dict]:
    """Sessions with transcript activity in the last `days`, newest first."""
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    candidates = [
        p
        for p in CLAUDE_PROJECTS_DIR.glob("*/*.jsonl")
        if p.stat().st_mtime >= cutoff
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    sessions = []
    for path in candidates[:limit]:
        summary = parse_transcript(path)
        if summary and summary["tool_counts"]:  # skip empty/agent stub sessions
            sessions.append(summary)
    return sessions
