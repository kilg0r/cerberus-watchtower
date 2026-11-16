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


def test_run_claude_uses_a_neutral_cwd(monkeypatch, tmp_path):
    """Run from an empty scratch dir, never a repo: a headless session started
    inside a checkout summarizes that checkout instead of stdin."""
    captured = {}

    class _Result:
        returncode = 0
        stdout = "a summary"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, **kwargs)
        return _Result()

    monkeypatch.setattr(summarizer.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(summarizer.subprocess, "run", fake_run)
    monkeypatch.setattr(summarizer, "NEUTRAL_CWD", tmp_path / "scratch")

    assert summarizer.run_claude("the diff") == "a summary"
    assert captured["cwd"] == tmp_path / "scratch"
    assert captured["cwd"].is_dir()  # created on demand
    assert captured["input"] == "the diff"


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


def test_run_claude_isolates_the_headless_run(monkeypatch, tmp_path):
    """Global Stop hooks (the PayTable wrap-up check) block a headless run's
    stop and the reply to the hook came back as the "summary" - every call
    must disable hooks, MCP servers and tools."""
    import subprocess

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="a narrative\n", stderr="")

    monkeypatch.setattr(summarizer.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(summarizer.subprocess, "run", fake_run)
    monkeypatch.setattr(summarizer, "NEUTRAL_CWD", tmp_path / "scratch")
    assert summarizer.run_claude("payTable.Payments structure", prompt="explain") == "a narrative"
    args = calls[0]
    assert args[:3] == ["claude", "-p", "explain"]
    settings = args[args.index("--settings") + 1]
    assert '"disableAllHooks": true' in settings
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert args[args.index("--tools") + 1] == ""
