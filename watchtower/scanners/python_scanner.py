"""Static architecture analysis for Python repos.

Builds an internal-module import graph (the Python equivalent of the .NET
project graph), plus dependencies, web endpoints, entry points, and the config
file inventory that drives config-heavy apps like cerberus-markets.
"""

import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", "dist",
             "build", ".pytest_cache", ".idea", ".serena", "tests", "test"}
CONFIG_SUFFIXES = {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env"}
CONFIG_SKIP_NAMES = {"package.json", "package-lock.json", "tsconfig.json"}

_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)
_ENDPOINT_RE = re.compile(
    r"@[\w.]+\.(get|post|put|patch|delete|websocket|route)\(\s*[\"']([^\"']+)"
)
_CLASS_RE = re.compile(r"^class\s+\w+", re.MULTILINE)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def find_packages(repo_path: Path) -> list[Path]:
    """Top-level python packages: dirs with __init__.py at root or under src/."""
    packages = []
    for base in (repo_path, repo_path / "src"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and child.name not in SKIP_DIRS and (child / "__init__.py").is_file():
                packages.append(child)
    return packages


def _module_id(package: Path, file: Path) -> str:
    rel = file.relative_to(package.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(module_id: str, target: str) -> str:
    """Resolve `from .sibling import x` style imports against the module path."""
    if not target.startswith("."):
        return target
    dots = len(target) - len(target.lstrip("."))
    base = module_id.split(".")[: -dots or None]
    suffix = target.lstrip(".")
    return ".".join(part for part in [*base, suffix] if part)


def build_import_graph(packages: list[Path]) -> tuple[list[dict], list[dict]]:
    nodes, contents = [], {}
    for package in packages:
        for file in package.rglob("*.py"):
            if SKIP_DIRS.intersection(file.relative_to(package).parts):
                continue
            module_id = _module_id(package, file)
            content = _read(file)
            contents[module_id] = content
            nodes.append(
                {
                    "id": module_id,
                    "label": module_id.split(".")[-1] or module_id,
                    "kind": "package" if file.name == "__init__.py" else "module",
                    "package": module_id.split(".")[0],
                    "loc": content.count("\n") + 1,
                    "classes": len(_CLASS_RE.findall(content)),
                }
            )

    known = sorted((n["id"] for n in nodes), key=len, reverse=True)
    edges, seen = [], set()
    for module_id, content in contents.items():
        for match in _IMPORT_RE.finditer(content):
            target = _resolve_relative(module_id, match.group(1) or match.group(2))
            resolved = next(
                (k for k in known if target == k or target.startswith(k + ".")), None
            )
            if resolved and resolved != module_id and (module_id, resolved) not in seen:
                seen.add((module_id, resolved))
                edges.append({"source": module_id, "target": resolved, "kind": "import"})
    return nodes, edges


def parse_dependencies(repo_path: Path) -> dict[str, str]:
    deps: dict[str, str] = {}
    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_read(pyproject))
            for spec in data.get("project", {}).get("dependencies", []):
                match = re.match(r"([\w][\w.-]*)\s*(.*)", spec)
                if match:
                    deps[match.group(1)] = match.group(2).strip()
        except tomllib.TOMLDecodeError:
            pass
    requirements = repo_path / "requirements.txt"
    if requirements.is_file():
        for line in _read(requirements).splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            match = re.match(r"([\w][\w.-]*)\s*(.*)", line)
            if match:
                deps.setdefault(match.group(1), match.group(2).strip())
    return deps


def find_endpoints(packages: list[Path]) -> list[dict]:
    endpoints = []
    for package in packages:
        for file in package.rglob("*.py"):
            if SKIP_DIRS.intersection(file.relative_to(package).parts):
                continue
            for match in _ENDPOINT_RE.finditer(_read(file)):
                endpoints.append(
                    {
                        "verb": match.group(1).upper(),
                        "route": match.group(2),
                        "module": _module_id(package, file),
                    }
                )
    return endpoints


def find_config_files(repo_path: Path, limit: int = 60) -> list[dict]:
    """Config inventory - the architecture of config-driven apps."""
    configs = []
    for file in sorted(repo_path.rglob("*")):
        rel_parts = file.relative_to(repo_path).parts
        if SKIP_DIRS.intersection(rel_parts) or len(rel_parts) > 3:
            continue
        if file.is_file() and file.suffix in CONFIG_SUFFIXES and file.name not in CONFIG_SKIP_NAMES:
            try:
                size = file.stat().st_size
            except OSError:
                size = 0
            configs.append(
                {"path": "/".join(rel_parts), "kind": file.suffix.lstrip("."), "size": size}
            )
            if len(configs) >= limit:
                break
    return configs


def analyze(repo_path: Path) -> dict | None:
    packages = find_packages(repo_path)
    has_python = bool(packages) or next(iter(repo_path.glob("*.py")), None) is not None
    if not has_python:
        return None
    nodes, edges = build_import_graph(packages)
    dependencies = parse_dependencies(repo_path)
    endpoints = find_endpoints(packages)
    configs = find_config_files(repo_path)
    entry_points = sorted(
        p.parent.name for p in (pkg / "__main__.py" for pkg in packages) if p.is_file()
    )
    return {
        "stack": "python",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "graph": {"nodes": nodes, "edges": edges},
        "packages": sorted(p.name for p in packages),
        "dependencies": dependencies,
        "endpoints": endpoints,
        "config_files": configs,
        "entry_points": entry_points,
        "stats": {
            "packages": len(packages),
            "modules": len(nodes),
            "imports": len(edges),
            "dependencies": len(dependencies),
            "endpoints": len(endpoints),
            "config_files": len(configs),
        },
    }
