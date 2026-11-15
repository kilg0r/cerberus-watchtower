"""Artifacts workdesk actions: pinning, opening in the default browser, and
linking artifacts whose publishing transcripts are gone."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from watchtower import api, artifacts, browser, db
from watchtower.models import Artifact, ArtifactOp, Base

PAGE_SLUG = "eda48d34-47b2-4cae-95b0-a3df6f7c0028"
FILE_ID = "964b295e-48b6-4d9b-8636-e8b287068f08"
OTHER_ID = "83671afb-8245-461b-9c5c-c7bb9f6c7969"


def _op(tool_use_id, tool, tool_input, result, ts="2026-10-30T17:51:38Z"):
    return {
        "tool_use_id": tool_use_id,
        "tool": tool,
        "input": tool_input,
        "result": result,
        "ts": ts,
        "cwd": r"C:\Users\aigor\_dev\_cerberus",
        "session_id": "s1",
    }


def _page_publish(ts="2026-10-30T17:51:38Z"):
    result = json.dumps(
        {"slug": PAGE_SLUG, "title": "Readiness", "url": "https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH", "version": "v1"}
    )
    return _op("p1", "mcp__claude-artifacts__publish_version", {"content": "<h1>Hi</h1>"}, result, ts=ts)


def _builtin_publish(tool_use_id, artifact_id, source: Path, ts):
    return _op(
        tool_use_id,
        "Artifact",
        {"file_path": str(source)},
        f"Published {source} at https://claude.ai/code/artifact/{artifact_id}",
        ts=ts,
    )


@pytest.fixture
def indexed(session, tmp_path: Path):
    """Three artifacts, newest last: an HTML page, a markdown file, a page
    whose source was gone at ingest time (no local copy)."""
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes", encoding="utf-8")
    store = tmp_path / "store"
    artifacts.ingest(
        session,
        [
            _page_publish(ts="2026-10-30T10:00:00Z"),
            _builtin_publish("a1", FILE_ID, notes, ts="2026-10-31T10:00:00Z"),
            _builtin_publish("a2", OTHER_ID, tmp_path / "gone.html", ts="2026-11-01T10:00:00Z"),
        ],
        store_dir=store,
    )
    return store


def test_pins_sort_first_in_pin_order(session, indexed):
    assert [a["id"] for a in artifacts.list_artifacts(session)] == [OTHER_ID, FILE_ID, PAGE_SLUG]

    first = artifacts.set_pinned(session, PAGE_SLUG, True)
    assert first["pinned"] and first["pinned_at"]
    artifacts.set_pinned(session, FILE_ID, True)
    items = artifacts.list_artifacts(session)
    # pinned first, in the order they were pinned; the rest newest first
    assert [a["id"] for a in items] == [PAGE_SLUG, FILE_ID, OTHER_ID]
    assert [a["pinned"] for a in items] == [True, True, False]

    # re-pinning keeps the original pin time, so the item doesn't move
    assert artifacts.set_pinned(session, PAGE_SLUG, True)["pinned_at"] == first["pinned_at"]
    assert artifacts.artifact_detail(session, PAGE_SLUG)["pinned"] is True

    assert artifacts.set_pinned(session, PAGE_SLUG, False) == {"pinned": False, "pinned_at": None}
    assert [a["id"] for a in artifacts.list_artifacts(session)] == [FILE_ID, OTHER_ID, PAGE_SLUG]
    assert artifacts.set_pinned(session, "00000000-0000-0000-0000-000000000000", True) is None


def test_pins_survive_the_list_limit(session, indexed):
    artifacts.set_pinned(session, PAGE_SLUG, True)  # the oldest artifact
    assert [a["id"] for a in artifacts.list_artifacts(session, limit=1)] == [PAGE_SLUG]


def test_browser_url_targets(session, indexed):
    server = "http://127.0.0.1:8765"
    assert artifacts.browser_url(session, PAGE_SLUG, "page", server, indexed) == (
        f"{server}/api/artifacts/{PAGE_SLUG}/content/index.html"
    )
    assert artifacts.browser_url(session, PAGE_SLUG, "claude", server, indexed).startswith("https://claude.ai/artifact/")
    # markdown would show as raw text, and a missing copy can't open at all
    assert artifacts.browser_url(session, FILE_ID, "page", server, indexed) is None
    assert artifacts.browser_url(session, OTHER_ID, "page", server, indexed) is None
    assert artifacts.browser_url(session, OTHER_ID, "claude", server, indexed).endswith(OTHER_ID)
    assert artifacts.browser_url(session, PAGE_SLUG, "file", server, indexed) is None
    assert artifacts.browser_url(session, PAGE_SLUG, "page", server, indexed, theme="light") == (
        f"{server}/api/artifacts/{PAGE_SLUG}/view/light/index.html"
    )
    assert artifacts.browser_url(session, "unknown", "claude", server, indexed) is None


def test_browser_names():
    assert browser.browser_name("ChromeHTML") == "Chrome"
    assert browser.browser_name("MSEdgeHTM") == "Edge"
    assert browser.browser_name("FirefoxURL-308046B0AF4A39CB") == "Firefox"
    assert browser.browser_name("SomethingElse") is None
    assert browser.browser_name(None) is None


def test_open_url_refuses_non_http(monkeypatch):
    launched = []
    monkeypatch.setattr(browser.os, "startfile", launched.append, raising=False)
    monkeypatch.setattr(browser.webbrowser, "open", lambda url: launched.append(url) or True)
    for bad in ("file:///C:/Windows/System32/calc.exe", r"C:\Windows\notepad.exe", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            browser.open_url(bad)
    assert launched == []


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'wt.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_session_factory", factory)
    with factory() as s:
        artifacts.ingest(s, [_page_publish()], store_dir=tmp_path / "store")
    opened = []
    monkeypatch.setattr(browser, "open_url", opened.append)
    test_client = TestClient(api.app)  # no `with`: keep the lifespan loops off
    test_client.opened = opened
    return test_client


def test_open_endpoint_launches_indexed_url(client):
    response = client.post(f"/api/artifacts/{PAGE_SLUG}/open", json={"target": "claude"})
    assert response.status_code == 200
    assert client.opened == ["https://claude.ai/artifact/WM24ecnBKnicUcAPL4fcFH"]
    assert response.json()["opened"] == client.opened[0]

    assert client.post(f"/api/artifacts/{OTHER_ID}/open", json={"target": "claude"}).status_code == 404
    assert client.post(f"/api/artifacts/{PAGE_SLUG}/open", json={"target": "url"}).status_code == 422


def test_open_endpoint_rejects_form_posts(client):
    # what a cross-origin page can send without a CORS preflight
    response = client.post(
        f"/api/artifacts/{PAGE_SLUG}/open",
        content='{"target": "claude"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 422
    assert client.opened == []


def test_pin_endpoints(client):
    assert client.put(f"/api/artifacts/{PAGE_SLUG}/pin").json()["pinned"] is True
    assert client.get("/api/artifacts").json()["artifacts"][0]["pinned"] is True
    assert client.delete(f"/api/artifacts/{PAGE_SLUG}/pin").json()["pinned"] is False
    assert client.put(f"/api/artifacts/{OTHER_ID}/pin").status_code == 404


# ------------------------------------------------------------ linking

LINKED_SHORT = "NYquE31oguJ7DC9PNZyarA"
LINKED_ID = "ae821acc-daf8-420d-9f20-b747cb2e5e27"


def test_version_time_reads_the_unix_prefix():
    assert artifacts.version_time("1793851773-5c69").isoformat() == "2026-11-05T04:09:33"
    assert artifacts.version_time("v2") is None
    assert artifacts.version_time(None) is None


def test_link_adds_an_artifact_without_a_session(session):
    meta = {"title": "PayTable Connect", "favicon": "📶", "sharing": "owner", "version": "1788234050-e7b6", "bogus": "x"}
    result = artifacts.link(session, f"https://claude.ai/artifact/{LINKED_SHORT}?ref=x#top", meta, cwd=r"C:\dev\_paytable")
    assert result == {"id": LINKED_ID, "created": True}

    art = session.get(Artifact, LINKED_ID)
    assert art.url == f"https://claude.ai/artifact/{LINKED_SHORT}"
    assert (art.title, art.favicon, art.sharing, art.version) == ("PayTable Connect", "📶", "owner", "1788234050-e7b6")
    assert art.updated_at.isoformat() == "2026-09-01T03:40:50"  # from the version id, not now
    assert (art.content_status, art.op_count, art.kind) == ("none", 0, "artifact")

    [item] = artifacts.list_artifacts(session)
    assert item["session_ids"] == [] and item["session_count"] == 0
    [op] = artifacts.artifact_detail(session, LINKED_ID)["ops"]
    assert (op["action"], op["session_id"], op["tool"]) == ("link", "", artifacts.LINK_TOOL)

    # re-linking refreshes metadata but never duplicates the link op
    assert artifacts.link(session, LINKED_ID, {"title": "PayTable Connect v2"}) == {"id": LINKED_ID, "created": False}
    assert session.get(Artifact, LINKED_ID).title == "PayTable Connect v2"
    assert session.query(ArtifactOp).count() == 1


def test_link_builds_urls_from_bare_ids(session):
    artifacts.link(session, LINKED_SHORT)
    assert session.get(Artifact, LINKED_ID).url == f"https://claude.ai/artifact/{LINKED_SHORT}"
    artifacts.link(session, PAGE_SLUG.upper())
    assert session.get(Artifact, PAGE_SLUG).url == f"https://claude.ai/code/artifact/{PAGE_SLUG}"
    assert session.get(Artifact, PAGE_SLUG).title is None  # shown as (untitled)


def test_link_rejects_non_artifacts(session):
    for bad in ("", "https://claude.ai/chat/abc", "hello world", "https://example.com/artifact/nope!"):
        assert artifacts.link(session, bad) is None
    assert session.query(Artifact).count() == 0


def test_relinking_an_indexed_artifact(session, indexed):
    art = session.get(Artifact, PAGE_SLUG)
    assert art.content_status == "ok" and art.version == "v1"

    # older metadata than the index has is ignored
    artifacts.link(session, PAGE_SLUG, {"title": "Old name", "version": "1700000000-aaaa"})
    assert art.title == "Readiness" and art.version == "v1"

    # a newer live version: metadata moves on, the local copy is now behind
    artifacts.link(session, PAGE_SLUG, {"title": "Readiness v9", "version": "1793851773-5c69"})
    assert (art.title, art.version, art.content_status) == ("Readiness v9", "1793851773-5c69", "stale")
    assert art.updated_at.isoformat() == "2026-11-05T04:09:33"
    assert art.op_count == 1  # a link is not a version


def test_link_endpoint(client):
    response = client.post(
        "/api/artifacts/link",
        json={"url": f"https://claude.ai/artifact/{LINKED_SHORT}", "title": "PayTable Connect", "cwd": r"C:\dev\_paytable"},
    )
    assert response.json() == {"id": LINKED_ID, "created": True}
    detail = client.get(f"/api/artifacts/{LINKED_ID}").json()
    assert detail["title"] == "PayTable Connect" and detail["project"] == "_paytable"
    assert detail["ops"][0]["session_title"] is None

    assert client.post("/api/artifacts/link", json={"url": "not a link"}).status_code == 422
    assert client.post("/api/artifacts/link", json={"url": LINKED_SHORT, "repo_id": "nope"}).status_code == 404
    assert client.post("/api/artifacts/link", json={"url": LINKED_SHORT, "sharing": "everyone"}).status_code == 422


def test_themed_view_forces_light_or_dark(client, tmp_path: Path, monkeypatch):
    page = tmp_path / "copy" / "index.html"
    page.parent.mkdir()
    page.write_text("<html><head></head><style>@media (prefers-color-scheme: dark){b{}}</style></html>", encoding="utf-8")
    css = page.with_name("site.css")
    css.write_text("@media (prefers-color-scheme: dark){b{}}", encoding="utf-8")
    files = {"index.html": page, "css/site.css": css}
    monkeypatch.setattr(artifacts, "content_path", lambda artifact_id, rel: files.get(rel))

    auto = client.get(f"/api/artifacts/{PAGE_SLUG}/content/index.html")
    assert "prefers-color-scheme: dark" in auto.text and "data-theme" not in auto.text
    light = client.get(f"/api/artifacts/{PAGE_SLUG}/view/light/index.html")
    assert light.status_code == 200 and "sandbox" in light.headers["content-security-policy"]
    assert "setAttribute('data-theme','light')" in light.text and "prefers-color-scheme" not in light.text
    # relative stylesheet URLs resolve under the same themed path
    sheet = client.get(f"/api/artifacts/{PAGE_SLUG}/view/dark/css/site.css")
    assert sheet.text == "@media (min-width: 0px){b{}}" and sheet.headers["content-type"].startswith("text/css")
    assert client.get(f"/api/artifacts/{PAGE_SLUG}/view/sepia/index.html").status_code == 404


def test_open_endpoint_carries_the_preview_theme(client, monkeypatch):
    monkeypatch.setattr(artifacts, "content_path", lambda *a, **k: Path("index.html"))
    with db.get_session_factory()() as s:
        s.get(Artifact, PAGE_SLUG).content_file = "index.html"
        s.commit()
    response = client.post(f"/api/artifacts/{PAGE_SLUG}/open", json={"target": "page", "theme": "light"})
    assert response.status_code == 200
    assert client.opened[-1].endswith(f"/api/artifacts/{PAGE_SLUG}/view/light/index.html")
    assert client.post(f"/api/artifacts/{PAGE_SLUG}/open", json={"target": "page", "theme": "neon"}).status_code == 422
