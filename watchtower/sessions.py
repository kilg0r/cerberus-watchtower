"""Live session index - incremental transcript scanning for the whole server.

One `TranscriptState` per transcript file (main sessions and their subagents)
is kept in memory and advanced every few seconds by a background loop, so a
600 MB transcript is read once and then only its appended tail. The same pass
feeds the Activity view (session summaries) and the artifact index (resolved
publish/edit operations, handed to `on_ops`).

Summaries are read without locking: summary() only copies plain dicts/lists,
which is atomic under the GIL, and a momentarily half-advanced summary is
harmless for a dashboard.
"""

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .scanners.transcript_parser import TranscriptState, iter_transcripts

log = logging.getLogger("watchtower.sessions")
REFRESH_SECONDS = 5


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class SessionIndex:
    def __init__(self) -> None:
        self._states: dict[Path, TranscriptState] = {}
        self._refresh_lock = threading.Lock()
        self.passes = 0  # completed full refresh passes

    @property
    def ready(self) -> bool:
        return self.passes > 0

    def refresh(self) -> list[dict]:
        """Advance every transcript; return artifact ops resolved this pass."""
        with self._refresh_lock:
            paths = iter_transcripts()
            ops: list[dict] = []
            for path in paths:
                state = self._states.get(path)
                if state is None:
                    state = self._states[path] = TranscriptState.for_path(path)
                ops.extend(state.advance())
            for gone in set(self._states) - set(paths):
                del self._states[gone]
            self.passes += 1
            return ops

    def _states_snapshot(self) -> list[TranscriptState]:
        return list(self._states.values())

    def sessions(self, days: int = 3, limit: int = 40) -> list[dict]:
        """Main sessions active in the last `days`, most recent first, each with
        its subagent rollup."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        subagents: dict[str, dict] = {}
        mains: list[TranscriptState] = []
        for state in self._states_snapshot():
            if state.is_subagent:
                agg = subagents.setdefault(state.session_id, {"count": 0, "last_activity": None})
                agg["count"] += 1
                if state.last_ts and (agg["last_activity"] or "") < state.last_ts:
                    agg["last_activity"] = state.last_ts
            else:
                mains.append(state)

        out = []
        for state in mains:
            summary = state.summary()
            if not summary or not summary["tool_counts"]:
                continue  # empty / stub sessions
            sub = subagents.get(state.session_id) or {"count": 0, "last_activity": None}
            latest = max(filter(None, (parse_iso(summary["last_activity"]), parse_iso(sub["last_activity"]))), default=None)
            if latest is None or latest < cutoff:
                continue
            summary["subagents"] = sub
            summary["latest_activity"] = latest.isoformat()
            out.append(summary)
        out.sort(key=lambda s: s["latest_activity"], reverse=True)
        return out[:limit]

    def summary_for(self, session_id: str) -> dict | None:
        for state in self._states_snapshot():
            if not state.is_subagent and state.session_id == session_id:
                return state.summary()
        return None

    def titles(self, session_ids: set[str]) -> dict[str, str | None]:
        return {
            state.session_id: state.title
            for state in self._states_snapshot()
            if not state.is_subagent and state.session_id in session_ids
        }


index = SessionIndex()


async def run(target: SessionIndex, on_ops: Callable[[list[dict]], object]) -> None:
    """Background loop: advance transcripts, hand resolved artifact ops on."""
    while True:
        try:
            ops = await asyncio.to_thread(target.refresh)
            if ops:
                await asyncio.to_thread(on_ops, ops)
        except asyncio.CancelledError:
            raise
        except Exception:  # never let one bad transcript stop the index
            log.exception("session index refresh failed")
        await asyncio.sleep(REFRESH_SECONDS)
