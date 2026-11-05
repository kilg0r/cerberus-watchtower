import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchtower import events
from watchtower.scanners.transcript_parser import (
    TranscriptState,
    extract_conversation,
    parse_transcript,
)


def _write_lines(path: Path, records: list[dict], mode: str = "w") -> None:
    with path.open(mode, encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _assistant_tool_use(ts: str, tool_id: str, name: str, tool_input: dict, cwd: str = "C:\\w") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "cwd": cwd,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def _tool_result(ts: str, tool_id: str, text: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}]}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "timestamp": ts, "cwd": "C:\\w", "message": {"role": "user", "content": [block]}}


def _transcript_lines() -> list[dict]:
    return [
        {
            "type": "user",
            "timestamp": "2026-06-03T10:00:00.000Z",
            "cwd": "C:\\Users\\aigor\\_dev\\_paytable\\payTableDotnet",
            "message": {"role": "user", "content": "add tip splitting to orders"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-06-03T10:01:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "C:\\repo\\OrderHandler.cs"},
                    },
                    {"type": "tool_use", "name": "Bash", "input": {"command": "dotnet test"}},
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-06-03T10:05:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "C:\\repo\\OrderHandler.cs"},
                    }
                ],
            },
        },
    ]


def test_parse_transcript_summarizes_session(tmp_path: Path):
    transcript = tmp_path / "abc-123.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line, separators=(",", ":")) for line in _transcript_lines()), encoding="utf-8"
    )

    summary = parse_transcript(transcript)
    assert summary is not None
    assert summary["session_id"] == "abc-123"
    assert summary["cwd"].endswith("payTableDotnet")
    assert summary["started_at"] == "2026-06-03T10:00:00.000Z"
    assert summary["last_activity"] == "2026-06-03T10:05:00.000Z"
    assert summary["tool_counts"] == {"Edit": 2, "Bash": 1}
    assert summary["files_edited"] == ["C:\\repo\\OrderHandler.cs"]  # deduped
    assert summary["last_prompt"] == "add tip splitting to orders"


def test_api_request_blob_duplicates_are_not_counted(tmp_path: Path):
    transcript = tmp_path / "dup.jsonl"
    real = _assistant_tool_use("2026-06-03T10:00:00Z", "toolu_1", "Edit", {"file_path": "C:\\a.py"})
    blob = {"type": "api-request-blob", "sessionId": "dup", "message": real["message"]}
    _write_lines(transcript, [real, blob])

    summary = parse_transcript(transcript)
    assert summary["tool_counts"] == {"Edit": 1}


def test_advance_is_incremental_and_tracks_latest_cwd(tmp_path: Path):
    transcript = tmp_path / "inc.jsonl"
    _write_lines(transcript, [_assistant_tool_use("2026-06-03T10:00:00Z", "t1", "Bash", {}, cwd="C:\\root")])
    state = TranscriptState.for_path(transcript)
    state.advance()
    assert state.tool_counts == {"Bash": 1}
    assert state.cwd == state.start_cwd == "C:\\root"

    # a partial line (no trailing newline yet) must wait for the rest
    with transcript.open("a", encoding="utf-8") as f:
        f.write('{"type":"assistant"')
    state.advance()
    assert state.tool_counts == {"Bash": 1}

    with transcript.open("a", encoding="utf-8") as f:
        rest = _assistant_tool_use("2026-06-03T11:00:00Z", "t2", "Bash", {}, cwd="C:\\root\\repo")
        f.write(json.dumps(rest, separators=(",", ":"))[len('{"type":"assistant"'):] + "\n")
    state.advance()
    assert state.tool_counts == {"Bash": 2}
    assert state.cwd == "C:\\root\\repo"
    assert state.start_cwd == "C:\\root"
    assert state.last_ts == "2026-06-03T11:00:00Z"


def test_advance_resolves_artifact_ops_and_skips_errors(tmp_path: Path):
    transcript = tmp_path / "art.jsonl"
    url = "https://claude.ai/code/artifact/964b295e-48b6-4d9b-8636-e8b287068f08"
    _write_lines(
        transcript,
        [
            _assistant_tool_use("2026-06-03T10:00:00Z", "a1", "Artifact", {"file_path": "C:\\s\\plan.md"}),
            _tool_result("2026-06-03T10:00:05Z", "a1", f"Published C:\\s\\plan.md at {url}  more"),
            _assistant_tool_use("2026-06-03T10:01:00Z", "a2", "mcp__claude-artifacts__publish_version", {"content": "x"}),
            _tool_result("2026-06-03T10:01:05Z", "a2", "refused", is_error=True),
        ],
    )
    state = TranscriptState.for_path(transcript)
    ops = state.advance()
    assert [op["tool_use_id"] for op in ops] == ["a1"]
    assert url in ops[0]["result"]
    assert ops[0]["session_id"] == "art"
    assert not state.pending


def test_subagent_transcripts_belong_to_parent_session(tmp_path: Path):
    sub = tmp_path / "parent-session" / "subagents" / "agent-abc.jsonl"
    sub.parent.mkdir(parents=True)
    _write_lines(sub, [_assistant_tool_use("2026-06-03T10:00:00Z", "t1", "Read", {})])
    state = TranscriptState.for_path(sub)
    assert state.session_id == "parent-session"
    assert state.is_subagent

    # workflow agents nest two levels deeper
    wf = tmp_path / "parent-session" / "subagents" / "workflows" / "wf_92552f71-e17" / "agent-x.jsonl"
    state = TranscriptState.for_path(wf)
    assert (state.session_id, state.is_subagent) == ("parent-session", True)
    assert not TranscriptState.for_path(tmp_path / "main-session.jsonl").is_subagent


def test_parse_transcript_handles_garbage(tmp_path: Path):
    transcript = tmp_path / "bad.jsonl"
    transcript.write_text("not json\n{\"half\":", encoding="utf-8")
    assert parse_transcript(transcript) is None


def test_extract_conversation_keeps_dialogue_drops_noise(tmp_path: Path):
    lines = [
        {
            "type": "user",
            "timestamp": "2026-06-03T10:00:00.000Z",
            "message": {"role": "user", "content": "add tip splitting to orders"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-06-03T10:01:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll add tip splitting to the order handler."},
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "C:\\x.cs"}},
                ],
            },
        },
        {  # tool result wrapped as a user message - must be skipped
            "type": "user",
            "timestamp": "2026-06-03T10:02:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "file edited ok"}],
            },
        },
        {  # harness-injected system reminder - must be skipped
            "type": "user",
            "timestamp": "2026-06-03T10:03:00.000Z",
            "message": {"role": "user", "content": "<system-reminder>noise</system-reminder>"},
        },
        {  # subagent traffic - must be skipped
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-06-03T10:04:00.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "sidechain"}]},
        },
    ]
    transcript = tmp_path / "conv.jsonl"
    transcript.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in lines), encoding="utf-8")

    conversation = extract_conversation(transcript)
    assert "USER: add tip splitting to orders" in conversation
    assert "ASSISTANT: I'll add tip splitting" in conversation
    assert "file edited ok" not in conversation
    assert "system-reminder" not in conversation
    assert "sidechain" not in conversation


def test_extract_conversation_truncates_long_messages(tmp_path: Path):
    lines = [
        {
            "type": "user",
            "timestamp": "2026-06-03T10:00:00.000Z",
            "message": {"role": "user", "content": "x" * 5000},
        }
    ]
    transcript = tmp_path / "long.jsonl"
    transcript.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in lines), encoding="utf-8")

    conversation = extract_conversation(transcript)
    assert "[...truncated]" in conversation
    assert len(conversation) < 2000


def test_read_recent_events_tails_newest_first(tmp_path: Path, monkeypatch):
    log = tmp_path / "events.jsonl"
    records = [
        {"ts": f"2026-06-03T10:00:0{i}+00:00", "event": "PostToolUse", "session_id": f"s{i}"}
        for i in range(5)
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    monkeypatch.setattr(events, "EVENTS_FILE", log)

    recent = events.read_recent_events(limit=3)
    assert [e["session_id"] for e in recent] == ["s4", "s3", "s2"]


def test_read_recent_events_missing_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(events, "EVENTS_FILE", tmp_path / "nope.jsonl")
    assert events.read_recent_events() == []


def test_latest_by_session_sees_past_the_recent_window(tmp_path: Path, monkeypatch):
    log = tmp_path / "events.jsonl"
    records = [{"ts": "2026-06-03T09:00:00+00:00", "event": "Notification", "session_id": "quiet"}]
    records += [
        {"ts": f"2026-06-03T10:{i // 60:02d}:{i % 60:02d}+00:00", "event": "PostToolUse", "session_id": "busy"}
        for i in range(300)
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    monkeypatch.setattr(events, "EVENTS_FILE", log)

    assert "quiet" not in {e["session_id"] for e in events.read_recent_events(limit=100)}
    latest = events.latest_by_session()
    assert latest["quiet"]["event"] == "Notification"
    assert latest["busy"]["ts"].startswith("2026-06-03T10:04:59")


def test_session_state_model():
    from watchtower.api import session_state

    now = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    ago = lambda **kw: now - timedelta(**kw)  # noqa: E731

    def event(name, when, message=None):
        e = {"event": name, "ts": when.isoformat()}
        if message:
            e["message"] = message
        return e

    permission = event("Notification", ago(minutes=3), "Claude needs your permission to use Bash")
    idle_ping = event("Notification", ago(minutes=3), "Claude is waiting for your input")

    assert session_state(ago(minutes=4), permission, now) == "waiting"
    # the 60s idle ping after a finished turn is "done", not "needs input"
    assert session_state(ago(minutes=4), idle_ping, now) == "done"
    assert session_state(ago(minutes=4), event("Stop", ago(minutes=4)), now) == "done"
    # permission granted, a non-edit tool is running: transcript moved on
    assert session_state(ago(seconds=10), permission, now) == "working"
    assert session_state(ago(seconds=20), event("UserPromptSubmit", ago(minutes=1)), now) == "working"
    # no hook event at all, but a live transcript
    assert session_state(ago(minutes=1), None, now) == "working"
    assert session_state(ago(hours=3), event("Stop", ago(hours=3)), now) == "idle"
    assert session_state(None, None, now) == "idle"


def test_session_projects_defers_root_cwd_to_edited_repo(monkeypatch):
    from watchtower import config
    from watchtower.api import _repo_table, _session_projects
    from watchtower.config import RepoConfig

    repos = (
        RepoConfig(id="dotnet", name="payTableDotnet", path=Path(r"C:\dev\_paytable\payTableDotnet"), stack="dotnet", group="p"),
        RepoConfig(id="vue", name="payTableVue", path=Path(r"C:\dev\_paytable\payTableVue"), stack="vue", group="p"),
    )
    monkeypatch.setattr(config, "registry", lambda: repos)
    summary = {
        "cwd": r"C:\dev\_paytable",
        "start_cwd": r"C:\dev\_paytable",
        "files_edited": [
            r"C:\dev\_paytable\payTableVue\src\a.vue",
            r"C:\dev\_paytable\payTableDotnet\Api\X.cs",
            r"C:\dev\_paytable\payTableDotnet\Api\Y.cs",
        ],
    }
    label = _session_projects(summary, None, _repo_table())
    assert label["repo_id"] == "dotnet"
    assert [r["repo_id"] for r in label["repos"]] == ["dotnet", "vue"]

    # a cwd inside a repo wins outright
    summary["cwd"] = r"C:\dev\_paytable\payTableVue"
    assert _session_projects(summary, None, _repo_table())["repo_id"] == "vue"


def test_project_label_respects_path_boundaries(monkeypatch):
    from watchtower import config
    from watchtower.api import _project_label
    from watchtower.config import RepoConfig

    repos = (
        RepoConfig(
            id="cerberus",
            name="Cerberus (Financial)",
            path=Path(r"C:\Users\aigor\_dev\_cerberus\cerberus"),
            stack="python",
            group="cerberus",
        ),
        RepoConfig(
            id="cerberus-watchtower",
            name="Watchtower",
            path=Path(r"C:\Users\aigor\_dev\_cerberus\cerberus-watchtower"),
            stack="python",
            group="cerberus",
        ),
    )
    monkeypatch.setattr(config, "registry", lambda: repos)

    # sibling repo sharing a name prefix must not shadow the real repo
    label = _project_label(r"C:\Users\aigor\_dev\_cerberus\cerberus-watchtower")
    assert label == {"project": "Watchtower", "repo_id": "cerberus-watchtower"}

    # subdirectories still map to the containing repo
    label = _project_label(r"C:\Users\aigor\_dev\_cerberus\cerberus\src")
    assert label == {"project": "Cerberus (Financial)", "repo_id": "cerberus"}

    # unknown cwd falls back to the directory name
    label = _project_label(r"C:\Users\aigor\_dev\elsewhere\thing")
    assert label == {"project": "thing", "repo_id": None}
