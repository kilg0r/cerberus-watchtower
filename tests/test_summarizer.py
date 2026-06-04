from pathlib import Path

import pytest

from watchtower.ai import summarizer
from watchtower.ai.summarizer import (
    SummarizerError,
    collect_diff,
    diff_hash,
    get_or_create_session_summary,
    get_or_create_summary,
)


def test_collect_diff_includes_tracked_and_untracked(temp_git_repo: Path):
    diff = collect_diff(temp_git_repo)
    assert "+print('world')" in diff
    assert "untracked file: new_module.py" in diff
    assert "VALUE = 1" in diff


def test_cache_miss_runs_claude_then_hit_skips_it(session, monkeypatch):
    calls = []

    def fake_run_claude(diff: str) -> str:
        calls.append(diff)
        return "Adds a world print and a new module."

    monkeypatch.setattr(summarizer, "run_claude", fake_run_claude)
    diff = "fake diff content"

    first = get_or_create_summary(session, "test", diff)
    assert first["cached"] is False
    assert first["summary"] == "Adds a world print and a new module."
    assert first["diff_hash"] == diff_hash(diff)
    assert len(calls) == 1

    second = get_or_create_summary(session, "test", diff)
    assert second["cached"] is True
    assert second["summary"] == first["summary"]
    assert len(calls) == 1  # no second claude run


def test_different_diff_busts_cache(session, monkeypatch):
    monkeypatch.setattr(summarizer, "run_claude", lambda diff: f"summary of {diff}")

    first = get_or_create_summary(session, "test", "diff one")
    second = get_or_create_summary(session, "test", "diff two")
    assert first["diff_hash"] != second["diff_hash"]
    assert second["cached"] is False


def test_session_summary_caches_by_content_then_regenerates_on_growth(session, monkeypatch):
    calls = []

    def fake_run_claude(text: str, prompt: str = "") -> str:
        calls.append(text)
        return f"summary #{len(calls)}"

    monkeypatch.setattr(summarizer, "run_claude", fake_run_claude)

    first = get_or_create_session_summary(session, "sess-1", "USER: hi")
    assert first["cached"] is False
    assert len(calls) == 1

    # same conversation -> cache hit, no claude run
    second = get_or_create_session_summary(session, "sess-1", "USER: hi")
    assert second["cached"] is True
    assert second["summary"] == first["summary"]
    assert len(calls) == 1

    # session grew -> new hash -> regenerate
    third = get_or_create_session_summary(session, "sess-1", "USER: hi\n\nASSISTANT: hello")
    assert third["cached"] is False
    assert len(calls) == 2


def test_claude_failure_is_not_cached(session, monkeypatch):
    attempts = []

    def flaky(diff: str) -> str:
        attempts.append(diff)
        if len(attempts) == 1:
            raise SummarizerError("claude exited 1")
        return "recovered summary"

    monkeypatch.setattr(summarizer, "run_claude", flaky)

    with pytest.raises(SummarizerError):
        get_or_create_summary(session, "test", "some diff")

    result = get_or_create_summary(session, "test", "some diff")
    assert result["cached"] is False  # failure was not cached; claude ran again
    assert result["summary"] == "recovered summary"
