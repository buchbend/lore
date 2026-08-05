"""`lore migrate retire-session-notes` — backfill the ledger, drop the notes.

The command deletes files, so the plan is the contract: what a dry run
prints is exactly what `--apply` does. Every test runs against a fixture
vault built under tmp_path.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_curator.retire_session_notes import apply_retirement, plan_retirement
from typer.testing import CliRunner


def _transcript(path: Path, *, branch: str, edited: str, sha: str) -> None:
    """A minimal Claude Code JSONL: one edit, one committing bash call."""
    lines = [
        {
            "type": "user",
            "gitBranch": branch,
            "timestamp": "2026-08-01T09:00:00Z",
            "message": {"role": "user", "content": "fix #358"},
        },
        {
            "type": "assistant",
            "gitBranch": branch,
            "timestamp": "2026-08-01T09:01:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "c1",
                        "name": "Edit",
                        "input": {"file_path": edited},
                    },
                ],
            },
        },
        {
            "type": "assistant",
            "gitBranch": branch,
            "timestamp": "2026-08-01T09:02:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "b1",
                        "name": "Bash",
                        "input": {"command": 'git commit -m "done"'},
                    },
                ],
            },
        },
        {
            "type": "user",
            "gitBranch": branch,
            "timestamp": "2026-08-01T09:03:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "b1",
                        "content": f"[{branch} {sha}] done\n 1 file changed",
                    },
                ],
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def _note(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: session\ntitle: {title}\n---\n\nbody\n")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """A vault with one archived transcript and three session notes."""
    lore_root = tmp_path / "vault"
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    _transcript(
        project / "t1.jsonl",
        branch="feat/358-ledger",
        edited=str(project / "lib" / "a.py"),
        sha="abc1234",
    )

    TranscriptLedger(lore_root).upsert(
        TranscriptLedgerEntry(
            integration="claude-code",
            transcript_id="t1",
            path=project / "t1.jsonl",
            directory=project,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            curator_a_run=None,
            noteworthy=True,
            session_note="[[01-0900-thing]]",
        )
    )

    sessions = lore_root / "wiki" / "demo" / "sessions"
    _note(sessions / "2026" / "08" / "01-0900-thing.md", "thing")
    _note(sessions / "2026" / "08" / "02-1000-other.md", "other")
    _note(sessions / "_recent.md", "index")
    _note(lore_root / "wiki" / "demo" / "concepts" / "keeper.md", "keeper")
    return lore_root


def test_plan_lists_every_session_note_and_leaves_the_vault_untouched(vault: Path) -> None:
    """AC3: a dry run changes nothing."""
    before = sorted(p for p in vault.rglob("*.md"))

    plan = plan_retirement(vault)

    assert [p.name for p in plan.deletions] == [
        "01-0900-thing.md", "02-1000-other.md", "_recent.md",
    ]
    assert sorted(vault.rglob("*.md")) == before
    assert TranscriptLedger(vault).get("claude-code", "t1").linkage == {}


def test_plan_never_lists_a_note_outside_the_sessions_tree(vault: Path) -> None:
    assert not any("concepts" in p.parts for p in plan_retirement(vault).deletions)


def test_apply_backfills_linkage_from_the_transcript_then_deletes_the_notes(vault: Path) -> None:
    """AC4: linkage first, deletion second."""
    report = apply_retirement(vault, plan_retirement(vault))

    linkage = TranscriptLedger(vault).get("claude-code", "t1").linkage
    assert linkage["branch"] == "feat/358-ledger"
    assert linkage["issues"] == [358]
    assert linkage["commits"] == ["abc1234"]
    assert linkage["files"] == [str(vault.parent / "proj" / "lib" / "a.py")]

    assert list((vault / "wiki" / "demo").rglob("sessions/**/*.md")) == []
    assert (vault / "wiki" / "demo" / "concepts" / "keeper.md").exists()
    assert report.deleted == 3
    assert report.backfilled == 1


def test_applied_deletions_are_exactly_what_the_plan_listed(vault: Path) -> None:
    plan = plan_retirement(vault)
    report = apply_retirement(vault, plan)
    assert report.deleted_paths == plan.deletions


def test_apply_leaves_a_non_note_file_in_the_sessions_tree_alone(vault: Path) -> None:
    """Defensive: only ``.md`` files go. Anything else is reported, not removed."""
    stray = vault / "wiki" / "demo" / "sessions" / "keep.json"
    stray.write_text("{}")

    plan = plan_retirement(vault)
    apply_retirement(vault, plan)

    assert stray.exists()
    assert plan.kept == [stray]


def test_wiki_filter_scopes_the_plan(vault: Path) -> None:
    _note(vault / "wiki" / "other" / "sessions" / "2026" / "08" / "03-1100-x.md", "x")
    assert [p.name for p in plan_retirement(vault, wiki="other").deletions] == ["03-1100-x.md"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_without_apply_prints_the_plan_and_changes_nothing(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3 at the verb."""
    from lore_cli import migrate_cmd

    monkeypatch.setenv("LORE_ROOT", str(vault))
    result = CliRunner().invoke(migrate_cmd.app, ["retire-session-notes"])

    assert result.exit_code == 0
    assert "--apply" in result.output
    assert "01-0900-thing.md" in result.output
    assert (vault / "wiki" / "demo" / "sessions" / "2026" / "08" / "01-0900-thing.md").exists()


def test_cli_with_apply_deletes(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4 at the verb."""
    from lore_cli import migrate_cmd

    monkeypatch.setenv("LORE_ROOT", str(vault))
    result = CliRunner().invoke(migrate_cmd.app, ["retire-session-notes", "--apply"])

    assert result.exit_code == 0
    assert not (vault / "wiki" / "demo" / "sessions" / "2026" / "08" / "01-0900-thing.md").exists()
    assert TranscriptLedger(vault).get("claude-code", "t1").linkage["issues"] == [358]
