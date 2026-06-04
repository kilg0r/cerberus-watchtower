"""Static architecture analysis for Vue 3 / Vite repos.

Extracts: npm dependencies, router routes -> components, stores, and API call
sites. Regex heuristics over src/ - same philosophy as the .NET scanner:
always-current map, not a compiler-grade model.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {"node_modules", "dist", ".git"}

_ROUTE_PATH_RE = re.compile(r"path:\s*['\"]([^'\"]+)['\"]")
_ROUTE_NAME_RE = re.compile(r"name:\s*['\"]([^'\"]+)['\"]")
_COMPONENT_RE = re.compile(
    r"component:\s*(?:(\w+)|\(\)\s*=>\s*import\(['\"]([^'\"]+)['\"]\))"
)
# Any client.get/post/... or fetch-style call; the "/ or http" prefix filter in
# parse_api_calls screens out Map.get()-style false positives.
_API_CALL_RE = re.compile(
    r"(?:\bfetch|\bapiFetch|\w+\.(?:get|post|put|patch|delete))\(\s*[`'\"]([^`'\"]+)"
)


def _src_files(root: Path, suffixes: tuple[str, ...]):
    src = root / "src"
    base = src if src.is_dir() else root
    for path in base.rglob("*"):
        if path.suffix in suffixes and not SKIP_DIRS.intersection(
            path.relative_to(base).parts
        ):
            yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_package_json(repo_path: Path) -> dict:
    pkg_path = repo_path / "package.json"
    try:
        pkg = json.loads(_read(pkg_path) or "{}")
    except json.JSONDecodeError:
        pkg = {}
    return {
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }


def parse_routes(repo_path: Path) -> list[dict]:
    """Pair each `path:` with the nearest following name/component before the
    next `path:` - good enough for standard route tables."""
    routes = []
    for file in _src_files(repo_path, (".js", ".ts")):
        if "router" not in str(file).lower():
            continue
        content = _read(file)
        path_matches = list(_ROUTE_PATH_RE.finditer(content))
        for i, match in enumerate(path_matches):
            end = path_matches[i + 1].start() if i + 1 < len(path_matches) else len(content)
            section = content[match.start() : end]
            name = _ROUTE_NAME_RE.search(section)
            component = _COMPONENT_RE.search(section)
            routes.append(
                {
                    "path": match.group(1),
                    "name": name.group(1) if name else None,
                    "component": (component.group(1) or component.group(2))
                    if component
                    else None,
                }
            )
    return routes


def parse_api_calls(repo_path: Path) -> list[dict]:
    calls = []
    for file in _src_files(repo_path, (".js", ".ts", ".vue")):
        content = _read(file)
        for match in _API_CALL_RE.finditer(content):
            url = match.group(1)
            if url.startswith("/") or url.startswith("http"):
                calls.append({"url": url, "file": file.name})
    # dedupe, keep first file seen per url
    seen, unique = set(), []
    for call in calls:
        if call["url"] not in seen:
            seen.add(call["url"])
            unique.append(call)
    return unique


def _count(repo_path: Path, subdir: str, suffixes: tuple[str, ...]) -> list[str]:
    directory = repo_path / "src" / subdir
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.rglob("*") if p.suffix in suffixes)


def analyze(repo_path: Path) -> dict | None:
    if not (repo_path / "package.json").is_file():
        return None
    packages = parse_package_json(repo_path)
    routes = parse_routes(repo_path)
    views = _count(repo_path, "views", (".vue",))
    components = _count(repo_path, "components", (".vue",))
    stores = _count(repo_path, "stores", (".js", ".ts")) or _count(
        repo_path, "store", (".js", ".ts")
    )
    api_calls = parse_api_calls(repo_path)
    return {
        "stack": "vue",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
        "routes": routes,
        "views": views,
        "components": components,
        "stores": stores,
        "api_calls": api_calls,
        "stats": {
            "dependencies": len(packages["dependencies"]),
            "routes": len(routes),
            "views": len(views),
            "components": len(components),
            "stores": len(stores),
            "api_calls": len(api_calls),
        },
    }
