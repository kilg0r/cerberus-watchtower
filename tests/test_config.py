import tomllib
from pathlib import Path

from watchtower.config import PROJECT_ROOT, infer_stack, load_registry

# The folder layout on the Windows desktop, and the repo id each folder must
# keep resolving to. Caches, drift snapshots and narratives are keyed by id, so
# a repos.toml edit that renames one of these silently orphans its history.
WINDOWS_LAYOUT = {
    "_paytable": {
        "payTableDotnet": "paytable-dotnet",
        "payTableVue": "paytable-vue",
        "payTableShell": "paytable-shell",
        "paytablemx": "paytablemx",
        "paytable-infrastructure": "paytable-infrastructure",
        "paytable-claude-config": "paytable-claude-config",
    },
    "_cerberus": {
        "Cerberus": "cerberus",
        "cerberuslabsllc": "cerberuslabsllc",
        "cerberus-content-manager": "cerberus-content-manager",
        "cerberus-markets": "cerberus-markets",
        "cerberus-home-hub": "cerberus-home-hub",
        "cerberus-watchtower": "cerberus-watchtower",
        "cerberus-claude-config": "cerberus-claude-config",
        "cerberus-books": "cerberus-books",
        "rierawedding": "rierawedding",
    },
}


def test_load_registry_parses_explicit_repos(tmp_path: Path):
    toml = tmp_path / "repos.toml"
    toml.write_text(
        """
[[repos]]
id = "demo"
name = "Demo Repo"
path = 'C:\\somewhere\\demo'
stack = "python"
group = "cerberus"
""",
        encoding="utf-8",
    )
    repos = load_registry(toml)
    assert len(repos) == 1
    repo = repos[0]
    assert repo.id == "demo"
    assert repo.path == Path(r"C:\somewhere\demo")
    assert repo.stack == "python"


def test_auto_discovery_with_overrides(tmp_path: Path):
    root = tmp_path / "projects"
    # a git repo with a .sln -> discovered as dotnet
    (root / "myApiThing" / ".git").mkdir(parents=True)
    (root / "myApiThing" / "demo.sln").write_text("", encoding="utf-8")
    # a git repo with requirements.txt -> python
    (root / "tool" / ".git").mkdir(parents=True)
    (root / "tool" / "requirements.txt").write_text("requests\n", encoding="utf-8")
    # not a git repo, no override -> skipped
    (root / "scratch").mkdir()
    # not a git repo but explicitly included
    (root / "books").mkdir()
    # skipped by prefix
    (root / "_secrets").mkdir()

    toml = tmp_path / "repos.toml"
    toml.write_text(
        f"""
[[roots]]
path = '{root}'
group = "demo"

[overrides.myApiThing]
id = "my-api"
name = "My API"

[overrides.books]
include = true
stack = "python"
""",
        encoding="utf-8",
    )
    repos = {r.id: r for r in load_registry(toml)}
    assert set(repos) == {"my-api", "tool", "books"}
    assert repos["my-api"].stack == "dotnet"
    assert repos["my-api"].name == "My API"
    assert repos["tool"].stack == "python"
    assert repos["tool"].group == "demo"
    assert repos["books"].stack == "python"


def test_infer_stack(tmp_path: Path):
    (tmp_path / "main.tf").write_text("", encoding="utf-8")
    assert infer_stack(tmp_path) == "terraform"


def test_load_registry_default_file():
    repos = load_registry()
    ids = {r.id for r in repos}
    # known stable ids must survive auto-discovery (caches/snapshots key on them)
    assert {"paytable-dotnet", "paytable-vue", "cerberus-watchtower"} <= ids
    assert len(ids) == len(repos)  # ids unique
    assert {r.group for r in repos} >= {"paytable", "cerberus"}
    # every registered path must exist on this machine - the assertion that
    # catches a repos.toml carried over from another box.
    assert [r.id for r in repos if not r.path.is_dir()] == []


def test_committed_overrides_preserve_windows_repo_ids(tmp_path: Path):
    """repos.toml carries the roots for every machine, so an edit made on one
    box must not renumber the other's repos. Replays the Windows folder layout
    against the committed overrides and pins the ids it must produce."""
    real = tomllib.loads((PROJECT_ROOT / "repos.toml").read_text(encoding="utf-8"))
    overrides = real["overrides"]

    roots = []
    for root_name, folders in WINDOWS_LAYOUT.items():
        root = tmp_path / root_name
        for folder in folders:
            (root / folder / ".git").mkdir(parents=True)
        roots.append(f"[[roots]]\npath = '{root}'\ngroup = \"{root_name.lstrip('_')}\"\n")

    toml = tmp_path / "repos.toml"
    override_block = "\n".join(
        f"[overrides.{folder}]\n"
        + "\n".join(
            f"{k} = {v!r}" if not isinstance(v, bool) else f"{k} = {str(v).lower()}"
            for k, v in body.items()
        )
        for folder, body in overrides.items()
    )
    toml.write_text("\n".join(roots) + "\n" + override_block, encoding="utf-8")

    resolved = {r.path.name: r.id for r in load_registry(toml)}
    expected = {f: i for folders in WINDOWS_LAYOUT.values() for f, i in folders.items()}
    assert resolved == expected
