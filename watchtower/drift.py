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
    else:  # vue
        payload = {
            "routes": sorted(r["path"] for r in arch["routes"]),
            "packages": {
                **arch["packages"]["dependencies"],
                **{f"dev:{k}": v for k, v in arch["packages"]["dev_dependencies"].items()},
            },
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


def _diff_payloads(old: dict, new: dict) -> dict:
    changes = {}
    if "edges" in new:  # dotnet
        old_edges, new_edges = set(old.get("edges", [])), set(new["edges"])
        old_nodes = {n["id"]: n for n in old.get("nodes", [])}
        new_nodes = {n["id"]: n for n in new["nodes"]}
        package_changes = []
        for node_id in sorted(set(old_nodes) & set(new_nodes)):
            for change in _package_changes(
                old_nodes[node_id]["packages"], new_nodes[node_id]["packages"]
            ):
                package_changes.append({**change, "project": node_id})
        old_eps = set(old.get("endpoints", []))
        new_eps = set(new.get("endpoints", []))
        changes = {
            "added_projects": sorted(set(new_nodes) - set(old_nodes)),
            "removed_projects": sorted(set(old_nodes) - set(new_nodes)),
            "added_edges": sorted(new_edges - old_edges),
            "removed_edges": sorted(old_edges - new_edges),
            "added_endpoints": sorted(new_eps - old_eps),
            "removed_endpoints": sorted(old_eps - new_eps),
            "package_changes": package_changes,
        }
    else:  # vue
        old_routes, new_routes = set(old.get("routes", [])), set(new["routes"])
        changes = {
            "added_routes": sorted(new_routes - old_routes),
            "removed_routes": sorted(old_routes - new_routes),
            "package_changes": _package_changes(
                old.get("packages", {}), new["packages"]
            ),
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
