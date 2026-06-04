import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from watchtower.models import Base


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit, one modified file, and one untracked file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.local")
    _git(repo, "config", "user.name", "Test")

    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial commit")

    (repo / "app.py").write_text("print('hello')\nprint('world')\n", encoding="utf-8")
    (repo / "new_module.py").write_text("VALUE = 1\nOTHER = 2\n", encoding="utf-8")
    return repo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
