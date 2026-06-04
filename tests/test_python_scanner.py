from pathlib import Path

import pytest

from watchtower.scanners.python_scanner import analyze


@pytest.fixture
def python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "pydemo"
    pkg = repo / "pydemo"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text("from pydemo.app import run\nrun()\n", encoding="utf-8")
    (pkg / "app.py").write_text(
        "from fastapi import FastAPI\n"
        "from .sub.helpers import helper\n"
        "import pydemo.config\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n    return {'ok': True}\n\n"
        "class Runner:\n    pass\n\n"
        "def run():\n    helper()\n",
        encoding="utf-8",
    )
    (pkg / "config.py").write_text("SETTING = 1\n", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "helpers.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "requirements.txt").write_text(
        "fastapi>=0.110\nuvicorn  # server\n", encoding="utf-8"
    )
    (repo / "strategies").mkdir()
    (repo / "strategies" / "alpha.yaml").write_text("kind: demo\n", encoding="utf-8")
    return repo


def test_analyze_builds_import_graph(python_repo: Path):
    result = analyze(python_repo)
    assert result is not None
    assert result["stack"] == "python"
    assert result["packages"] == ["pydemo"]
    assert result["entry_points"] == ["pydemo"]

    ids = {n["id"] for n in result["graph"]["nodes"]}
    assert {"pydemo", "pydemo.app", "pydemo.config", "pydemo.sub.helpers"} <= ids

    edges = {(e["source"], e["target"]) for e in result["graph"]["edges"]}
    assert ("pydemo.app", "pydemo.sub.helpers") in edges  # relative import resolved
    assert ("pydemo.app", "pydemo.config") in edges       # absolute import
    assert ("pydemo.__main__", "pydemo.app") in edges


def test_analyze_finds_deps_endpoints_configs(python_repo: Path):
    result = analyze(python_repo)
    assert result["dependencies"]["fastapi"] == ">=0.110"
    assert "uvicorn" in result["dependencies"]
    assert {"verb": "GET", "route": "/health", "module": "pydemo.app"} in result["endpoints"]
    assert any(c["path"] == "strategies/alpha.yaml" for c in result["config_files"])


def test_analyze_non_python_repo_returns_none(tmp_path: Path):
    (tmp_path / "readme.md").write_text("hi", encoding="utf-8")
    assert analyze(tmp_path) is None
