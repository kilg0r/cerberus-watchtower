"""Claude Code hook -> Watchtower event emitter.

Registered in ~/.claude/settings.json for SessionStart / UserPromptSubmit /
PostToolUse / Notification / Stop / SubagentStop. Reads the hook payload from
stdin and appends one JSON line to ~/.cerberus-watchtower/events.jsonl.

PostToolUse covers file edits plus artifact publishing (the built-in Artifact
tool, claude-artifacts publish/edit, Claude Docs create/update) so publishes
show up in the live feed; the full artifact record is indexed separately from
the transcript.

Design constraints: stdlib only, append-and-exit, and NEVER fail - a hook that
errors or blocks would degrade every Claude Code session on this machine.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

EVENTS_FILE = Path.home() / ".cerberus-watchtower" / "events.jsonl"
ARTIFACT_TOOL_PREFIXES = ("mcp__claude-artifacts__", "mcp__claude_ai_Claude_Docs__")
ARTIFACT_URL_RE = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9-]+")
MAX_RESPONSE_SCAN = 256 * 1024


def _artifact_fields(tool_name: str, tool_input: dict, payload: dict) -> dict:
    if tool_name != "Artifact" and not tool_name.startswith(ARTIFACT_TOOL_PREFIXES):
        return {}
    response = json.dumps(payload.get("tool_response"), ensure_ascii=False)[:MAX_RESPONSE_SCAN]
    match = ARTIFACT_URL_RE.search(response)
    container = tool_input.get("container") if isinstance(tool_input.get("container"), dict) else {}
    create = container.get("create") if isinstance(container.get("create"), dict) else {}
    title = tool_input.get("title") or create.get("name")
    if not title and tool_input.get("file_path"):
        title = Path(tool_input["file_path"]).name
    return {
        "artifact_url": match.group(0) if match else None,
        "artifact_title": title,
        "artifact_label": tool_input.get("label"),
    }


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    tool_name = payload.get("tool_name") or ""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "tool_name": tool_name or None,
        "file_path": tool_input.get("file_path"),
        "message": payload.get("message"),  # Notification events carry text
    }
    try:
        event.update(_artifact_fields(tool_name, tool_input, payload))
    except Exception:
        pass  # artifact details are a nicety; the base event still lands
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in event.items() if v is not None}) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break a session over telemetry
    sys.exit(0)
