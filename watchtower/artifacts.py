"""Artifact index - everything sessions publish, browsable from Watchtower.

Recovered from session transcripts as resolved tool_use/tool_result pairs
(see `TranscriptState`), in two kinds:

  artifact - a claude.ai artifact, published either by Claude Code's built-in
             `Artifact` tool (a local .md/.html file, optionally with sibling
             files) or by the claude-artifacts MCP (publish_version /
             publish_files / edit_version / create_from_type). Both tools
             address the same service objects, keyed here by uuid - the
             /artifact/<short> URL form is the uuid in base58.
  doc      - a Claude Doc (batch / create / update)

Rows persist in SQLite so the index outlives transcript cleanup, and ingestion
is idempotent by tool_use_id so every rescan is safe. Where the content is
recoverable - the published local file, the HTML sent in a publish, the files
a publish_files pointed at, edits replayed over the last full publish - a
local copy is kept under ARTIFACTS_DIR/<id>/current/ for in-app preview. Docs
are link-only (their content lives in the docs service).
"""

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DATA_DIR
from .models import Artifact, ArtifactOp

ARTIFACTS_DIR = DATA_DIR / "artifacts"
MAX_COPY_BYTES = 32 * 1024 * 1024
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
CODE_URL_RE = re.compile(rf"https://claude\.ai/code/artifact/({UUID})")
# built-in Artifact tool results, old and new forms:
#   Published <path> at https://claude.ai/code/artifact/<uuid>
#   Published <path> at https://claude.ai/artifact/<short> (Version 34, version id 1793391446-5b1d)
PUBLISHED_RE = re.compile(
    r"Published (.+?) at (https://claude\.ai/(?:code/)?artifact/([A-Za-z0-9-]+))"
    r"(?: \(Version (\d+)(?:, version id ([0-9A-Za-z-]+))?\))?"
)
#   Created a new Artifact at https://claude.ai/artifact/<short> (version <id>) from the Artifact type ...
CREATED_FROM_TYPE_RE = re.compile(
    r"Created a new Artifact at (https://claude\.ai/(?:code/)?artifact/([A-Za-z0-9-]+))(?: \(version ([0-9A-Za-z-]+)\))?"
)
UUID_RE = re.compile(rf"^{UUID}$")
_ARTIFACT_URL_ID_RE = re.compile(r"artifact/([A-Za-z0-9-]+)")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# claude.ai short artifact ids are the artifact uuid in base58 (bitcoin alphabet)
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

KIND_LABELS = {"artifact": "Artifact", "doc": "Doc"}


# ------------------------------------------------------------ normalizing


def canonical_id(value: str | None) -> str | None:
    """Artifact uuid from a uuid, a base58 short id, or either URL form - one
    artifact is reached as /code/artifact/<uuid> and /artifact/<short>."""
    if not value:
        return None
    match = _ARTIFACT_URL_ID_RE.search(value)
    token = match.group(1) if match else value.strip()
    if UUID_RE.match(token):
        return token.lower()
    if 16 <= len(token) <= 24 and all(ch in _B58 for ch in token):
        number = 0
        for ch in token:
            number = number * 58 + _B58.index(ch)
        if number < 1 << 128:
            return str(uuid.UUID(int=number))
    return None


def _parse_ts(value: str | None) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_prefix(text: str) -> dict | None:
    """The JSON object a tool result opens with (prose may follow it)."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _page_meta(data: dict) -> dict:
    return {
        "title": data.get("title"),
        "url": data.get("url"),
        "sharing": data.get("read"),
        "version": data.get("version"),
        "favicon": data.get("favicon") or None,
    }


def normalize(op: dict) -> dict | None:
    """Raw transcript op -> {artifact_id, kind, action, meta, content} or None
    when the call didn't create/change an artifact (reads, refusals)."""
    tool = op.get("tool") or ""
    tool_input = op.get("input") or {}
    result = op.get("result") or ""
    base = {
        "tool_use_id": op.get("tool_use_id"),
        "session_id": op.get("session_id"),
        "ts": _parse_ts(op.get("ts")),
        "cwd": op.get("cwd"),
        "tool": tool,
        "label": tool_input.get("label"),
        "content": None,
    }
    if not base["tool_use_id"] or not base["session_id"]:
        return None

    if tool == "Artifact":
        match = PUBLISHED_RE.search(result)
        if not match:
            created = CREATED_FROM_TYPE_RE.search(result)
            if not created:
                return None  # read / list / watch / status / open actions
            return {  # made from an artifact type - its files live in the service
                **base,
                "artifact_id": canonical_id(created.group(2)),
                "kind": "artifact",
                "action": "publish",
                "meta": {
                    "url": created.group(1),
                    "title": tool_input.get("title"),
                    "description": tool_input.get("description"),
                    "version": created.group(3),
                },
            }
        source = tool_input.get("file_path") or match.group(1)
        extra = tool_input.get("files") if isinstance(tool_input.get("files"), dict) else {}
        version = match.group(5) or (f"Version {match.group(4)}" if match.group(4) else None)
        return {
            **base,
            "artifact_id": canonical_id(match.group(3)),
            "kind": "artifact",
            "action": "publish",
            "meta": {
                "url": match.group(2),
                "description": tool_input.get("description"),
                "favicon": tool_input.get("favicon"),
                "source_path": source,
                "version": version,
            },
            "content": {"mode": "replace_file", "src": source, "extra": extra},
        }

    if tool.startswith("mcp__claude-artifacts__"):
        action_name = tool.rsplit("__", 1)[-1]
        data = _json_prefix(result)
        if action_name == "delete_artifact":
            slug = (data or {}).get("slug") or tool_input.get("slug")
            return {**base, "artifact_id": canonical_id(slug), "kind": "artifact", "action": "delete", "meta": {}}
        if not data or not data.get("slug"):
            return None  # refused or failed - nothing published
        normalized = {
            **base,
            "artifact_id": canonical_id(str(data["slug"])),
            "kind": "artifact",
            "action": "edit" if action_name == "edit_version" else "publish",
            "meta": _page_meta(data),
        }
        if action_name == "publish_version" and isinstance(tool_input.get("content"), str):
            normalized["content"] = {"mode": "replace_html", "html": tool_input["content"]}
        elif action_name == "publish_files":
            normalized["content"] = {
                "mode": "files",
                "files": [f for f in tool_input.get("files") or [] if isinstance(f, dict)],
                # a new artifact, or mode=replace, starts from an empty file set
                "replace": not tool_input.get("slug") or tool_input.get("mode") == "replace",
                "remove": [r for r in tool_input.get("remove") or [] if isinstance(r, str)],
            }
            first = next((f.get("path") for f in normalized["content"]["files"] if f.get("as") == "index.html"), None)
            normalized["meta"]["source_path"] = first
        elif action_name == "edit_version":
            normalized["content"] = {
                "mode": "edits",
                "path": tool_input.get("path") or "index.html",
                "edits": tool_input.get("edits") or [],
            }
        return normalized

    if tool.startswith("mcp__claude_ai_Claude_Docs__"):
        data = _json_prefix(result)
        if data and data.get("verdict") not in (None, "allow"):
            return None
        container = tool_input.get("container") or {}
        create = container.get("create") if isinstance(container.get("create"), dict) else None
        action_name = tool.rsplit("__", 1)[-1]
        match = CODE_URL_RE.search(result)
        doc_id = canonical_id(str(container.get("id") or "")) or (match.group(1).lower() if match else None)
        if not doc_id:
            return None
        title = (create or {}).get("name") or tool_input.get("name") or tool_input.get("title")
        is_create = bool(create) or action_name == "create"
        return {
            **base,
            "artifact_id": doc_id,
            "kind": "doc",
            "action": "delete" if action_name == "delete" else ("publish" if is_create else "edit"),
            "meta": {"title": title, "url": f"https://claude.ai/code/artifact/{doc_id}"},
        }
    return None


# ------------------------------------------------------------ local copies


def _safe_rel(name: str | None) -> PurePosixPath | None:
    """A served path kept strictly inside the artifact's content dir."""
    if not name:
        return None
    rel = PurePosixPath(name.replace("\\", "/").lstrip("/"))
    if not rel.parts or any(part in ("..", "") or ":" in part for part in rel.parts):
        return None
    return rel


def content_dir(artifact_id: str, store_dir: Path | None = None) -> Path:
    return (store_dir or ARTIFACTS_DIR) / artifact_id / "current"


def _copy_in(src: Path, dest: Path) -> bool:
    try:
        if not src.is_file() or src.stat().st_size > MAX_COPY_BYTES:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return True
    except OSError:
        return False


def _title_from_file(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:65536].decode("utf-8", errors="replace")
    except OSError:
        return None
    if path.suffix.lower() in (".html", ".htm"):
        match = _TITLE_RE.search(head)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else None
    for line in head.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _apply_content(art: Artifact, op: dict, store_dir: Path | None) -> None:
    content = op["content"]
    target = content_dir(art.id, store_dir)
    mode = content["mode"]

    if mode in ("replace_file", "replace_html") or (mode == "files" and content["replace"]):
        shutil.rmtree(target, ignore_errors=True)

    if mode == "replace_file":
        src = Path(content["src"])
        if _copy_in(src, target / src.name):
            art.content_file, art.content_status = src.name, "ok"
            art.title = _title_from_file(target / src.name) or art.title or src.stem
        else:
            art.content_file, art.content_status = None, "missing"
            art.title = art.title or src.stem
            return
        # multi-file publishes: {served name: local path} beside the entry page
        for name, path in (content.get("extra") or {}).items():
            rel = _safe_rel(name)
            if rel is None or not isinstance(path, str) or not _copy_in(Path(path), target.joinpath(*rel.parts)):
                art.content_status = "stale"
        return

    if mode == "replace_html":
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(content["html"], encoding="utf-8")
        art.content_file, art.content_status = "index.html", "ok"
        return

    if mode == "files":
        missing = False
        for entry in content["files"]:
            rel = _safe_rel(entry.get("as"))
            if rel is None or not entry.get("path"):
                missing = True
                continue
            missing |= not _copy_in(Path(entry["path"]), target.joinpath(*rel.parts))
        for name in content["remove"]:
            rel = _safe_rel(name)
            if rel is not None:
                target.joinpath(*rel.parts).unlink(missing_ok=True)
        has_index = (target / "index.html").is_file()
        art.content_file = "index.html" if has_index else None
        if not has_index:
            art.content_status = "missing"
        elif missing or (not content["replace"] and art.content_status != "ok"):
            art.content_status = "stale"  # some files could not be captured
        else:
            art.content_status = "ok"
        return

    if mode == "edits":
        rel = _safe_rel(content["path"])
        page = target.joinpath(*rel.parts) if rel else None
        if art.content_status != "ok" or page is None or not page.is_file():
            if art.content_status == "ok":
                art.content_status = "stale"
            return  # no trustworthy base to replay over
        text = page.read_text(encoding="utf-8", errors="replace")
        for edit in content["edits"]:
            old, new = (edit or {}).get("old_text"), (edit or {}).get("new_text", "")
            if not isinstance(old, str) or text.count(old) != 1:
                art.content_status = "stale"
                return
            text = text.replace(old, new if isinstance(new, str) else "", 1)
        page.write_text(text, encoding="utf-8")


def _is_full_replace(op: dict) -> bool:
    content = op.get("content")
    return bool(content) and (
        content["mode"] in ("replace_file", "replace_html")
        or (content["mode"] == "files" and content["replace"])
    )


# ------------------------------------------------------------ ingestion


def ingest(session: Session, raw_ops: list[dict], store_dir: Path | None = None) -> int:
    """Index new artifact ops (chronologically); returns how many were new."""
    ops = [op for op in (normalize(raw) for raw in raw_ops) if op and op["artifact_id"]]
    if not ops:
        return 0
    known = set(
        session.scalars(
            select(ArtifactOp.tool_use_id).where(
                ArtifactOp.tool_use_id.in_([op["tool_use_id"] for op in ops])
            )
        )
    )
    fresh: dict[str, dict] = {}
    for op in ops:  # a batch can repeat an op (main + subagent transcripts)
        if op["tool_use_id"] not in known:
            fresh.setdefault(op["tool_use_id"], op)
    ordered = sorted(fresh.values(), key=lambda op: op["ts"])

    # content written before an artifact's last full replace is discarded by
    # it anyway - skip that work (it matters on the first, whole-history pass)
    last_full = {op["artifact_id"]: i for i, op in enumerate(ordered) if _is_full_replace(op)}

    for i, op in enumerate(ordered):
        art = session.get(Artifact, op["artifact_id"])
        if art is None:
            if op["action"] == "delete":
                continue
            art = Artifact(
                id=op["artifact_id"],
                kind=op["kind"],
                created_at=op["ts"],
                updated_at=op["ts"],
                content_status="none",
                op_count=0,
                deleted=False,
            )
            session.add(art)
        for key, value in op["meta"].items():
            if value:
                setattr(art, key, value)
        art.cwd = op["cwd"] or art.cwd
        art.updated_at = max(art.updated_at or op["ts"], op["ts"])
        art.op_count = (art.op_count or 0) + 1
        if op["action"] == "delete":
            art.deleted = True
        elif op["content"] and i >= last_full.get(op["artifact_id"], -1):
            _apply_content(art, op, store_dir)
        session.add(
            ArtifactOp(
                tool_use_id=op["tool_use_id"],
                artifact_id=art.id,
                session_id=op["session_id"],
                tool=op["tool"],
                action=op["action"],
                label=(op["label"] or "")[:300] or None,
                version=op["meta"].get("version"),
                cwd=op["cwd"],
                ts=op["ts"],
            )
        )
        session.flush()
    session.commit()
    return len(ordered)


# ------------------------------------------------------------ reading


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def to_dict(art: Artifact) -> dict:
    return {
        "id": art.id,
        "kind": art.kind,
        "kind_label": KIND_LABELS.get(art.kind, art.kind),
        "title": art.title or "(untitled)",
        "url": art.url,
        "description": art.description,
        "favicon": art.favicon,
        "source_path": art.source_path,
        "sharing": art.sharing,
        "version": art.version,
        "cwd": art.cwd,
        "content_file": art.content_file,
        "content_status": art.content_status,
        "deleted": art.deleted,
        "op_count": art.op_count,
        "created_at": _iso(art.created_at),
        "updated_at": _iso(art.updated_at),
    }


def list_artifacts(session: Session, limit: int = 1000) -> list[dict]:
    rows = session.scalars(select(Artifact).order_by(Artifact.updated_at.desc()).limit(limit)).all()
    # sessions per artifact, most recently active first
    session_ids: dict[str, list[str]] = {}
    for artifact_id, session_id, _ in session.execute(
        select(ArtifactOp.artifact_id, ArtifactOp.session_id, func.max(ArtifactOp.ts).label("last"))
        .group_by(ArtifactOp.artifact_id, ArtifactOp.session_id)
        .order_by(func.max(ArtifactOp.ts).desc())
    ).all():
        session_ids.setdefault(artifact_id, []).append(session_id)
    out = []
    for art in rows:
        item = to_dict(art)
        item["session_ids"] = session_ids.get(art.id, [])
        item["session_count"] = len(item["session_ids"])
        out.append(item)
    return out


def artifact_detail(session: Session, artifact_id: str) -> dict | None:
    art = session.get(Artifact, artifact_id)
    if art is None:
        return None
    ops = session.scalars(
        select(ArtifactOp).where(ArtifactOp.artifact_id == artifact_id).order_by(ArtifactOp.ts.desc())
    ).all()
    detail = to_dict(art)
    detail["ops"] = [
        {
            "ts": _iso(op.ts),
            "action": op.action,
            "tool": op.tool,
            "label": op.label,
            "version": op.version,
            "session_id": op.session_id,
            "cwd": op.cwd,
        }
        for op in ops
    ]
    return detail


def artifacts_by_session(session: Session, session_ids: list[str]) -> dict[str, list[dict]]:
    if not session_ids:
        return {}
    rows = session.execute(
        select(ArtifactOp.session_id, Artifact)
        .join(Artifact, Artifact.id == ArtifactOp.artifact_id)
        .where(ArtifactOp.session_id.in_(session_ids))
        .order_by(Artifact.updated_at.desc())
    ).all()
    out: dict[str, list[dict]] = {}
    for session_id, art in rows:
        bucket = out.setdefault(session_id, [])
        if not any(a["id"] == art.id for a in bucket):
            bucket.append(
                {"id": art.id, "kind": art.kind, "title": art.title or "(untitled)", "url": art.url, "deleted": art.deleted}
            )
    return out


# Local copies are served under a CSP sandbox (opaque origin), where touching
# localStorage/sessionStorage throws a SecurityError - enough to kill an
# artifact's only script and leave a blank page. This shim swaps in an
# in-memory Storage only when the real one is inaccessible.
STORAGE_SHIM = (
    "<script>(function(){function mk(){var d={};return{getItem:function(k){"
    "return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null},setItem:function(k,v){d[k]=String(v)},"
    "removeItem:function(k){delete d[k]},clear:function(){d={}},key:function(i){return Object.keys(d)[i]||null},"
    "get length(){return Object.keys(d).length}}}['localStorage','sessionStorage'].forEach(function(n){"
    "try{window[n].getItem('x')}catch(e){try{Object.defineProperty(window,n,{value:mk(),configurable:true})}"
    "catch(_){}}})})();</script>"
)
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_DOCTYPE_RE = re.compile(r"<!doctype[^>]*>", re.IGNORECASE)


def with_storage_shim(html: str) -> str:
    """Insert STORAGE_SHIM ahead of the page's own scripts (after <head> or the
    doctype - never before the doctype, which would force quirks mode)."""
    head = _HEAD_RE.search(html, 0, 4096)
    if head:
        return html[: head.end()] + STORAGE_SHIM + html[head.end() :]
    doctype = _DOCTYPE_RE.match(html.lstrip())
    if doctype:
        offset = len(html) - len(html.lstrip()) + doctype.end()
        return html[:offset] + STORAGE_SHIM + html[offset:]
    return STORAGE_SHIM + html


def content_path(artifact_id: str, rel_path: str, store_dir: Path | None = None) -> Path | None:
    """Resolve a served file inside an artifact's local copy, or None."""
    if not UUID_RE.match(artifact_id):
        return None
    rel = _safe_rel(rel_path)
    if rel is None:
        return None
    root = content_dir(artifact_id, store_dir).resolve()
    candidate = root.joinpath(*rel.parts).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate
