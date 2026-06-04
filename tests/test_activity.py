import json
from pathlib import Path

from watchtower import events
from watchtower.scanners.transcript_parser import parse_transcript


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
