"""Watchtower event feed - reads and streams the hook-emitted event log.

Events arrive via hooks/emit_event.py appending to
~/.cerberus-watchtower/events.jsonl. We tail the file by size for SSE
streaming; appends are the only mutation, so size growth == new lines.
"""

import asyncio
import json
from pathlib import Path

from .config import DATA_DIR

EVENTS_FILE = DATA_DIR / "events.jsonl"
TAIL_BYTES = 256 * 1024
STATE_TAIL_BYTES = 2 * 1024 * 1024  # ~a day of hook events on a busy day

# Notification hook messages: the idle ping that fires ~60s after a turn ends
# ("Claude is waiting for your input") means the session is finished, not
# blocked - only permission/approval prompts actually need a decision
IDLE_NOTIFICATION_MARKERS = ("waiting for your input",)


def _read_tail(max_bytes: int) -> list[dict]:
    """Events in the last `max_bytes` of the log, oldest first."""
    if not EVENTS_FILE.is_file():
        return []
    try:
        with EVENTS_FILE.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
    except OSError:
        return []
    lines = chunk.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]  # first line may be cut mid-record
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def read_recent_events(limit: int = 100) -> list[dict]:
    """Last `limit` events, newest first."""
    return list(reversed(_read_tail(TAIL_BYTES)[-limit:]))


def is_idle_notification(event: dict) -> bool:
    message = (event.get("message") or "").lower()
    return event.get("event") == "Notification" and any(m in message for m in IDLE_NOTIFICATION_MARKERS)


def latest_by_session(max_bytes: int = STATE_TAIL_BYTES) -> dict[str, dict]:
    """Most recent hook event per session, from a generous tail of the log -
    wide enough that a quiet session isn't pushed out by busy ones."""
    latest: dict[str, dict] = {}
    for event in _read_tail(max_bytes):
        session_id = event.get("session_id")
        if session_id:
            latest[session_id] = event  # oldest-first, so the last write wins
    return latest


def current_position() -> int:
    return EVENTS_FILE.stat().st_size if EVENTS_FILE.is_file() else 0


def read_new(position: int) -> tuple[list[str], int]:
    """New complete lines appended since `position` (handles truncation)."""
    try:
        size = EVENTS_FILE.stat().st_size if EVENTS_FILE.is_file() else 0
    except OSError:
        size = 0
    if size < position:  # rotated/truncated
        position = 0
    if size <= position:
        return [], position
    with EVENTS_FILE.open("rb") as f:
        f.seek(position)
        chunk = f.read().decode("utf-8", errors="replace")
    return [line for line in chunk.splitlines() if line.strip()], size


async def stream_events():
    """SSE generator: yields new event lines as they are appended, with
    heartbeats so proxies and EventSource keep the connection alive."""
    position = current_position()
    idle = 0
    while True:
        await asyncio.sleep(1)
        idle += 1
        lines, position = read_new(position)
        if lines:
            for line in lines:
                yield f"data: {line}\n\n"
            idle = 0
        elif idle >= 15:
            yield ": heartbeat\n\n"
            idle = 0
