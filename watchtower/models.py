from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
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
