import json
from datetime import datetime, timezone
from pathlib import Path

from watchtower import api, live_processes as lp
from watchtower.scanners.transcript_parser import TranscriptState, tool_brief


def _write(path: Path, records: list[dict], mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _use(ts, tool_id, name, tool_input, sidechain=False):
    record = {
        "type": "assistant", "timestamp": ts, "cwd": "C:\\w",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]},
    }
    if sidechain:
        record["isSidechain"] = True
    return record


def _result(ts, tool_id, text, error=False, id_last=False, sidechain=False):
    block = {"tool_use_id": tool_id, "type": "tool_result", "content": text}
    if id_last:  # error results put the id after the content
        block = {"type": "tool_result", "content": text, "is_error": True, "tool_use_id": tool_id}
    elif error:
        block["is_error"] = True
    record = {"type": "user", "timestamp": ts, "cwd": "C:\\w", "message": {"role": "user", "content": [block]}}
    if sidechain:
        record["isSidechain"] = True
    return record


def _notification(ts, tool_id, task_id, status="completed"):
    text = (f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>{tool_id}</tool-use-id>\n"
            f"<status>{status}</status>\n</task-notification>")
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


# ------------------------------------------------------------------ transcript tracking

def test_inflight_tracks_calls_until_their_result(tmp_path: Path):
    path = tmp_path / "s1.jsonl"
    _write(path, [
        _use("2026-11-14T10:00:00Z", "toolu_a", "Bash", {"command": "dotnet test", "description": "Run tests"}),
        _use("2026-11-14T10:00:01Z", "toolu_b", "Read", {"file_path": "C:\\x.cs"}),
        _use("2026-11-14T10:00:02Z", "toolu_c", "PowerShell", {"command": "git status"}),
    ])
    state = TranscriptState.for_path(path)
    state.advance()
    inflight = state.live()["inflight"]
    assert [e["id"] for e in inflight] == ["toolu_a", "toolu_b", "toolu_c"]
    assert inflight[0]["label"] == "Run tests" and inflight[0]["detail"] == "dotnet test"
    assert "bg_candidate" not in inflight[0]

    _write(path, [
        _result("2026-11-14T10:00:05Z", "toolu_b", "file text"),
        _result("2026-11-14T10:00:06Z", "toolu_c", "fatal: dubious ownership", id_last=True),
    ], mode="a")
    state.advance()
    assert [e["id"] for e in state.live()["inflight"]] == ["toolu_a"]


def test_id_quoted_in_some_output_does_not_resolve(tmp_path: Path):
    path = tmp_path / "s1.jsonl"
    quoted = json.dumps({"tool_use_id": "toolu_a", "type": "tool_result"}, separators=(",", ":"))
    _write(path, [
        _use("2026-11-14T10:00:00Z", "toolu_a", "Bash", {"command": "sleep 100"}),
        _use("2026-11-14T10:00:01Z", "toolu_b", "Bash", {"command": "cat transcript.jsonl"}),
        _result("2026-11-14T10:00:02Z", "toolu_b", quoted),  # toolu_b's output mentions toolu_a
    ])
    state = TranscriptState.for_path(path)
    state.advance()
    assert [e["id"] for e in state.live()["inflight"]] == ["toolu_a"]


def test_background_task_lives_until_its_notification(tmp_path: Path):
    path = tmp_path / "s1.jsonl"
    out = "C:\\Users\\me\\AppData\\Local\\Temp\\claude\\x\\s1\\tasks\\b4x9q2.output"
    _write(path, [
        _use("2026-11-14T10:00:00Z", "toolu_bg", "Bash", {"command": "pytest", "description": "Full suite", "run_in_background": True}),
        _result("2026-11-14T10:00:01Z", "toolu_bg", f"Command running in background with ID: b4x9q2. Output is being written to: {out}. You will be notified"),
        _use("2026-11-14T10:00:02Z", "toolu_mon", "Monitor", {"command": "tail -f log", "description": "watch log"}),
        _result("2026-11-14T10:00:03Z", "toolu_mon", "Monitor started (task bm7k2, expires in 30m)"),
        _use("2026-11-14T10:00:04Z", "toolu_fg", "Agent", {"description": "Explore", "prompt": "look"}),
        _result("2026-11-14T10:00:09Z", "toolu_fg", "done. agentId: a1b2c3 (use SendMessage to continue)"),
    ])
    state = TranscriptState.for_path(path)
    state.advance()
    background = {e["id"]: e for e in state.live()["background"]}
    assert set(background) == {"toolu_bg", "toolu_mon"}  # a finished foreground agent is not background
    assert background["toolu_bg"]["task_id"] == "b4x9q2"
    assert background["toolu_bg"]["output_file"] == out
    assert background["toolu_mon"]["task_id"] == "bm7k2"
    assert state.live()["inflight"] == []

    _write(path, [_notification("2026-11-14T10:05:00Z", "toolu_bg", "b4x9q2", "running")], mode="a")
    state.advance()
    assert len(state.live()["background"]) == 2  # not a final status
    _write(path, [
        _notification("2026-11-14T10:06:00Z", "toolu_bg", "b4x9q2", "completed"),
        _notification("2026-11-14T10:06:01Z", "", "bm7k2", "killed"),  # matched by task id
    ], mode="a")
    state.advance()
    assert state.live()["background"] == []


def test_subagent_transcripts_track_their_own_sidechain_calls(tmp_path: Path):
    sub = tmp_path / "proj" / "s1" / "subagents" / "agent-af0rk.jsonl"
    sub.parent.mkdir(parents=True)
    sub.with_name("agent-af0rk.meta.json").write_text(json.dumps({
        "agentType": "fork", "isFork": True, "description": "Build CA1", "toolUseId": "toolu_spawn",
        "requestShape": "background", "secret": "dropped",
    }))
    _write(sub, [
        _use("2026-11-14T10:00:00Z", "toolu_spawn", "Agent", {"description": "Build CA1"}, sidechain=True),  # parent's copy
        _use("2026-11-14T10:00:05Z", "toolu_s1", "Bash", {"command": "dotnet build"}, sidechain=True),
    ])
    state = TranscriptState.for_path(sub)
    state.advance()
    assert state.is_subagent and state.session_id == "s1" and state.agent_id == "af0rk"
    assert state.meta == {"agentType": "fork", "isFork": True, "description": "Build CA1",
                          "toolUseId": "toolu_spawn", "requestShape": "background"}
    assert [e["id"] for e in state.live()["inflight"]] == ["toolu_s1"]
    assert state.tool_counts == {"Bash": 1}


def test_main_transcript_still_ignores_inlined_sidechain_records(tmp_path: Path):
    path = tmp_path / "s1.jsonl"
    _write(path, [_use("2026-11-14T10:00:00Z", "toolu_x", "Bash", {"command": "ls"}, sidechain=True)])
    state = TranscriptState.for_path(path)
    state.advance()
    assert state.live()["inflight"] == [] and state.tool_counts == {}


def test_workflow_agents_know_their_run(tmp_path: Path):
    path = tmp_path / "proj" / "s1" / "subagents" / "workflows" / "wf_abc" / "agent-a1.jsonl"
    _write(path, [_use("2026-11-14T10:00:00Z", "toolu_w", "Grep", {"pattern": "x"}, sidechain=True)])
    state = TranscriptState.for_path(path)
    assert state.workflow_run == "wf_abc" and state.session_id == "s1"


def test_tool_brief_is_bounded_and_specific():
    brief = tool_brief("Bash", {"command": "x" * 5000, "description": "Long one", "run_in_background": True})
    assert brief["label"] == "Long one" and len(brief["detail"]) == 400 and brief["background"] is True
    assert tool_brief("Read", {"file_path": "C:\\a.py"})["detail"] == "C:\\a.py"
    assert tool_brief("Agent", {"subagent_type": "Explore", "prompt": "p"})["agent_type"] == "Explore"
    assert tool_brief("Workflow", {"script": "export const meta = { name: 'review-changes' }"})["label"] == "review-changes"
    assert tool_brief("AskUserQuestion", {"questions": [{"question": "Which one?"}]})["detail"] == "Which one?"
    assert tool_brief("mcp__x__y", {"big": "z" * 1000, "query": "find it"})["detail"] == "find it"


# ------------------------------------------------------------------ classification

def test_classify_command_picks_the_most_specific_kind():
    assert lp.classify_command("cd x && timeout 500 dotnet test a.csproj --no-build")[0] == "test"
    assert lp.classify_command("python -I build/mutants.py export")[0] == "test"
    assert lp.classify_command("dotnet build a.csproj -c Debug")[0] == "build"
    assert lp.classify_command("npm run build")[0] == "build"
    assert lp.classify_command(".venv/Scripts/python.exe -m watchtower")[0] == "script"
    assert lp.classify_command("ssh pi@cerberus-hub 'journalctl -u x'") == ("network", "ssh")
    assert lp.classify_command("gh pr checks 42")[0] == "network"
    assert lp.classify_command("git status --short") == ("shell", "git")
    assert lp.classify_command("npm install three")[1] == "install"
    assert lp.classify_command("uvicorn app:app --reload")[0] == "service"
    assert lp.classify_command("ls -la")[0] == "shell"


def test_classify_tool():
    assert lp.classify_tool("Agent") == ("agent", "agent")
    assert lp.classify_tool("AskUserQuestion")[0] == "waiting"
    assert lp.classify_tool("WebFetch") == ("network", "web")
    assert lp.classify_tool("mcp__claude_ai_Gmail__search_threads") == ("network", "mcp")
    assert lp.classify_tool("mcp__serena__find_symbol") == ("tool", "serena")
    assert lp.classify_tool("Edit")[0] == "tool"
    assert lp.classify_tool("Bash", "pytest -q")[0] == "test"


# ------------------------------------------------------------------ commands

def _bash(command: str, stdin_null: bool = True) -> list[str]:
    quoted = command.replace("'", "'\"'\"'")
    redirect = " < /dev/null" if stdin_null else ""
    return ["C:\\Program Files\\Git\\bin\\bash.exe", "-c",
            f"source /c/x/shell-snapshots/snapshot-bash-1.sh 2>/dev/null || true && eval '{quoted}'{redirect}"
            " && pwd -P >| /c/tmp/claude-1-cwd"]


def test_shell_command_unwraps_claudes_bash_wrapper():
    assert lp.shell_command(_bash("dotnet test 'a b.csproj'")) == "dotnet test 'a b.csproj'"
    heredoc = "python - <<'EOF'\nprint(\"' < /dev/null\")\nEOF"
    assert lp.shell_command(_bash(heredoc, stdin_null=False)) == heredoc
    assert lp.is_tool_shell(_bash("ls"))
    pwsh = ["pwsh.exe", "-Command", "$s = $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT; Invoke-Expression $s > C:\\t\\claude\\x\\tasks\\bq1.output"]
    assert lp.is_tool_shell(pwsh) and lp.shell_command(pwsh) is None
    assert lp.task_id_of(pwsh) == "bq1"
    assert not lp.is_tool_shell(["node", "C:\\tools\\comfyui-mcp\\dist\\index.js"])
    assert lp.service_label("node.exe", ["node", "C:\\tools\\comfyui-mcp\\dist\\index.js"]) == "comfyui-mcp"
    assert lp.service_label("serena.exe", ["serena", "start-mcp-server", "--context", "x"]) == "serena"


# ------------------------------------------------------------------ registry

def test_read_registry_reads_json_only_and_keeps_known_fields(tmp_path: Path):
    (tmp_path / "1.json").write_text(json.dumps({
        "pid": 1, "sessionId": "s1", "status": "busy", "messagingSocketPath": "\\\\.\\pipe\\x", "procStart": "1",
    }))
    (tmp_path / "1.abc.key").write_text(json.dumps({"peerToken": "secret"}))
    (tmp_path / "2.json").write_text("{not json")
    (tmp_path / "3.json").write_text(json.dumps({"pid": "3", "sessionId": "s3"}))
    entries = lp.read_registry(tmp_path)
    assert len(entries) == 1
    assert entries[0]["pid"] == 1 and entries[0]["status"] == "busy"
    assert "messagingSocketPath" not in entries[0]


def test_live_entries_rejects_reused_pids():
    started = 1_794_000_000.0
    filetime = str(int((started + lp.FILETIME_EPOCH_OFFSET) * 1e7))
    table = {10: lp.Proc(10, 1, "claude.exe", started), 11: lp.Proc(11, 1, "notepad.exe", started + 999)}
    entries = [{"pid": 10, "sessionId": "a", "procStart": filetime},
               {"pid": 11, "sessionId": "b", "procStart": filetime},
               {"pid": 12, "sessionId": "c", "procStart": filetime}]
    assert [e["sessionId"] for e, _ in lp.live_entries(entries, table)] == ["a"]


def test_transcript_owner_prefers_the_busy_process():
    entries = [({"pid": 1, "sessionId": "s", "status": "idle", "statusUpdatedAt": 9}, None),
               ({"pid": 2, "sessionId": "s", "status": "busy", "statusUpdatedAt": 1}, None)]
    assert lp.transcript_owner(entries) == {"s": 2}


# ------------------------------------------------------------------ output tails

def test_tail_output_reads_only_task_outputs_under_claude_temp(tmp_path: Path):
    root = tmp_path / "claude"
    task = root / "x" / "tasks" / "b1.output"
    task.parent.mkdir(parents=True)
    task.write_text("build...\n\x1b[32mPassed!\x1b[0m  - Failed: 0, Passed: 412\r\n\n", encoding="utf-8")
    assert lp.tail_output(str(task), root=root) == ["build...", "Passed!  - Failed: 0, Passed: 412"]
    outside = tmp_path / "secrets.output"
    outside.write_text("nope")
    assert lp.tail_output(str(outside), root=root) == []
    wrong = root / "x" / "tasks" / "b1.txt"
    wrong.write_text("nope")
    assert lp.tail_output(str(wrong), root=root) == []
    assert lp.tail_output(None, root=root) == []


# ------------------------------------------------------------------ joining

def _shell(pid, cmd, created, task_id=None):
    return {"tree": {"pid": pid, "cmd": cmd, "children": []}, "task_id": task_id, "created": created}


def _shell_op(op_id, detail, ts, tool="Bash", task_id=None):
    return {"id": op_id, "tool": tool, "detail": detail, "_ts": ts, "task_id": task_id, "processes": []}


def test_match_shells_by_task_then_text_then_time():
    bg = _shell_op("bg", "pytest", 100, task_id="b1")
    by_text = _shell_op("t", "dotnet test a.csproj", 50)
    by_time = _shell_op("p", None, 200, tool="PowerShell")
    shells = [
        _shell(1, None, 101, task_id="b1"),
        _shell(2, "dotnet test a.csproj", 300),  # started after both foreground calls: text decides
        _shell(3, None, 201),
        _shell(4, "ls", 50),  # nothing left to claim it
    ]
    rest = lp.match_shells(shells, [by_text, by_time, bg])
    assert [p["pid"] for p in bg["processes"]] == [1]
    assert [p["pid"] for p in by_text["processes"]] == [2]
    assert [p["pid"] for p in by_time["processes"]] == [3]
    assert [s["tree"]["pid"] for s in rest] == [4]


class _FakeProcs:
    cpu_count = 4

    def __init__(self, cmdlines):
        self.cmdlines = cmdlines

    def cmdline(self, proc):
        return self.cmdlines.get(proc.pid, [])

    def usage(self, proc):
        return (1.0, 10.0)


def _state(tmp_path: Path, name: str, records: list[dict], meta: dict | None = None) -> TranscriptState:
    path = tmp_path / "proj" / name
    _write(path, records)
    if meta is not None:
        path.with_name(path.stem + ".meta.json").write_text(json.dumps(meta))
    state = TranscriptState.for_path(path)
    state.advance()
    return state


def test_session_view_builds_lanes_and_attaches_processes(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).isoformat()
    main = _state(tmp_path, "s1.jsonl", [
        _use(now_iso, "toolu_agent", "Agent", {"description": "Build CA1", "subagent_type": "fork"}),
        _use(now_iso, "toolu_q", "AskUserQuestion", {"questions": [{"question": "Ship it?"}]}),
    ])
    sub = _state(tmp_path, "s1/subagents/agent-af1.jsonl",
                 [_use(now_iso, "toolu_t", "Bash", {"command": "dotnet test x.csproj", "description": "Run tests"}, sidechain=True)],
                 meta={"agentType": "fork", "description": "Build CA1", "toolUseId": "toolu_agent"})
    testhost = lp.Proc(4, 3, "testhost.exe", 1.0)
    dotnet = lp.Proc(3, 2, "dotnet.exe", 1.0, [testhost])
    bash = lp.Proc(2, 1, "bash.exe", 1.0, [dotnet, lp.Proc(9, 2, "conhost.exe", 1.0)])
    mcp = lp.Proc(5, 1, "node.exe", 1.0, [lp.Proc(6, 5, "msedgewebview2.exe", 1.0)])
    claude = lp.Proc(1, 0, "claude.exe", 1.0, [bash, mcp])
    procs = _FakeProcs({
        2: _bash("dotnet test x.csproj"),
        3: ["C:\\dotnet\\dotnet.exe", "test", "x.csproj"],
        4: ["testhost.exe", "--port", "1"],
        5: ["node", "C:\\tools\\comfyui-mcp\\dist\\index.js"],
    })
    entry = {"pid": 1, "sessionId": "s1", "status": "busy", "name": "paytable-6e", "startedAt": 0}
    view = lp.session_view(entry, claude, [main, sub], procs, datetime.now(timezone.utc).timestamp())

    main_lane, fork_lane = view["lanes"]
    assert [o["tool"] for o in main_lane["ops"]] == ["AskUserQuestion"]  # the Agent call is the fork's lane
    assert fork_lane["kind"] == "fork" and fork_lane["label"] == "Build CA1"
    (op,) = fork_lane["ops"]
    assert op["category"] == "test" and op["label"] == "Run tests"
    (tree,) = op["processes"]
    assert tree["pid"] == 2 and tree["cmd"] == "dotnet test x.csproj"
    assert [c["name"] for c in tree["children"]] == ["dotnet.exe"]  # conhost hidden
    assert tree["children"][0]["children"][0]["name"] == "testhost.exe"
    assert tree["total_mem_mb"] == 30.0
    assert view["services"] == [{"pid": 5, "label": "comfyui-mcp", "mem_mb": 20.0, "cpu": 1.0}]
    assert api.process_state(view, None, datetime.now(timezone.utc)) == "waiting"


def test_process_state_maps_registry_status():
    base = {"lanes": [{"ops": []}], "background": [], "last_activity": None}
    now = datetime.now(timezone.utc)
    assert api.process_state({**base, "status": "busy"}, None, now) == "working"
    assert api.process_state({**base, "status": "idle"}, None, now) == "idle"
    assert api.process_state({**base, "status": "shell"}, None, now) == "shell"
    assert api.process_state({**base, "status": "idle", "background": [{"id": "b"}]}, None, now) == "background"
    assert api.process_state({**base, "status": "awaiting_permission"}, None, now) == "waiting"
