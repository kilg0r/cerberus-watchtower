import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watchtower import events, process_history as ph

T0 = 1_800_000_000  # a bin boundary (divisible by 30)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _event(t, name, session="s1", message=None):
    record = {"ts": _iso(t), "event": name, "session_id": session}
    if message:
        record["message"] = message
    return record


@pytest.fixture
def log(tmp_path: Path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(events, "EVENTS_FILE", path)

    def append(*records, partial: str = ""):
        with path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
            f.write(partial)
    return append


def _snap(states: dict[str, str], cpu=1.0, mem=100.0):
    sessions = [
        {"pid": i + 10, "session_id": sid, "state": state, "cpu": cpu, "mem_mb": mem, "claude_mem_mb": mem - 10,
         "services": [{"pid": 99, "label": "serena", "mem_mb": 50.0, "cpu": 0.5}]}
        for i, (sid, state) in enumerate(states.items())
    ]
    snap = {"sessions": sessions, "host": {"cpu": 20.0, "mem_used_gb": 12.0}}
    snap["totals"] = ph.totals(snap)
    return snap


def test_hook_state_mirrors_session_state():
    assert ph.hook_state({"event": "UserPromptSubmit"}) == "working"
    assert ph.hook_state({"event": "PostToolUse"}) == "working"
    assert ph.hook_state({"event": "Stop"}) == "idle"
    assert ph.hook_state({"event": "Notification", "message": "Claude needs your permission"}) == "waiting"
    assert ph.hook_state({"event": "Notification", "message": "Claude is waiting for your input"}) == "idle"
    assert ph.hook_state({"event": "SessionStart"}) is None  # fires on compaction mid-turn too
    assert ph.hook_state({"event": "SubagentStop"}) is None


def test_timeline_bins_from_hook_events(log):
    history = ph.ProcessHistory(window_s=300, bin_s=30)  # 10 bins
    start = T0 - 270  # the first bin; the last one holds T0
    log(
        _event(start - 100, "UserPromptSubmit"),  # before the window: working at its start
        _event(start + 65, "Stop"),               # bin 2
        _event(start + 125, "UserPromptSubmit"),  # bin 4
        _event(start + 130, "Notification", message="Claude needs your permission"),
        _event(start + 200, "PostToolUse"),       # bin 6: granted, back to work
        _event(start + 250, "Stop"),              # bin 8
        _event(start + 10, "UserPromptSubmit", session="other"),
    )
    out = history.timeline({"s1", "unknown"}, now=T0 + 5)
    assert out["start"] == start * 1000 and out["bin_s"] == 30
    assert out["sessions"]["s1"] == "www.!!!ww."
    assert out["sessions"]["unknown"] == ".........."


def test_waiting_wins_its_bin_and_idle_ping_ends_a_turn(log):
    history = ph.ProcessHistory(window_s=90, bin_s=30)
    start = T0 - 60
    log(
        _event(start + 1, "UserPromptSubmit"),
        _event(start + 5, "Notification", message="Claude needs your permission"),
        _event(start + 8, "PostToolUse"),
        _event(start + 40, "Notification", message="Claude is waiting for your input"),
    )
    assert history.timeline({"s1"}, now=T0)["sessions"]["s1"] == "!w."


def test_hook_log_reads_incrementally_and_waits_for_whole_lines(log):
    history = ph.ProcessHistory(window_s=90, bin_s=30)
    start = T0 - 60
    half = json.dumps(_event(start + 35, "UserPromptSubmit"))
    log(_event(start + 1, "UserPromptSubmit"), partial=half[:20])
    assert history.timeline({"s1"}, now=T0)["sessions"]["s1"] == "www"
    log(partial=half[20:] + "\n")  # the rest of the line lands
    log(_event(start + 50, "Stop"))
    assert history.timeline({"s1"}, now=T0)["sessions"]["s1"] == "ww."
    assert len(history.hooks.transitions) == 3  # nothing read twice


def test_old_transitions_roll_into_the_starting_state(log):
    history = ph.ProcessHistory(window_s=60, bin_s=30)
    log(_event(T0 - 500, "Notification", message="Claude needs your permission"))
    assert history.timeline({"s1"}, now=T0)["sessions"]["s1"] == "!!"
    assert not history.hooks.transitions and history.hooks.before == {"s1": "waiting"}


def _warm(history):
    history.warm = True
    return history


def test_first_snapshot_keeps_states_but_not_its_cold_cpu(log):
    history = ph.ProcessHistory(window_s=60, bin_s=30)
    assert history.record(_snap({"s1": "working"}), now=T0 + 1) is False
    assert history.record(_snap({"s1": "working"}), now=T0 + 4) is True
    assert len(history.samples) == 1
    assert history.timeline({"s1"}, now=T0 + 5)["sessions"]["s1"] == ".w"


def test_sampled_bins_override_hooks(log):
    history = _warm(ph.ProcessHistory(window_s=90, bin_s=30))
    start = T0 - 60
    # hooks say waiting all along (the grant was for a Bash call, which logs nothing)
    log(_event(start - 10, "Notification", message="Claude needs your permission"))
    history.record(_snap({"s1": "working"}), now=start + 31)
    history.record(_snap({"s1": "idle"}), now=start + 61)
    history.record(_snap({"s1": "waiting"}), now=start + 62)  # inside min spacing: still counts for the timeline
    assert history.timeline({"s1"}, now=T0 + 1)["sessions"]["s1"] == "!w!"
    assert len(history.samples) == 2


def test_absent_sessions_read_idle_in_sampled_bins(log):
    history = ph.ProcessHistory(window_s=60, bin_s=30)
    log(_event(T0 - 100, "UserPromptSubmit"))
    history.record(_snap({"other": "working"}), now=T0 + 1)
    assert history.timeline({"s1"}, now=T0 + 2)["sessions"]["s1"] == "w."


def test_trend_sparks_and_pruning():
    history = _warm(ph.ProcessHistory(window_s=60, bin_s=30))
    for i in range(5):
        history.record(_snap({"s1": "working"}, cpu=float(i), mem=100.0 + i), now=T0 + i * 20)
    trend = history.trend()
    assert trend["t"] == [(T0 + 20) * 1000, (T0 + 40) * 1000, (T0 + 60) * 1000, (T0 + 80) * 1000]
    assert trend["host_mem_gb"] == [12.0] * 4
    assert trend["claude_cpu"] == [1.5, 2.5, 3.5, 4.5]  # session + its MCP server
    assert history.sparks([10, 404]) == {10: {"cpu": [1.0, 2.0, 3.0, 4.0], "mem": [101.0, 102.0, 103.0, 104.0]},
                                          404: {"cpu": [], "mem": []}}
    assert min(history.sampled_bins) >= (T0 + 20) // 30


def test_totals_split_cli_tools_and_mcp():
    totals = ph.totals(_snap({"a": "working", "b": "idle"}, cpu=2.0, mem=200.0))
    assert totals == {"cli_mb": 380.0, "tools_mb": 20.0, "mcp_mb": 100.0, "claude_mem_gb": 0.49, "claude_cpu": 5.0}
