"""Architecture drift detection.

Every architecture scan produces a stack-specific signature. A snapshot row is
recorded only when the signature changes, so the snapshot table is a change
log, not a scan log. Drift = structured diff of the two most recent snapshots.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ArchSnapshot


def snapshot_signature(arch: dict) -> tuple[str, dict]:
    """Reduce an architecture payload to the structure we track for drift."""
    if arch["stack"] == "dotnet":
        payload = {
            "nodes": sorted(
                (
                    {
                        "id": n["id"],
                        "folder": n["folder"],
                        "is_test": n["is_test"],
                        "framework": n["framework"],
                        "packages": {p["name"]: p["version"] for p in n["packages"]},
                    }
                    for n in arch["graph"]["nodes"]
                ),
                key=lambda n: n["id"],
            ),
            "edges": sorted(
                f"{e['source']}->{e['target']}" for e in arch["graph"]["edges"]
            ),
            "endpoints": sorted(
                f"{e['verb']} {e['route']}" for e in arch["endpoints"]
            ),
            "stats": arch["stats"],
        }
    elif arch["stack"] == "vue":
        payload = {
            "routes": sorted(r["path"] for r in arch["routes"]),
            "packages": {
                **arch["packages"]["dependencies"],
                **{f"dev:{k}": v for k, v in arch["packages"]["dev_dependencies"].items()},
            },
            "edges": sorted(
                f"{e['source']}->{e['target']}"
                for e in arch.get("graph", {}).get("edges", [])
            ),
            "stats": arch["stats"],
        }
    elif arch["stack"] == "python":
        payload = {
            "modules": sorted(n["id"] for n in arch["graph"]["nodes"]),
            "edges": sorted(
                f"{e['source']}->{e['target']}" for e in arch["graph"]["edges"]
            ),
            "packages": dict(arch["dependencies"]),
            "endpoints": sorted(f"{e['verb']} {e['route']}" for e in arch["endpoints"]),
            "stats": arch["stats"],
        }
    else:  # generic inventory stacks (config / terraform / android / web / mixed)
        payload = {
            "languages": arch.get("languages", {}),
            "directories": {d["name"]: d["files"] for d in arch.get("directories", [])},
            "stats": arch["stats"],
        }
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), payload


def record_snapshot(session: Session, repo_id: str, arch: dict) -> bool:
    """Store a snapshot if the architecture changed. Returns True when new."""
    sig_hash, payload = snapshot_signature(arch)
    latest = session.execute(
        select(ArchSnapshot)
        .where(ArchSnapshot.repo_id == repo_id)
        .order_by(ArchSnapshot.created_at.desc(), ArchSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is not None and latest.sig_hash == sig_hash:
        return False
    session.add(
        ArchSnapshot(
            repo_id=repo_id,
            sig_hash=sig_hash,
            payload=json.dumps(payload),
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    return True


def _package_changes(old: dict, new: dict) -> list[dict]:
    changes = []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before != after:
            changes.append({"package": name, "from": before, "to": after})
    return changes


def _set_diff(old: dict, new: dict, key: str) -> tuple[list, list]:
    old_set, new_set = set(old.get(key, [])), set(new.get(key, []))
    return sorted(new_set - old_set), sorted(old_set - new_set)


def _diff_payloads(old: dict, new: dict) -> dict:
    changes = {}
    if "nodes" in new:  # dotnet: project graph with per-project packages
        added_edges, removed_edges = _set_diff(old, new, "edges")
        added_eps, removed_eps = _set_diff(old, new, "endpoints")
        old_nodes = {n["id"]: n for n in old.get("nodes", [])}
        new_nodes = {n["id"]: n for n in new["nodes"]}
        package_changes = []
        for node_id in sorted(set(old_nodes) & set(new_nodes)):
            for change in _package_changes(
                old_nodes[node_id]["packages"], new_nodes[node_id]["packages"]
            ):
                package_changes.append({**change, "project": node_id})
        changes = {
            "added_projects": sorted(set(new_nodes) - set(old_nodes)),
            "removed_projects": sorted(set(old_nodes) - set(new_nodes)),
            "added_edges": added_edges,
            "removed_edges": removed_edges,
            "added_endpoints": added_eps,
            "removed_endpoints": removed_eps,
            "package_changes": package_changes,
        }
    elif "modules" in new:  # python: module import graph
        added_modules, removed_modules = _set_diff(old, new, "modules")
        added_edges, removed_edges = _set_diff(old, new, "edges")
        added_eps, removed_eps = _set_diff(old, new, "endpoints")
        changes = {
            "added_projects": added_modules,
            "removed_projects": removed_modules,
            "added_edges": added_edges,
            "removed_edges": removed_edges,
            "added_endpoints": added_eps,
            "removed_endpoints": removed_eps,
            "package_changes": _package_changes(old.get("packages", {}), new["packages"]),
        }
    elif "routes" in new:  # vue
        added_routes, removed_routes = _set_diff(old, new, "routes")
        added_edges, removed_edges = _set_diff(old, new, "edges")
        changes = {
            "added_routes": added_routes,
            "removed_routes": removed_routes,
            "added_edges": added_edges,
            "removed_edges": removed_edges,
            "package_changes": _package_changes(old.get("packages", {}), new["packages"]),
        }
    else:  # generic inventory
        old_dirs, new_dirs = old.get("directories", {}), new.get("directories", {})
        old_langs, new_langs = old.get("languages", {}), new.get("languages", {})
        changes = {
            "added_directories": sorted(set(new_dirs) - set(old_dirs)),
            "removed_directories": sorted(set(old_dirs) - set(new_dirs)),
            "language_changes": [
                {"language": lang, "from": old_langs.get(lang), "to": new_langs.get(lang)}
                for lang in sorted(set(old_langs) | set(new_langs))
                if old_langs.get(lang) != new_langs.get(lang)
            ],
        }
    changes["stats_delta"] = {
        key: {"from": old.get("stats", {}).get(key), "to": value}
        for key, value in new.get("stats", {}).items()
        if old.get("stats", {}).get(key) != value
    }
    return changes


def compute_drift(session: Session, repo_id: str) -> dict:
    """Diff the two most recent snapshots for a repo."""
    rows = (
        session.execute(
            select(ArchSnapshot)
            .where(ArchSnapshot.repo_id == repo_id)
            .order_by(ArchSnapshot.created_at.desc(), ArchSnapshot.id.desc())
            .limit(2)
        )
        .scalars()
        .all()
    )
    count = session.execute(
        select(ArchSnapshot.id).where(ArchSnapshot.repo_id == repo_id)
    ).all()
    base = {"snapshot_count": len(count)}
    if len(rows) < 2:
        return {
            **base,
            "changed": False,
            "latest_at": rows[0].created_at.isoformat() if rows else None,
            "previous_at": None,
            "changes": None,
        }
    latest, previous = rows[0], rows[1]
    changes = _diff_payloads(json.loads(previous.payload), json.loads(latest.payload))
    changed = any(value for key, value in changes.items())
    return {
        **base,
        "changed": changed,
        "latest_at": latest.created_at.isoformat(),
        "previous_at": previous.created_at.isoformat(),
        "changes": changes,
    }
