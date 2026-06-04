"""Windows toast notifications for agent session lifecycle.

Tails the hook event log and raises a toast when:
  - a session is waiting on the user (Notification events - permission /
    proceed prompts, idle warnings), or
  - a session finishes a long-running turn (Stop after >= MIN_TURN_SECONDS
    since that session's last UserPromptSubmit).

Short conversational turns never toast - the threshold exists so actively
chatting in a session doesn't spam the notification center. Disable entirely
with WATCHTOWER_NOTIFY=0.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

from . import events
from .config import PROJECT_ROOT

MIN_TURN_SECONDS = 45
COOLDOWN_SECONDS = 10
DASHBOARD_URL = "http://127.0.0.1:8765/activity"
ICON = PROJECT_ROOT / "assets" / "icon.ico"

try:
    from winotify import Notification as _Toast
except ImportError:  # non-Windows / dep missing - notifier becomes a no-op
    _Toast = None


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _project_of(event: dict) -> str:
    cwd = (event.get("cwd") or "").replace("/", "\\").rstrip("\\")
    return cwd.rsplit("\\", 1)[-1] or "unknown"


class NotificationDecider:
    """Pure decision logic - returns a toast payload or None per event."""

    def __init__(
        self,
        min_turn_seconds: int = MIN_TURN_SECONDS,
        cooldown_seconds: int = COOLDOWN_SECONDS,
    ):
        self.min_turn = timedelta(seconds=min_turn_seconds)
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self._last_prompt: dict[str, datetime] = {}
        self._last_toast: dict[str, datetime] = {}

    def _cooled_down(self, session_id: str, ts: datetime) -> bool:
        last = self._last_toast.get(session_id)
        return last is None or ts - last >= self.cooldown

    def decide(self, event: dict) -> dict | None:
        kind = event.get("event")
        session_id = event.get("session_id") or "?"
        ts = _parse_ts(event.get("ts"))
        if ts is None:
            return None

        if kind == "UserPromptSubmit":
            self._last_prompt[session_id] = ts
            return None

        if kind == "Notification":
            if not self._cooled_down(session_id, ts):
                return None
            self._last_toast[session_id] = ts
            return {
                "title": f"Waiting for you - {_project_of(event)}",
                "message": event.get("message") or "A session needs your input.",
            }

        if kind == "Stop":
            started = self._last_prompt.pop(session_id, None)
            if started is None or ts - started < self.min_turn:
                return None
            if not self._cooled_down(session_id, ts):
                return None
            self._last_toast[session_id] = ts
            minutes, seconds = divmod(int((ts - started).total_seconds()), 60)
            duration = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
            return {
                "title": f"Session finished - {_project_of(event)}",
                "message": f"Turn completed after {duration}.",
            }

        return None


def _show_toast(payload: dict) -> None:
    if _Toast is None:
        return
    toast = _Toast(
        app_id="Cerberus Watchtower",
        title=payload["title"],
        msg=payload["message"],
        icon=str(ICON) if ICON.is_file() else "",
        launch=DASHBOARD_URL,
    )
    toast.show()


async def run() -> None:
    """Background task: tail the event log, toast on qualifying events."""
    if os.environ.get("WATCHTOWER_NOTIFY", "1") == "0" or _Toast is None:
        return
    decider = NotificationDecider()
    position = events.current_position()  # only react to events after startup
    while True:
        await asyncio.sleep(1)
        try:
            lines, position = events.read_new(position)
            for line in lines:
                try:
                    payload = decider.decide(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if payload:
                    await asyncio.to_thread(_show_toast, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            continue  # never let telemetry kill the notifier loop
