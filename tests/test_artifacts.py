import importlib.util
import json
from pathlib import Path

from watchtower import artifacts
from watchtower.models import Artifact, ArtifactOp

PAGE_SLUG = "eda48d34-47b2-4cae-95b0-a3df6f7c0028"
DOC_ID = "32cce8c6-d53b-4b87-9afa-c34ed1def329"
FILE_ID = "964b295e-48b6-4d9b-8636-e8b287068f08"


def _op(tool_use_id, tool, tool_input, result, ts="2026-10-30T17:51:38Z", session_id="s1"):
    return {
        "tool_use_id": tool_use_id,
        "tool": tool,
        "input": tool_input,
        "result": result,
        "ts": ts,
        "cwd": r"C:\Users\aigor\_dev\_cerberus",
        "session_id": session_id,
    }


def _page_result(version, title="Vanta SweatPals Readiness"):
    data = {
        "slug": PAGE_SLUG,
        "title": title,
        "url": "https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH",
        "version": version,
        "read": "owner",
        "favicon": "",
    }
    return json.dumps(data) + " Once this session's watch on this artifact connects, ..."


def test_normalize_builtin_artifact_publish():
    op = artifacts.normalize(
        _op(
            "a1",
            "Artifact",
            {"file_path": r"C:\tmp\plan.md", "description": "SPEI plan", "favicon": "🏦", "label": "v2"},
            rf"Published C:\tmp\plan.md at https://claude.ai/code/artifact/{FILE_ID}  Live subscription: skipped",
        )
    )
    assert op["artifact_id"] == FILE_ID
    assert op["kind"] == "artifact"
    assert op["meta"]["url"].endswith(FILE_ID)
    assert op["content"] == {"mode": "replace_file", "src": r"C:\tmp\plan.md", "extra": {}}
    assert op["label"] == "v2"


def test_canonical_id_decodes_short_ids_and_urls():
    # the /artifact/<short> form is the uuid in base58
    assert artifacts.canonical_id("HE7qxaH3fqc9FghckY8bE8") == "83671afb-8245-461b-9c5c-c7bb9f6c7969"
    assert artifacts.canonical_id("https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH") == PAGE_SLUG
    assert artifacts.canonical_id(f"https://claude.ai/code/artifact/{FILE_ID.upper()}") == FILE_ID
    assert artifacts.canonical_id("not an id!") is None
    assert artifacts.canonical_id(None) is None


def test_normalize_builtin_publish_short_url_with_version_and_files():
    op = artifacts.normalize(
        _op(
            "a2",
            "Artifact",
            {
                "action": "publish",
                "url": "https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH",
                "file_path": r"C:\tmp\site\index.html",
                "files": {"plan.js": r"C:\tmp\site\plan.js"},
            },
            r"Published C:\tmp\site\index.html at https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH "
            "(Version 150, version id 1793391446-5b1d)  Live subscription: already connected",
        )
    )
    assert op["artifact_id"] == PAGE_SLUG  # same artifact the MCP calls eda48d34-...
    assert op["meta"]["version"] == "1793391446-5b1d"
    assert op["content"]["extra"] == {"plan.js": r"C:\tmp\site\plan.js"}


def test_builtin_multi_file_publish_copies_siblings(session, tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Catalog Blueprint</title><script src=plan.js></script>", encoding="utf-8")
    (site / "plan.js").write_text("var plan = 1", encoding="utf-8")
    store = tmp_path / "store"
    op = _op(
        "a3",
        "Artifact",
        {"file_path": str(site / "index.html"), "files": {"plan.js": str(site / "plan.js")}},
        f"Published {site / 'index.html'} at https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH (Version 3)",
    )
    artifacts.ingest(session, [op], store_dir=store)
    art = session.get(Artifact, PAGE_SLUG)
    assert (art.title, art.content_file, art.content_status, art.version) == (
        "Catalog Blueprint",
        "index.html",
        "ok",
        "Version 3",
    )
    assert artifacts.content_path(PAGE_SLUG, "plan.js", store_dir=store) is not None


def test_normalize_artifact_created_from_type():
    op = artifacts.normalize(
        _op(
            "t1",
            "Artifact",
            {"action": "publish", "title": "Sales deck", "type_url": "https://claude.ai/artifact/Rp9naXUCj2xozpUkyQy19W"},
            "Created a new Artifact at https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH (version 1793488978-abf0) "
            "from the Artifact type https://claude.ai/artifact/Rp9naXUCj2xozpUkyQy19W (release 1793486466-e4f4).",
        )
    )
    assert (op["artifact_id"], op["meta"]["title"], op["meta"]["version"]) == (PAGE_SLUG, "Sales deck", "1793488978-abf0")
    assert op["content"] is None


def test_normalize_ignores_reads_and_refusals():
    read = _op("r1", "Artifact", {"action": "read", "url": "x"}, "[Artifact 53e1... owned by you]")
    assert artifacts.normalize(read) is None
    opened = _op("r4", "Artifact", {"action": "open"}, "Opened the Artifact at https://claude.ai/artifact/7GqYyRUM1tRKwJcHGiw5SG for the user.")
    assert artifacts.normalize(opened) is None
    refused = _op("r2", "mcp__claude-artifacts__publish_version", {"content": "<p>"}, "conflict: not live")
    assert artifacts.normalize(refused) is None
    denied = _op("r3", "mcp__claude_ai_Claude_Docs__batch", {"container": {"kind": "project", "id": DOC_ID}}, '{"verdict":"deny"}')
    assert artifacts.normalize(denied) is None


def test_normalize_docs_create_and_update():
    create = artifacts.normalize(
        _op(
            "d1",
            "mcp__claude_ai_Claude_Docs__batch",
            {"container": {"kind": "project", "create": {"name": "Pearson Fence - Statement of Work"}}},
            f'{{"verdict":"allow","acks":[{{"minted":"{DOC_ID}","url":"https://claude.ai/code/artifact/{DOC_ID}"}}]}}',
        )
    )
    assert (create["artifact_id"], create["kind"], create["action"]) == (DOC_ID, "doc", "publish")
    assert create["meta"]["title"] == "Pearson Fence - Statement of Work"

    update = artifacts.normalize(
        _op("d2", "mcp__claude_ai_Claude_Docs__update", {"container": {"kind": "project", "id": DOC_ID}}, '{"verdict":"allow"}')
    )
    assert (update["artifact_id"], update["action"]) == (DOC_ID, "edit")


def test_ingest_replays_page_publish_and_edits(session, tmp_path: Path):
    ops = [
        _op(
            "p1",
            "mcp__claude-artifacts__publish_version",
            {"title": "Vanta SweatPals Readiness", "content": "<html><body><h1>Draft</h1></body></html>"},
            _page_result("v1"),
            ts="2026-10-30T17:51:38Z",
        ),
        _op(
            "p2",
            "mcp__claude-artifacts__edit_version",
            {"slug": PAGE_SLUG, "label": "Match questions", "edits": [{"old_text": "Draft", "new_text": "Final"}]},
            _page_result("v2"),
            ts="2026-10-30T18:38:52Z",
        ),
    ]
    # out-of-order arrival (e.g. two sessions) must still replay chronologically
    assert artifacts.ingest(session, list(reversed(ops)), store_dir=tmp_path) == 2

    art = session.get(Artifact, PAGE_SLUG)
    assert art.kind == "artifact" and art.version == "v2" and art.op_count == 2
    assert art.content_status == "ok"
    page = artifacts.content_dir(PAGE_SLUG, tmp_path) / "index.html"
    assert "Final" in page.read_text(encoding="utf-8")

    # rescans are idempotent
    assert artifacts.ingest(session, ops, store_dir=tmp_path) == 0
    assert session.query(ArtifactOp).count() == 2


def test_failed_edit_replay_marks_copy_stale(session, tmp_path: Path):
    ops = [
        _op("p1", "mcp__claude-artifacts__publish_version", {"content": "<p>one</p>"}, _page_result("v1")),
        _op(
            "p2",
            "mcp__claude-artifacts__edit_version",
            {"slug": PAGE_SLUG, "edits": [{"old_text": "not there", "new_text": "x"}]},
            _page_result("v2"),
            ts="2026-10-30T18:00:00Z",
        ),
    ]
    artifacts.ingest(session, ops, store_dir=tmp_path)
    assert session.get(Artifact, PAGE_SLUG).content_status == "stale"


def test_builtin_artifact_copies_source_and_titles_from_heading(session, tmp_path: Path):
    source = tmp_path / "scratch" / "plan.md"
    source.parent.mkdir()
    source.write_text("# SPEI bank transfer plan\n\nBody", encoding="utf-8")
    store = tmp_path / "store"
    op = _op(
        "a1",
        "Artifact",
        {"file_path": str(source), "description": "plan"},
        f"Published {source} at https://claude.ai/code/artifact/{FILE_ID}",
    )
    artifacts.ingest(session, [op], store_dir=store)

    art = session.get(Artifact, FILE_ID)
    assert art.title == "SPEI bank transfer plan"
    assert art.content_file == "plan.md" and art.content_status == "ok"
    assert artifacts.content_path(FILE_ID, "plan.md", store_dir=store).read_text(encoding="utf-8").startswith("# SPEI")


def test_builtin_artifact_with_missing_source(session, tmp_path: Path):
    op = _op(
        "a1",
        "Artifact",
        {"file_path": str(tmp_path / "gone.html")},
        f"Published x at https://claude.ai/code/artifact/{FILE_ID}",
    )
    artifacts.ingest(session, [op], store_dir=tmp_path / "store")
    art = session.get(Artifact, FILE_ID)
    assert art.content_status == "missing"
    assert art.title == "gone"
    assert art.url.endswith(FILE_ID)


def test_publish_files_copies_named_files(session, tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "page.html").write_text("<title>Report</title>", encoding="utf-8")
    (src / "app.js").write_text("console.log(1)", encoding="utf-8")
    store = tmp_path / "store"
    op = _op(
        "f1",
        "mcp__claude-artifacts__publish_files",
        {
            "title": "Report",
            "files": [
                {"as": "index.html", "path": str(src / "page.html")},
                {"as": "js/app.js", "path": str(src / "app.js")},
                {"as": "../escape.js", "path": str(src / "app.js")},
            ],
        },
        _page_result("v1", title="Report"),
    )
    artifacts.ingest(session, [op], store_dir=store)
    art = session.get(Artifact, PAGE_SLUG)
    assert art.content_file == "index.html"
    assert art.content_status == "stale"  # the traversal entry was refused
    assert artifacts.content_path(PAGE_SLUG, "js/app.js", store_dir=store) is not None
    assert not (store / PAGE_SLUG / "escape.js").exists()


def test_content_path_rejects_traversal(tmp_path: Path):
    target = artifacts.content_dir(PAGE_SLUG, tmp_path)
    target.mkdir(parents=True)
    (target / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    assert artifacts.content_path(PAGE_SLUG, "index.html", store_dir=tmp_path) is not None
    assert artifacts.content_path(PAGE_SLUG, "../../secret.txt", store_dir=tmp_path) is None
    assert artifacts.content_path("not-a-uuid", "index.html", store_dir=tmp_path) is None


def test_listing_and_session_lookup(session, tmp_path: Path):
    artifacts.ingest(
        session,
        [
            _op("p1", "mcp__claude-artifacts__publish_version", {"content": "<p>"}, _page_result("v1"), session_id="s1"),
            _op(
                "p2",
                "mcp__claude-artifacts__edit_version",
                {"slug": PAGE_SLUG, "edits": []},
                _page_result("v2"),
                ts="2026-10-31T10:00:00Z",
                session_id="s2",
            ),
        ],
        store_dir=tmp_path,
    )
    [item] = artifacts.list_artifacts(session)
    assert item["session_ids"] == ["s2", "s1"]
    assert item["kind_label"] == "Artifact"
    by_session = artifacts.artifacts_by_session(session, ["s1", "s2", "s3"])
    assert set(by_session) == {"s1", "s2"}
    detail = artifacts.artifact_detail(session, PAGE_SLUG)
    assert [op["session_id"] for op in detail["ops"]] == ["s2", "s1"]


def test_storage_shim_lands_ahead_of_page_scripts():
    page = "<!doctype html><html><head><meta charset=utf8></head><body><script>localStorage.x</script></body></html>"
    shimmed = artifacts.with_storage_shim(page)
    assert shimmed.startswith("<!doctype html><html><head>" + artifacts.STORAGE_SHIM)
    # no <head>: right after the doctype, never before it (quirks mode)
    assert artifacts.with_storage_shim("<!DOCTYPE html><body>x</body>").startswith("<!DOCTYPE html>" + artifacts.STORAGE_SHIM)
    assert artifacts.with_storage_shim("<p>bare</p>") == artifacts.STORAGE_SHIM + "<p>bare</p>"


def _load_hook():
    path = Path(__file__).resolve().parent.parent / "hooks" / "emit_event.py"
    spec = importlib.util.spec_from_file_location("emit_event", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_captures_artifact_fields():
    hook = _load_hook()
    fields = hook._artifact_fields(
        "Artifact",
        {"file_path": r"C:\tmp\plan.md", "label": "v3"},
        {"tool_response": f"Published C:\\tmp\\plan.md at https://claude.ai/code/artifact/{FILE_ID}"},
    )
    assert fields["artifact_url"].endswith(FILE_ID)
    assert fields["artifact_title"] == "plan.md"
    assert fields["artifact_label"] == "v3"
    assert hook._artifact_fields("Edit", {"file_path": "x"}, {}) == {}
