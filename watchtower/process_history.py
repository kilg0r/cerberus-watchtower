"""Recent history for the Processes tab.

Two sources, because neither covers everything:

- **Samples** recorded at each `/api/processes` snapshot: host and Claude
  CPU/RAM, per-process CPU/RAM and per-session state. Exact, but they only
  exist while someone has the page open - the charts show gaps for the rest.
- **Hook events** (`events.jsonl`), which cover every session all the time:
  a prompt starts a turn, Stop ends it, a permission Notification means the
  session is waiting on you.

The activity timeline bins both: a bin the page watched uses the recorded
states, any other bin falls back to what the hooks imply. Hooks can't see a
permission being granted for a non-edit tool, so a hook-only "waiting" can
run long until the turn's next event - samples fix that wherever they exist.
"""

import json
import threading
import time
from collections import deque
from datetime import datetime

from . import events

WINDOW_S = 60 * 60
BIN_S = 30
MIN_SPACING_S = 2.0  # two open windows polling every 3s still record ~one sample per poll
SPARK_SAMPLES = 40  # per-process sparkline length (two minutes at 3s polls)

# one character per bin; a bin shows the most important state it saw
CODES = {"waiting": "!", "working": "w", "shell": "w", "background": "b", "idle": "."}
PRIORITY = "!wb."


def _best(codes) -> str:
    return min(codes, key=PRIORITY.index, default=".")


def _unix(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def hook_state(event: dict) -> str | None:
    """The state a hook event puts its session in, or None for no change.
    Mirrors `api.session_state`: any Notification that isn't the post-turn
    idle ping is a prompt waiting on you."""
    name = event.get("event")
    if name in ("UserPromptSubmit", "PostToolUse"):
        return "working"
    if name == "Notification":
        return "idle" if events.is_idle_notification(event) else "waiting"
    if name == "Stop":
        return "idle"
    return None  # SessionStart also fires on compaction mid-turn; SubagentStop says nothing about the main turn


class HookLog:
    """State transitions from the hook log over the last window, read
    incrementally (only whole lines, so a half-written append is read next
    time rather than lost)."""

    def __init__(self, window_s: int = WINDOW_S):
        self.window_s = window_s
        self.position: int | None = None
        self.transitions: deque[tuple[float, str, str]] = deque()  # (t, session_id, state)
        self.before: dict[str, str] = {}  # session_id -> state as of the window start

    def _read(self) -> list[bytes]:
        path = events.EVENTS_FILE
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if self.position is None or size < self.position:  # first read, or the log was truncated
            self.transitions.clear()
            self.before.clear()
            start = max(0, size - events.STATE_TAIL_BYTES)
            skip_partial = start > 0
        else:
            start, skip_partial = self.position, False
        if size <= start:
            self.position = size
            return []
        try:
            with path.open("rb") as f:
                f.seek(start)
                chunk = f.read(size - start)
        except OSError:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:
            self.position = start
            return []
        self.position = start + end + 1
        lines = chunk[:end].split(b"\n")
        return lines[1:] if skip_partial else lines

    def update(self, now: float) -> None:
        for line in self._read():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict) or not event.get("session_id"):
                continue
            state = hook_state(event)
            t = _unix(event.get("ts"))
            if state and t is not None:
                self.transitions.append((t, event["session_id"], state))
        cutoff = now - self.window_s
        while self.transitions and self.transitions[0][0] < cutoff:
            _, session_id, state = self.transitions.popleft()
            self.before[session_id] = state

    def bins(self, session_id: str, start: float, count: int, bin_s: int) -> list[str]:
        moves = sorted((t, s) for t, sid, s in self.transitions if sid == session_id)
        state = self.before.get(session_id, "idle")
        i, out = 0, []
        for b in range(count):
            end = start + (b + 1) * bin_s
            seen = {CODES[state]}
            while i < len(moves) and moves[i][0] < end:
                state = moves[i][1]
                seen.add(CODES[state])
                i += 1
            out.append(_best(seen))
        return out


class ProcessHistory:
    def __init__(self, window_s: int = WINDOW_S, bin_s: int = BIN_S):
        self.window_s = window_s
        self.bin_s = bin_s
        self._lock = threading.Lock()
        self.samples: deque[dict] = deque()
        self.sampled_bins: dict[int, dict[str, str]] = {}  # absolute bin -> session_id -> code
        self.hooks = HookLog(window_s)
        self.warm = False

    def record(self, snap: dict, now: float | None = None) -> bool:
        """Keep one sample of a finished snapshot (sessions carry `state`).
        Returns whether its resource readings were kept."""
        now = time.time() if now is None else now
        with self._lock:
            # the timeline needs every poll's states; resources keep a minimum spacing
            seen = self.sampled_bins.setdefault(int(now // self.bin_s), {})
            for view in snap["sessions"]:
                code = CODES.get(view.get("state"), "w")
                seen[view["session_id"]] = _best({code, seen.get(view["session_id"], ".")})
            if not self.warm:
                # psutil's CPU readings measure between calls - the first is always 0
                self.warm = True
                return False
            if self.samples and now - self.samples[-1]["t"] < MIN_SPACING_S:
                return False
            totals = snap["totals"]
            self.samples.append({
                "t": now,
                "host_cpu": snap["host"]["cpu"],
                "host_mem_gb": snap["host"]["mem_used_gb"],
                "claude_cpu": totals["claude_cpu"],
                "claude_mem_gb": totals["claude_mem_gb"],
                "procs": {v["pid"]: (v["cpu"], v["mem_mb"]) for v in snap["sessions"]},
            })
            cutoff = now - self.window_s
            while self.samples and self.samples[0]["t"] < cutoff:
                self.samples.popleft()
            first_bin = int(cutoff // self.bin_s)
            for key in [k for k in self.sampled_bins if k < first_bin]:
                del self.sampled_bins[key]
            return True

    def trend(self) -> dict:
        """Host vs Claude CPU and memory over the window, oldest first."""
        with self._lock:
            samples = list(self.samples)
        return {
            "t": [round(s["t"] * 1000) for s in samples],
            "host_cpu": [s["host_cpu"] for s in samples],
            "host_mem_gb": [s["host_mem_gb"] for s in samples],
            "claude_cpu": [s["claude_cpu"] for s in samples],
            "claude_mem_gb": [s["claude_mem_gb"] for s in samples],
        }

    def sparks(self, pids: list[int]) -> dict[int, dict]:
        """The last few CPU/RAM readings of each live process."""
        with self._lock:
            recent = list(self.samples)[-SPARK_SAMPLES:]
        out = {}
        for pid in pids:
            readings = [s["procs"][pid] for s in recent if pid in s["procs"]]
            out[pid] = {"cpu": [r[0] for r in readings], "mem": [r[1] for r in readings]}
        return out

    def timeline(self, session_ids: set[str], now: float | None = None) -> dict:
        """One code per bin per session over the window; the last bin holds now."""
        now = time.time() if now is None else now
        count = self.window_s // self.bin_s
        last = int(now // self.bin_s)
        first = last - count + 1
        with self._lock:
            self.hooks.update(now)
            sampled = [self.sampled_bins.get(b) for b in range(first, last + 1)]
            rows = {}
            for session_id in session_ids:
                hooked = self.hooks.bins(session_id, first * self.bin_s, count, self.bin_s)
                rows[session_id] = "".join(
                    (bins.get(session_id, ".") if bins else hooked[i]) for i, bins in enumerate(sampled)
                )
        return {"start": first * self.bin_s * 1000, "bin_s": self.bin_s, "sessions": rows}


def totals(snap: dict) -> dict:
    """What all Claude processes add up to: the CLI itself, the processes its
    tool calls started, and the MCP servers / helpers it keeps alive."""
    cli = sum(v["claude_mem_mb"] for v in snap["sessions"])
    tools = sum(v["mem_mb"] - v["claude_mem_mb"] for v in snap["sessions"])
    mcp = sum(s["mem_mb"] for v in snap["sessions"] for s in v["services"])
    cpu = sum(v["cpu"] for v in snap["sessions"]) + sum(s["cpu"] for v in snap["sessions"] for s in v["services"])
    return {
        "cli_mb": round(cli, 1),
        "tools_mb": round(max(tools, 0), 1),
        "mcp_mb": round(mcp, 1),
        "claude_mem_gb": round((cli + max(tools, 0) + mcp) / 1024, 2),
        "claude_cpu": round(cpu, 1),
    }


history = ProcessHistory()
