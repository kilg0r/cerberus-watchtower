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


def read_recent_events(limit: int = 100) -> list[dict]:
    """Last `limit` events, newest first."""
    if not EVENTS_FILE.is_file():
        return []
    try:
        with EVENTS_FILE.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    if len(chunk) >= TAIL_BYTES:
        lines = lines[1:]  # first line may be cut mid-record
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(events[-limit:]))


async def stream_events():
    """SSE generator: yields new event lines as they are appended, with
    heartbeats so proxies and EventSource keep the connection alive."""
    position = EVENTS_FILE.stat().st_size if EVENTS_FILE.is_file() else 0
    idle = 0
    while True:
        await asyncio.sleep(1)
        idle += 1
        try:
            size = EVENTS_FILE.stat().st_size if EVENTS_FILE.is_file() else 0
        except OSError:
            size = 0
        if size < position:  # rotated/truncated
            position = 0
        if size > position:
            with EVENTS_FILE.open("rb") as f:
                f.seek(position)
                chunk = f.read().decode("utf-8", errors="replace")
            position = size
            for line in chunk.splitlines():
                if line.strip():
                    yield f"data: {line}\n\n"
            idle = 0
        elif idle >= 15:
            yield ": heartbeat\n\n"
            idle = 0
