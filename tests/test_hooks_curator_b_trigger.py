"""Kill switch: SessionStart no longer auto-triggers Curator B.

Curator B's day-rollover spawn used to fire unconditionally (no config
flag) from `cmd_session_start`. It is now a severed entry point —
SessionStart never calls `_spawn_detached_curator_b`, regardless of the
wiki ledger's `last_curator_b` state. The underlying spawn machinery
(`_spawn_detached_curator_b` itself, in `lore_cli.spawn`) is untouched;
only the automatic call site is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lore_cli.hooks import hook_app
from lore_core.ledger import WikiLedger, WikiLedgerEntry
from typer.testing import CliRunner

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
    """Create a directory registered as an attachment (Phase 6 registry)."""
    from lore_core.state.attachments import Attachment, AttachmentsFile

    project = root / "project"
    project.mkdir()
    # wiki directory so _infer_lore_root walks up correctly
    (project / "wiki" / "testwiki").mkdir(parents=True)
    (project / ".lore").mkdir(parents=True, exist_ok=True)
    af = AttachmentsFile(project)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki="testwiki",
            scope="testscope",
            attached_at=_now(),
            source="manual",
        )
    )
    af.save()
    return project


def _now() -> datetime:
    return datetime.now(tz=UTC)


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


def test_session_start_never_spawns_curator_b_when_stale(tmp_path: Path) -> None:
    """Even with a stale (yesterday's) last_curator_b, no spawn happens."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_b=_yesterday()))

    calls = []

    def mock_spawn(lore_root: Path, wiki: str, **kw):
        calls.append((lore_root, wiki))
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert calls == [], f"Curator B entry point must be severed; got {calls}"


def test_session_start_never_spawns_curator_b_when_never_run(tmp_path: Path) -> None:
    """Even with last_curator_b=None (never run), no spawn happens."""
    project = _make_attached_project(tmp_path)
    lore_root = project

    wledger = WikiLedger(lore_root, "testwiki")
    wledger.write(WikiLedgerEntry(wiki="testwiki", last_curator_b=None))

    calls = []

    def mock_spawn(lore_root: Path, wiki: str, **kw):
        calls.append((lore_root, wiki))
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(lore_root)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert calls == [], f"Curator B entry point must be severed; got {calls}"


def test_session_start_does_not_spawn_when_unattached(tmp_path: Path) -> None:
    """Session-start does NOT spawn when cwd is unattached (no ## Lore block)."""
    unattached = tmp_path / "unattached"
    unattached.mkdir()

    calls = []

    def mock_spawn(lore_root: Path, wiki: str, **kw):
        calls.append((lore_root, wiki))
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn):
        result = runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(unattached), "--plain"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert calls == [], f"Expected no spawn calls, got {calls}"
