"""AI diff summaries via the Claude Code CLI in headless mode.

Summaries are cached by (repo_id, sha256-of-diff) so a given set of pending
changes is only summarized once — token spend tracks change velocity, not
dashboard refresh rate.
"""

import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SessionSummaryCache, SummaryCache
from ..scanners.git_scanner import MAX_UNTRACKED_BYTES, _git, _parse_status

MAX_DIFF_BYTES = 80 * 1024
CLAUDE_TIMEOUT_SECONDS = 120

PROMPT = (
    "You are summarizing pending (uncommitted) changes in a git repository so the "
    "developer can review them before committing. The full diff follows on stdin. "
    "Describe: the intent of the change, which files/modules are touched, and anything "
    "risky (migrations, config changes, deleted code, secrets). Plain text only, no "
    "markdown headers, under 200 words."
)


SESSION_PROMPT = (
    "You are summarizing a Claude Code session for a developer's activity dashboard. "
    "The user/assistant dialogue follows on stdin (tool calls and results were "
    "stripped). Describe: what the user asked for, what was actually done, key "
    "decisions made along the way, and anything unresolved or left as follow-up. "
    "Plain text only, no markdown headers, under 150 words."
)


class SummarizerError(Exception):
    pass


def collect_diff(repo_path: Path) -> str:
    """Tracked diff vs HEAD plus untracked file contents, capped at MAX_DIFF_BYTES."""
    parts = []
    tracked = _git(repo_path, "diff", "HEAD")
    if tracked.returncode == 0 and tracked.stdout:
        parts.append(tracked.stdout)

    for status, rel_path in _parse_status(repo_path) or []:
        if status != "untracked":
            continue
        file_path = repo_path / rel_path
        try:
            if file_path.stat().st_size > MAX_UNTRACKED_BYTES:
                parts.append(f"--- untracked (too large to inline): {rel_path}\n")
                continue
            content = file_path.read_bytes()
        except OSError:
            continue
        if b"\0" in content[:8192]:
            parts.append(f"--- untracked (binary): {rel_path}\n")
        else:
            text = content.decode("utf-8", errors="replace")
            parts.append(f"--- untracked file: {rel_path}\n{text}\n")

    diff = "\n".join(parts)
    encoded = diff.encode("utf-8")
    if len(encoded) > MAX_DIFF_BYTES:
        diff = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
        diff += "\n\n[diff truncated at 80KB - summarize what is visible]"
    return diff


def diff_hash(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def run_claude(diff: str, prompt: str = PROMPT) -> str:
    """Run `claude -p` with input on stdin. Raises SummarizerError on any failure."""
    claude = shutil.which("claude")
    if claude is None:
        raise SummarizerError("claude CLI not found on PATH")
    try:
        result = subprocess.run(
            [claude, "-p", prompt],
            input=diff,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SummarizerError("claude timed out after 120s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise SummarizerError(f"claude exited {result.returncode}: {detail}")
    summary = result.stdout.strip()
    if not summary:
        raise SummarizerError("claude returned an empty summary")
    return summary


def get_or_create_summary(session: Session, repo_id: str, diff: str) -> dict:
    """Cache-aware summary. Failures raise SummarizerError and are never cached."""
    hash_ = diff_hash(diff)
    cached = session.execute(
        select(SummaryCache).where(
            SummaryCache.repo_id == repo_id, SummaryCache.diff_hash == hash_
        )
    ).scalar_one_or_none()
    if cached is not None:
        return {
            "summary": cached.summary,
            "cached": True,
            "generated_at": cached.generated_at.isoformat(),
            "diff_hash": hash_,
        }

    summary = run_claude(diff)
    generated_at = datetime.now(timezone.utc)
    session.add(
        SummaryCache(
            repo_id=repo_id, diff_hash=hash_, summary=summary, generated_at=generated_at
        )
    )
    session.commit()
    return {
        "summary": summary,
        "cached": False,
        "generated_at": generated_at.isoformat(),
        "diff_hash": hash_,
    }


def get_or_create_session_summary(
    session: Session, session_id: str, conversation: str
) -> dict:
    """Cache-aware conversation summary. Failures raise and are never cached."""
    hash_ = diff_hash(conversation)
    cached = session.execute(
        select(SessionSummaryCache).where(
            SessionSummaryCache.session_id == session_id,
            SessionSummaryCache.content_hash == hash_,
        )
    ).scalar_one_or_none()
    if cached is not None:
        return {
            "summary": cached.summary,
            "cached": True,
            "generated_at": cached.generated_at.isoformat(),
            "content_hash": hash_,
        }

    summary = run_claude(conversation, prompt=SESSION_PROMPT)
    generated_at = datetime.now(timezone.utc)
    session.add(
        SessionSummaryCache(
            session_id=session_id,
            content_hash=hash_,
            summary=summary,
            generated_at=generated_at,
        )
    )
    session.commit()
    return {
        "summary": summary,
        "cached": False,
        "generated_at": generated_at.isoformat(),
        "content_hash": hash_,
    }
