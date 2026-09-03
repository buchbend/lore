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
