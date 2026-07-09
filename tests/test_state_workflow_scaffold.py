"""Tests for `lore_core.state.workflow_scaffold` — scaffold-run record.

Records only that a repo was scaffolded and when; the scaffold's own
idempotency comes from the filesystem checks in `lore_workflow.scaffold`
(file existence / shim sentinel), not from this record — this is an
observability record, not the source of truth.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.state.workflow_scaffold import WorkflowScaffoldFile


def test_record_then_reload_roundtrips(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    repo = tmp_path / "repo"

    scaffolded = WorkflowScaffoldFile(lore_root)
    scaffolded.load()
    scaffolded.record(repo)
    scaffolded.save()

    reloaded = WorkflowScaffoldFile(lore_root)
    reloaded.load()
    assert reloaded.was_scaffolded(repo)


def test_unrecorded_repo_returns_false(tmp_path: Path) -> None:
    lore_root = tmp_path / "vault"
    scaffolded = WorkflowScaffoldFile(lore_root)
    scaffolded.load()
    assert scaffolded.was_scaffolded(tmp_path / "never-scaffolded") is False


def test_record_is_idempotent(tmp_path: Path) -> None:
    """Recording the same repo twice keeps a single entry (last write wins)."""
    lore_root = tmp_path / "vault"
    repo = tmp_path / "repo"

    scaffolded = WorkflowScaffoldFile(lore_root)
    scaffolded.load()
    scaffolded.record(repo)
    scaffolded.record(repo)
    scaffolded.save()

    reloaded = WorkflowScaffoldFile(lore_root)
    reloaded.load()
    assert reloaded.all_paths() == [repo.resolve()]
