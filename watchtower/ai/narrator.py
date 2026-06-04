"""AI module narratives - "what does this project do and why is it shaped
this way" - cached by structural signature so a narrative only regenerates
when the module's shape (refs, packages, handlers, endpoints) changes."""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Narrative
from .summarizer import SummarizerError, run_claude

PROMPT = (
    "You are documenting one project inside a .NET solution for an architecture "
    "dashboard. From the structural facts on stdin (references, packages, MediatR "
    "handlers, endpoints), explain: what this project is responsible for, its role "
    "in the layering, and anything notable about its dependencies. Plain text, "
    "confident and specific, under 150 words. Do not restate the raw lists."
)


def project_signature(arch: dict, node_id: str) -> str | None:
    """Structural facts block for one project node; None if the node is unknown."""
    node = next((n for n in arch["graph"]["nodes"] if n["id"] == node_id), None)
    if node is None:
        return None
    uses = sorted(e["target"] for e in arch["graph"]["edges"] if e["source"] == node_id)
    used_by = sorted(e["source"] for e in arch["graph"]["edges"] if e["target"] == node_id)
    handlers = sorted(h["handler"] for h in arch["handlers"] if h["project"] == node_id)
    endpoints = sorted(
        f"{e['verb']} {e['route']}" for e in arch["endpoints"] if e["project"] == node_id
    )
    lines = [
        f"Project: {node_id}",
        f"Solution folder: {node['folder'] or 'none'}",
        f"Framework: {node['framework'] or 'unknown'}",
        f"C# files: {node['file_count']}",
        f"References: {', '.join(uses) or 'none'}",
        f"Referenced by: {', '.join(used_by) or 'none'}",
        f"Packages: {', '.join(p['name'] for p in node['packages']) or 'none'}",
        f"MediatR handlers ({len(handlers)}): {', '.join(handlers[:40])}",
        f"Endpoints ({len(endpoints)}): {', '.join(endpoints[:40])}",
    ]
    return "\n".join(lines)


def get_or_create_narrative(
    session: Session, repo_id: str, node_id: str, signature: str
) -> dict:
    """Cache-aware narrative generation. Raises SummarizerError on AI failure."""
    sig_hash = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    cached = session.execute(
        select(Narrative).where(
            Narrative.repo_id == repo_id,
            Narrative.node_id == node_id,
            Narrative.sig_hash == sig_hash,
        )
    ).scalar_one_or_none()
    if cached is not None:
        return {
            "narrative": cached.narrative,
            "cached": True,
            "generated_at": cached.generated_at.isoformat(),
        }

    narrative = run_claude(signature, prompt=PROMPT)
    generated_at = datetime.now(timezone.utc)
    session.add(
        Narrative(
            repo_id=repo_id,
            node_id=node_id,
            sig_hash=sig_hash,
            narrative=narrative,
            generated_at=generated_at,
        )
    )
    session.commit()
    return {"narrative": narrative, "cached": False, "generated_at": generated_at.isoformat()}
