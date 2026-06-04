"""Fallback architecture analysis for any stack without a dedicated scanner
(terraform, android, web, mixed, config). Produces a structural inventory:
language breakdown, directory map, and the documentation index - so every
registered repo gets a relevant view instead of a 501.
"""

from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "bin", "obj",
             "dist", "build", ".idea", ".vs", ".serena", ".pytest_cache", "publish",
             "TestResults", ".gradle"}

LANGUAGE_NAMES = {
    ".cs": "C#", ".py": "Python", ".vue": "Vue", ".js": "JavaScript",
    ".ts": "TypeScript", ".swift": "Swift", ".kt": "Kotlin", ".java": "Java",
    ".tf": "Terraform", ".yaml": "YAML", ".yml": "YAML", ".json": "JSON",
    ".toml": "TOML", ".md": "Markdown", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".xml": "XML", ".ino": "Arduino", ".scad": "OpenSCAD", ".gradle": "Gradle",
}


def _walk(repo_path: Path):
    for path in repo_path.rglob("*"):
        if SKIP_DIRS.intersection(path.relative_to(repo_path).parts):
            continue
        if path.is_file():
            yield path


def analyze(repo_path: Path, stack: str = "mixed") -> dict | None:
    if not repo_path.is_dir():
        return None

    languages: dict[str, int] = {}
    dirs: dict[str, dict] = {}
    docs: list[str] = []

    for file in _walk(repo_path):
        rel = file.relative_to(repo_path)
        language = LANGUAGE_NAMES.get(file.suffix.lower())
        if language:
            languages[language] = languages.get(language, 0) + 1
        if file.suffix.lower() == ".md" and len(docs) < 60:
            docs.append(str(rel).replace("\\", "/"))

        top = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        entry = dirs.setdefault(top, {"files": 0, "languages": {}})
        entry["files"] += 1
        if language:
            entry["languages"][language] = entry["languages"].get(language, 0) + 1

    directory_map = [
        {
            "name": name,
            "files": info["files"],
            "main_language": max(info["languages"], key=info["languages"].get)
            if info["languages"]
            else None,
        }
        for name, info in sorted(dirs.items(), key=lambda kv: -kv[1]["files"])
    ]

    return {
        "stack": stack,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "languages": dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        "directories": directory_map[:25],
        "docs": sorted(docs),
        "stats": {
            "files": sum(info["files"] for info in dirs.values()),
            "directories": len(dirs),
            "languages": len(languages),
            "docs": len(docs),
        },
    }
