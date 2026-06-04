"""Claude Code transcript parser - session history for the activity feed.

Transcripts live at ~/.claude/projects/<project-slug>/<session-id>.jsonl, one
JSON object per line. We extract per-session summaries: project, time range,
tools used, files edited, last prompt. Substring pre-filters keep json.loads
off the hot path for lines we don't care about.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
FILE_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
MAX_TRANSCRIPT_BYTES = 60 * 1024 * 1024


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

    for line in content.splitlines():
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
        "started_at": first_ts,
        "last_activity": last_ts,
        "tool_counts": tool_counts,
        "files_edited": files_edited,
        "last_prompt": last_prompt,
    }


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
