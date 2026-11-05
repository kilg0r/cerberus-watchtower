from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ArchSnapshot(Base):
    """Point-in-time architecture signatures, one row per detected change.
    Drift = diff between the two most recent rows for a repo."""

    __tablename__ = "arch_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(64), index=True)
    sig_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)  # JSON: stack-specific signature
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Narrative(Base):
    """AI-generated module narratives, cached by structural signature so they
    only regenerate when the module actually changes shape."""

    __tablename__ = "narratives"
    __table_args__ = (UniqueConstraint("repo_id", "node_id", "sig_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(String(128))
    sig_hash: Mapped[str] = mapped_column(String(64))
    narrative: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime)


class SessionSummaryCache(Base):
    """AI conversation summaries for Claude Code sessions, keyed by session +
    content hash - an active session regenerates as it grows, a finished one
    is summarized exactly once."""

    __tablename__ = "session_summary_cache"
    __table_args__ = (UniqueConstraint("session_id", "content_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime)


class CommitArchCache(Base):
    """Full architecture scan of a repo's tree as it stood at a specific commit.
    A commit's tree is immutable, so this is cached once per (repo, sha) and
    never invalidated - it backs the bundle architectural-delta drill-through."""

    __tablename__ = "commit_arch_cache"
    __table_args__ = (UniqueConstraint("repo_id", "commit_sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(64), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)  # JSON: full architecture payload
    generated_at: Mapped[datetime] = mapped_column(DateTime)


class Artifact(Base):
    """An artifact a session published - a claude.ai artifact (built-in
    Artifact tool or claude-artifacts MCP) or a Claude Doc. Indexed from
    transcripts and persisted here so it outlives transcript cleanup. The
    local copy (when one could be captured) lives under
    ~/.cerberus-watchtower/artifacts/<id>/current/."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # service uuid
    kind: Mapped[str] = mapped_column(String(16))  # artifact | doc
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    favicon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sharing: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # ok | stale (an edit could not be replayed) | missing (source gone) | none
    content_status: Mapped[str] = mapped_column(String(16), default="none")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    op_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ArtifactOp(Base):
    """One publish/edit/delete of an artifact, attributed to the session that
    made it. tool_use_id makes ingestion idempotent across rescans."""

    __tablename__ = "artifact_ops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_use_id: Mapped[str] = mapped_column(String(64), unique=True)
    artifact_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(16))  # publish | edit | delete
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)


class SummaryCache(Base):
    """AI diff summaries, keyed by repo + diff content hash so a summary is
    only regenerated when the pending changes actually change."""

    __tablename__ = "summary_cache"
    __table_args__ = (UniqueConstraint("repo_id", "diff_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(64), index=True)
    diff_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime)
