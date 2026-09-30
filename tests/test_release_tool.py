"""Tests for `tools/release.py` — the version bump the release PR carries.

Imports the script by file path, the same way `test_undo_install_sh.py` does.

Only the pure text transforms are tested here. The git and `gh` calls are thin
subprocess wrappers around commands a maintainer runs by hand, and a test that
mocked them would assert the mock.

The edges that matter: a bump that hits the wrong `version` line ships a broken
`pyproject.toml`, and a rewritten `plugin.json` that loses its formatting shows
up as noise in every later diff.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "release.py"


def _load():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("release", None)
    spec.loader.exec_module(module)
    return module


release = _load()


PYPROJECT = """\
[project]
name = "lore"
version = "0.72.0"
requires-python = ">=3.11"
dependencies = ["ruff>=0.5"]

[tool.ruff]
line-length = 100
target-version = "py311"
"""

MANIFEST = """\
{
  "name": "lore",
  "description": "Knowledge graph",
  "version": "0.72.0",
  "author": {"name": "lore"}
}
"""

CHANGELOG = """\
# Changelog

Format: Keep a Changelog.

## [0.72.0] - 2026-08-11

Moves the flag review walk into a browser page.

### Added

- A browser page.
"""


@pytest.mark.parametrize(
    ("current", "part", "expected"),
    [
        ("0.72.0", "minor", "0.73.0"),
        ("0.72.3", "minor", "0.73.0"),
        ("0.72.0", "patch", "0.72.1"),
        ("0.72.4", "major", "1.0.0"),
    ],
)
def test_next_version_zeroes_the_lower_parts(current: str, part: str, expected: str) -> None:
    assert release.next_version(current, part) == expected


def test_next_version_refuses_a_string_that_is_not_semver() -> None:
    with pytest.raises(ValueError):
        release.next_version("0.72", "minor")


def test_bump_pyproject_moves_the_project_version_only() -> None:
    """`target-version = "py311"` also matches a loose version pattern, and a
    dependency pin holds a version too. Neither may move."""
    out = release.bump_pyproject(PYPROJECT, "0.73.0")
    assert 'version = "0.73.0"' in out
    assert 'target-version = "py311"' in out
    assert '"ruff>=0.5"' in out
    assert "0.72.0" not in out


def test_bump_manifest_keeps_every_other_byte() -> None:
    out = release.bump_manifest(MANIFEST, "0.73.0")
    assert json.loads(out)["version"] == "0.73.0"
    assert out == MANIFEST.replace("0.72.0", "0.73.0")


def test_insert_section_lands_above_the_newest_release() -> None:
    out = release.insert_section(CHANGELOG, "0.73.0", "2026-08-14", "- feat: a thing (#1)")
    assert out.startswith("# Changelog\n")
    assert out.index("## [0.73.0] - 2026-08-14") < out.index("## [0.72.0]")
    assert "- feat: a thing (#1)" in out


def test_insert_section_refuses_a_version_the_changelog_already_holds() -> None:
    with pytest.raises(ValueError):
        release.insert_section(CHANGELOG, "0.72.0", "2026-08-14", "- feat: a thing (#1)")


def test_pick_release_commit_reads_the_subject_only() -> None:
    """A squash merge carries every branch commit message in its body, and this
    repo's history holds a body line that quotes the release subject. Matching
    the body would cut the range short and drop released work."""
    log = "\n".join(
        [
            "aaa1\x00feat(flag): write flag text (#414)",
            "bbb2\x00tooling: commits `chore: release X.Y.Z` and opens the PR",
            "ccc3\x00chore: release 0.72.0 (#413)",
        ]
    )
    assert release.pick_release_commit(log) == "ccc3"


def test_pick_release_commit_returns_empty_when_no_release_landed() -> None:
    assert release.pick_release_commit("aaa1\x00feat: first commit") == ""


def test_read_version_reads_the_project_table() -> None:
    assert release.read_version(PYPROJECT) == "0.72.0"


def test_the_repo_files_survive_a_round_trip(tmp_path: Path) -> None:
    """The transforms run against this repo's own files, so a format change in
    any of the three breaks the test rather than the next release."""
    root = SCRIPT.parent.parent
    current = release.read_version((root / "pyproject.toml").read_text(encoding="utf-8"))
    following = release.next_version(current, "minor")

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    bumped = release.bump_pyproject(pyproject, following)
    assert release.read_version(bumped) == following

    manifest = (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    assert json.loads(release.bump_manifest(manifest, following))["version"] == following

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{following}]" in release.insert_section(changelog, following, "2026-01-01", "- x")


# --- --in-branch ------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def shipping_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone with an `origin/main` and a feature branch that holds one commit."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    for name in ("user.name", "user.email"):
        _git(work, "config", name, "test")
    (work / ".claude-plugin").mkdir()
    (work / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (work / ".claude-plugin" / "plugin.json").write_text(MANIFEST, encoding="utf-8")
    (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "chore: release 0.72.0")
    _git(work, "push", "origin", "main")
    _git(work, "checkout", "-b", "feat/thing")
    (work / "thing.txt").write_text("x", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "feat: a thing")

    monkeypatch.setattr(release, "REPO_ROOT", work)
    monkeypatch.setattr(release, "PYPROJECT", work / "pyproject.toml")
    monkeypatch.setattr(release, "MANIFEST", work / ".claude-plugin" / "plugin.json")
    monkeypatch.setattr(release, "CHANGELOG", work / "CHANGELOG.md")
    return work


def test_in_branch_adds_one_bump_commit_on_the_current_branch(
    shipping_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard_calls: list[int] = []
    monkeypatch.setattr(release, "run_version_guard", lambda: guard_calls.append(1))
    # No gh on PATH, and any gh call fails the test.
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    real_run = release.subprocess.run

    def no_gh(cmd, *a, **kw):
        assert cmd[0] != "gh", "--in-branch must not call gh"
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(release.subprocess, "run", no_gh)
    notes = tmp_path / "notes.md"
    notes.write_text("### Added\n\n- A thing.\n", encoding="utf-8")
    before = _git(shipping_repo, "rev-parse", "HEAD")

    assert release.main(["--in-branch", "--notes", str(notes)]) == 0

    assert _git(shipping_repo, "rev-parse", "--abbrev-ref", "HEAD") == "feat/thing"
    assert _git(shipping_repo, "rev-list", "--count", f"{before}..HEAD") == "1"
    assert _git(shipping_repo, "log", "-1", "--format=%s") == "chore: release 0.73.0"
    changed = set(_git(shipping_repo, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert changed == {"pyproject.toml", ".claude-plugin/plugin.json", "CHANGELOG.md"}
    assert 'version = "0.73.0"' in (shipping_repo / "pyproject.toml").read_text()
    assert "## [0.73.0]" in (shipping_repo / "CHANGELOG.md").read_text()
    assert guard_calls == [1]
    assert _git(shipping_repo, "status", "--porcelain") == ""
    assert "feat/thing" not in _git(shipping_repo, "branch", "-r")


@pytest.mark.parametrize("target", ["main", "--detach"])
def test_in_branch_refuses_main_and_detached_head(shipping_repo: Path, target: str) -> None:
    _git(shipping_repo, "checkout", *(["main"] if target == "main" else ["--detach"]))
    with pytest.raises(SystemExit) as exc:
        release.main(["--in-branch"])
    assert "feature branch" in str(exc.value)


def test_in_branch_refuses_a_branch_cut_before_main_released(
    shipping_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main released 0.73.0 after the branch was cut. Bumping the branch's own
    0.72.0 would repeat main's 0.73.0, so the script stops and says why."""
    monkeypatch.setattr(release, "run_version_guard", lambda: None)
    _git(shipping_repo, "checkout", "main")
    (shipping_repo / "pyproject.toml").write_text(
        PYPROJECT.replace("0.72.0", "0.73.0"), encoding="utf-8"
    )
    _git(shipping_repo, "commit", "-am", "chore: release 0.73.0")
    _git(shipping_repo, "push", "origin", "main")
    _git(shipping_repo, "checkout", "feat/thing")
    before = _git(shipping_repo, "rev-parse", "HEAD")
    notes = tmp_path / "notes.md"
    notes.write_text("### Added\n\n- A thing.\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        release.main(["--in-branch", "--notes", str(notes)])

    message = str(exc.value)
    assert "origin/main is at 0.73.0" in message
    assert "merge main into this branch first" in message
    assert _git(shipping_repo, "rev-parse", "HEAD") == before
    assert _git(shipping_repo, "status", "--porcelain") == ""
