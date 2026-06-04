from pathlib import Path

from watchtower.config import load_registry


def test_load_registry_parses_repos(tmp_path: Path):
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
    assert repo.name == "Demo Repo"
    assert repo.path == Path(r"C:\somewhere\demo")
    assert repo.stack == "python"
    assert repo.group == "cerberus"


def test_load_registry_default_file():
    repos = load_registry()
    assert len(repos) == 15
    assert {r.group for r in repos} == {"paytable", "cerberus", "personal"}
    assert len({r.id for r in repos}) == len(repos)  # ids unique
