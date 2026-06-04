from pathlib import Path

from watchtower.config import RepoConfig
from watchtower.scanners.git_scanner import file_diff, scan_all, scan_repo


def _repo_config(path: Path) -> RepoConfig:
    return RepoConfig(id="test", name="Test Repo", path=path, stack="python", group="cerberus")


def test_scan_repo_reports_modified_and_untracked(temp_git_repo: Path):
    entry = scan_repo(_repo_config(temp_git_repo))

    assert entry is not None
    assert entry["repo_id"] == "test"
    assert entry["group"] == "cerberus"
    assert entry["branch"] == "main"
    assert entry["ahead"] == 0 and entry["behind"] == 0  # no upstream

    by_path = {f["path"]: f for f in entry["files"]}
    assert by_path["app.py"]["status"] == "modified"
    assert by_path["app.py"]["additions"] == 1
    assert by_path["app.py"]["deletions"] == 0
    assert by_path["new_module.py"]["status"] == "untracked"
    assert by_path["new_module.py"]["additions"] == 2

    assert entry["total_additions"] == 3
    assert entry["total_deletions"] == 0
    assert entry["last_commit"]["message"] == "initial commit"
    assert entry["scanned_at"]


def test_scan_repo_clean_repo_returns_none(temp_git_repo: Path):
    import subprocess

    subprocess.run(
        ["git", "-C", str(temp_git_repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "commit", "-m", "checkpoint"],
        check=True,
        capture_output=True,
    )
    assert scan_repo(_repo_config(temp_git_repo)) is None


def test_scan_repo_missing_path_returns_none(tmp_path: Path):
    assert scan_repo(_repo_config(tmp_path / "does-not-exist")) is None


def test_scan_repo_non_git_dir_returns_none(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "file.txt").write_text("hi", encoding="utf-8")
    assert scan_repo(_repo_config(plain)) is None


def test_file_diff_tracked_modification(temp_git_repo: Path):
    diff = file_diff(temp_git_repo, "app.py")
    assert diff is not None
    assert "+print('world')" in diff
    assert "-" not in [line[:1] for line in diff.splitlines() if line.startswith("-print")]


def test_file_diff_untracked_pseudo_diff(temp_git_repo: Path):
    diff = file_diff(temp_git_repo, "new_module.py")
    assert diff is not None
    assert "--- /dev/null" in diff
    assert "+VALUE = 1" in diff
    assert "+OTHER = 2" in diff


def test_file_diff_unknown_or_clean_returns_none(temp_git_repo: Path):
    assert file_diff(temp_git_repo, "missing.py") is None


def test_file_diff_path_escape_returns_none(temp_git_repo: Path, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    assert file_diff(temp_git_repo, "..\\outside.txt") is None


def test_scan_all_drops_clean_and_missing(temp_git_repo: Path, tmp_path: Path):
    repos = [
        _repo_config(temp_git_repo),
        RepoConfig(id="gone", name="Gone", path=tmp_path / "nope", stack="python", group="cerberus"),
    ]
    results = scan_all(repos)
    assert [r["repo_id"] for r in results] == ["test"]
