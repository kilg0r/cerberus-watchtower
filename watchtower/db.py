"""SQLite persistence at ~/.cerberus-watchtower/watchtower.db."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import DATA_DIR
from .models import Base

_session_factory: sessionmaker | None = None


def init_db(db_path: Path | None = None) -> sessionmaker:
    """Create tables (if needed) and return a session factory."""
    global _session_factory
    if db_path is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = DATA_DIR / "watchtower.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def get_session_factory() -> sessionmaker:
    if _session_factory is None:
        return init_db()
    return _session_factory
