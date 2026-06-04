"""Claude Code hook -> Watchtower event emitter.

Registered in ~/.claude/settings.json for SessionStart / PostToolUse / Stop /
SubagentStop. Reads the hook payload from stdin and appends one JSON line to
~/.cerberus-watchtower/events.jsonl.

Design constraints: stdlib only, append-and-exit, and NEVER fail - a hook that
errors or blocks would degrade every Claude Code session on this machine.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVENTS_FILE = Path.home() / ".cerberus-watchtower" / "events.jsonl"


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "tool_name": payload.get("tool_name"),
        "file_path": tool_input.get("file_path"),
        "message": payload.get("message"),  # Notification events carry text
    }
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in event.items() if v is not None}) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break a session over telemetry
    sys.exit(0)
