import json
from pathlib import Path

from watchtower import events
from watchtower.scanners.transcript_parser import extract_conversation, parse_transcript


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
        "\n".join(json.dumps(line) for line in _transcript_lines()), encoding="utf-8"
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
    transcript.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

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
    transcript.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

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
