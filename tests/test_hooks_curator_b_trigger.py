"""Tests for SessionStart auto-trigger of Curator B on calendar-day rollover."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lore_cli.hooks import hook_app
from lore_core.ledger import WikiLedger, WikiLedgerEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LORE_BLOCK = """\
# Project

## Lore

<!-- managed by /lore:attach -->

- wiki: testwiki
- scope: testscope
- backend: none
"""


def _make_attached_project(root: Path) -> Path:
    """Create a directory with an attached CLAUDE.md and required wiki layout."""
    project = root / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(LORE_BLOCK)
    # wiki directory so _infer_lore_root walks up correctly
    (project / "wiki" / "testwiki").mkdir(parents=True)
    # Initialize the .lore directory for ledger
    (project / ".lore").mkdir(parents=True, exist_ok=True)
    return project


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _today() -> datetime:
    """Return midnight UTC for today."""
    now = _now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _yesterday() -> datetime:
    """Return midnight UTC for yesterday."""
    return _today() - timedelta(days=1)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

runner = CliRunner()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_session_start_spawns_curator_b_on_new_day(tmp_path: Path) -> None:
    """Session-start spawns Curator B when last_curator_b is from yesterday."""
    project = _make_attached_project(tmp_path)
    lore_root = project  # In test setup, project is the lore_root

    # Pre-populate wiki ledger with last_curator_b from yesterday
    wledger = WikiLedger(lore_root, "testwiki")
    entry = WikiLedgerEntry(
        wiki="testwiki",
        last_curator_b=_yesterday(),
    )
    wledger.write(entry)

    # Monkeypatch _spawn_detached_curator_b to track calls
    calls = []

    def mock_spawn(lore_root: Path, wiki: str):
        calls.append((lore_root, wiki))

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1, f"Expected 1 spawn call, got {len(calls)}: {calls}"
    assert calls[0] == (lore_root, "testwiki")


def test_session_start_does_not_spawn_curator_b_same_day(tmp_path: Path) -> None:
    """Session-start does NOT spawn Curator B when last_curator_b is from today."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    # Pre-populate wiki ledger with last_curator_b from today
    wledger = WikiLedger(lore_root, "testwiki")
    entry = WikiLedgerEntry(
        wiki="testwiki",
        last_curator_b=_today(),
    )
    wledger.write(entry)

    calls = []

    def mock_spawn(lore_root: Path, wiki: str):
        calls.append((lore_root, wiki))

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(calls) == 0, f"Expected no spawn calls, got {len(calls)}: {calls}"


def test_session_start_spawns_curator_b_when_never_run(tmp_path: Path) -> None:
    """Session-start spawns Curator B when last_curator_b is None (never run)."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    # Pre-populate wiki ledger with last_curator_b=None
    wledger = WikiLedger(lore_root, "testwiki")
    entry = WikiLedgerEntry(
        wiki="testwiki",
        last_curator_b=None,
    )
    wledger.write(entry)

    calls = []

    def mock_spawn(lore_root: Path, wiki: str):
        calls.append((lore_root, wiki))

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1, f"Expected 1 spawn call, got {len(calls)}: {calls}"
    assert calls[0] == (lore_root, "testwiki")


def test_session_start_does_not_spawn_when_unattached(tmp_path: Path) -> None:
    """Session-start does NOT spawn when cwd is unattached (no ## Lore block)."""
    unattached = tmp_path / "unattached"
    unattached.mkdir()
    # No CLAUDE.md with ## Lore

    calls = []

    def mock_spawn(lore_root: Path, wiki: str):
        calls.append((lore_root, wiki))

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(unattached), "--plain"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    # No spawn should happen because scope resolution fails (unattached)
    assert len(calls) == 0, f"Expected no spawn calls, got {len(calls)}: {calls}"


def test_session_start_curator_b_spawn_does_not_break_hook_on_error(tmp_path: Path) -> None:
    """Session-start does NOT break if _spawn_detached_curator_b raises an error."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    # Pre-populate wiki ledger with last_curator_b from yesterday to trigger spawn
    wledger = WikiLedger(lore_root, "testwiki")
    entry = WikiLedgerEntry(
        wiki="testwiki",
        last_curator_b=_yesterday(),
    )
    wledger.write(entry)

    def mock_spawn_raises(lore_root: Path, wiki: str):
        raise RuntimeError("Intentional spawn failure for testing")

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn_raises):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    # Hook should still exit 0 even though spawn raised
    assert result.exit_code == 0, f"Hook should not fail on spawn error. Output: {result.output}"
