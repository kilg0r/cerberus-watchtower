"""Repo registry: auto-discovers git repositories under configured roots.

`repos.toml` declares roots (directories whose child git repos are picked up
automatically) plus per-folder overrides for id/name/stack/group and explicit
includes for non-git projects. Stack is inferred from repo contents when not
overridden, so a freshly cloned repo shows up on the next registry refresh
with a sensible stack.
"""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path.home() / ".cerberus-watchtower"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

_SKIP_DIR_PREFIXES = ("_", ".")
_SKIP_DIR_NAMES = {"dist", "node_modules", "research", "scripts", "documentacion"}


@dataclass(frozen=True)
class RepoConfig:
    id: str
    name: str
    path: Path
    stack: str
    group: str


def _kebab(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


def infer_stack(path: Path) -> str:
    """Best-effort stack detection from repo contents."""
    if next(iter(path.glob("*.sln")), None):
        return "dotnet"
    pkg = path / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except (json.JSONDecodeError, OSError):
            deps = {}
        return "vue" if "vue" in deps else "web"
    if any((path / f).is_file() for f in ("pyproject.toml", "requirements.txt", "setup.py")):
        return "python"
    if next(iter(path.glob("*.tf")), None) or (path / "terraform").is_dir():
        return "terraform"
    if any((path / f).is_file() for f in ("settings.gradle", "settings.gradle.kts", "gradlew")):
        return "android"
    return "mixed"


def load_registry(config_path: Path | None = None) -> list[RepoConfig]:
    path = config_path or PROJECT_ROOT / "repos.toml"
    with path.open("rb") as f:
        data = tomllib.load(f)

    overrides: dict[str, dict] = data.get("overrides", {})
    repos: dict[str, RepoConfig] = {}

    # auto-discovery under roots
    for root_cfg in data.get("roots", []):
        root = Path(root_cfg["path"])
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith(_SKIP_DIR_PREFIXES) or child.name in _SKIP_DIR_NAMES:
                continue
            override = overrides.get(child.name, {})
            is_git = (child / ".git").exists()
            if not is_git and not override.get("include", False):
                continue
            repo_id = override.get("id", _kebab(child.name))
            repos[repo_id] = RepoConfig(
                id=repo_id,
                name=override.get("name", child.name),
                path=child,
                stack=override.get("stack", infer_stack(child)),
                group=override.get("group", root_cfg.get("group", "other")),
            )

    # explicit additions outside any root
    for entry in data.get("repos", []):
        repos[entry["id"]] = RepoConfig(
            id=entry["id"],
            name=entry["name"],
            path=Path(entry["path"]),
            stack=entry.get("stack", infer_stack(Path(entry["path"]))),
            group=entry.get("group", "other"),
        )

    return sorted(repos.values(), key=lambda r: (r.group, r.name.lower()))


_registry_cache: tuple[RepoConfig, ...] | None = None


def registry() -> tuple[RepoConfig, ...]:
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = tuple(load_registry())
    return _registry_cache


def refresh_registry() -> tuple[RepoConfig, ...]:
    """Re-discover repos under the configured roots."""
    global _registry_cache
    _registry_cache = None
    return registry()


def repo_by_id(repo_id: str) -> RepoConfig | None:
    return next((r for r in registry() if r.id == repo_id), None)
