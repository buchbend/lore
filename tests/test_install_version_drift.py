"""Tests for ``check_lore_version_match`` — issue #28.

The Claude Code plugin and the Python CLI binary update via
*different* channels (``claude plugin update`` vs ``pipx install``).
Drift between them silently shows the old version in SessionStart's
status line. ``lore install`` and ``lore doctor`` now run a
version-drift check; this file pins the contract.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from lore_core.install._helpers import check_lore_version_match


def _write_pyproject(repo: Path, version: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "pyproject.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "lore"
        version = "{version}"
    """).strip())


def test_match_when_versions_agree(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "0.10.0")
    with patch("importlib.metadata.version", return_value="0.10.0"):
        ok, msg = check_lore_version_match(tmp_path)
    assert ok is True
    assert "0.10.0" in msg
    assert "matches source" in msg


def test_drift_when_installed_is_older(tmp_path: Path) -> None:
    """The exact issue-#28 scenario: stale binary, fresh source."""
    _write_pyproject(tmp_path, "0.10.0")
    with patch("importlib.metadata.version", return_value="0.2.4"):
        ok, msg = check_lore_version_match(tmp_path)
    assert ok is False
    assert "0.2.4" in msg and "0.10.0" in msg
    # Fix command must be copy-pasteable.
    assert f"pipx install --force --editable {tmp_path}" in msg
    # Mention the silent-failure mode so the user understands *why*.
    assert "older installed binary" in msg or "silently" in msg


def test_drift_when_installed_is_newer(tmp_path: Path) -> None:
    """Reverse case (rare but possible — local checkout out of date)."""
    _write_pyproject(tmp_path, "0.5.0")
    with patch("importlib.metadata.version", return_value="0.10.0"):
        ok, msg = check_lore_version_match(tmp_path)
    assert ok is False
    assert "0.10.0" in msg and "0.5.0" in msg


def test_no_repo_path_returns_ok(tmp_path: Path) -> None:
    """Non-editable installs (PyPI / no source clone) skip the drift
    check gracefully and report just the running version."""
    with patch("importlib.metadata.version", return_value="0.10.0"):
        ok, msg = check_lore_version_match(None)
    assert ok is True
    assert "0.10.0" in msg
    assert "no source tree" in msg


def test_repo_path_without_pyproject_returns_ok(tmp_path: Path) -> None:
    """A passed lore_repo path that doesn't actually contain a
    pyproject.toml (e.g. user pointed at the wrong dir) skips
    gracefully — don't fail the install over a misconfigured flag."""
    with patch("importlib.metadata.version", return_value="0.10.0"):
        ok, msg = check_lore_version_match(tmp_path)
    assert ok is True
    assert "no source tree" in msg


def test_package_not_installed_fails_with_install_hint() -> None:
    """If lore isn't installed in the running env at all (somehow we
    got here), the message tells the user how to fix it."""
    from importlib.metadata import PackageNotFoundError

    with patch(
        "importlib.metadata.version",
        side_effect=PackageNotFoundError("lore"),
    ):
        ok, msg = check_lore_version_match(None)
    assert ok is False
    assert "pipx install" in msg


def test_malformed_pyproject_does_not_crash(tmp_path: Path) -> None:
    """Bad pyproject.toml on disk should degrade to ok-with-note,
    not raise. We don't want a typo in pyproject to break installs."""
    (tmp_path / "pyproject.toml").write_text("not [valid] toml = {")
    with patch("importlib.metadata.version", return_value="0.10.0"):
        ok, msg = check_lore_version_match(tmp_path)
    assert ok is True
    assert "0.10.0" in msg
    assert "could not read" in msg.lower()


def test_doctor_check_finds_repo_via_walk_up() -> None:
    """The doctor check resolves the source tree by walking up from
    its own __file__ — the running pyproject.toml in this checkout
    should always be the one it finds."""
    from lore_cli.doctor_cmd import _check_lore_version_drift

    ok, msg = _check_lore_version_drift(cwd=".")
    # Either ok (versions match in this dev checkout) or a clear
    # drift message; the test just pins the shape, not the value.
    assert isinstance(ok, bool)
    assert "lore CLI version" in msg or "drift" in msg
