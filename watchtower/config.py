"""Repo registry and app paths."""

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path.home() / ".cerberus-watchtower"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@dataclass(frozen=True)
class RepoConfig:
    id: str
    name: str
    path: Path
    stack: str
    group: str


def load_registry(config_path: Path | None = None) -> list[RepoConfig]:
    path = config_path or PROJECT_ROOT / "repos.toml"
    with path.open("rb") as f:
        data = tomllib.load(f)
    return [
        RepoConfig(
            id=entry["id"],
            name=entry["name"],
            path=Path(entry["path"]),
            stack=entry["stack"],
            group=entry["group"],
        )
        for entry in data.get("repos", [])
    ]


@lru_cache(maxsize=1)
def registry() -> tuple[RepoConfig, ...]:
    return tuple(load_registry())


def repo_by_id(repo_id: str) -> RepoConfig | None:
    return next((r for r in registry() if r.id == repo_id), None)
