from pathlib import Path

from watchtower.config import infer_stack, load_registry


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
    assert {"paytable-dotnet", "paytable-vue", "cerberus-markets", "cerberus-books"} <= ids
    assert len(ids) == len(repos)  # ids unique
    assert {r.group for r in repos} >= {"paytable", "cerberus"}
