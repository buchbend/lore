"""Tests for `lore drain prune` — drops orphan `_system.jsonl` rows."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.drain_cmd import app
from lore_core.drain import SYSTEM_SESSION


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch) -> Path:
    """A vault with `.lore/drain/` ready and `LORE_ROOT` set so the CLI
    finds it without needing a configured wiki."""
    drain_dir = tmp_path / ".lore" / "drain"
    drain_dir.mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _write_row(lore_root: Path, *, event: str, wiki: str = "private",
                ts: datetime | None = None, **data) -> None:
    """Append a raw row to `_system.jsonl` (bypassing emit guards so we
    can plant any event type the prune logic should handle)."""
    path = lore_root / ".lore" / "drain" / f"{SYSTEM_SESSION}.jsonl"
    record = {
        "ts": (ts or datetime.now(UTC)).isoformat(),
        "event": event,
        "wiki": wiki,
        "session_id": SYSTEM_SESSION,
        "data": data,
    }
    with path.open("a") as fp:
        fp.write(json.dumps(record) + "\n")


def _read_rows(lore_root: Path) -> list[dict]:
    path = lore_root / ".lore" / "drain" / f"{SYSTEM_SESSION}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_prune_drops_orphan_note_filed_row(lore_root, runner):
    """The flagship case: a note-filed row whose path is gone."""
    _write_row(
        lore_root, event="note-filed",
        wikilink="[[debug-test-29-event]]", path="/tmp/does-not-exist.md",
    )
    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "pruned 1 orphan row" in result.stdout
    assert _read_rows(lore_root) == []


def test_prune_keeps_rows_whose_path_exists(lore_root, runner, tmp_path):
    real = tmp_path / "real-note.md"
    real.write_text("hi")
    _write_row(
        lore_root, event="note-filed",
        wikilink="[[real]]", path=str(real),
    )
    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "nothing to prune" in result.stdout
    rows = _read_rows(lore_root)
    assert len(rows) == 1
    assert rows[0]["data"]["wikilink"] == "[[real]]"


def test_prune_keeps_transcript_synced_unconditionally(lore_root, runner):
    """transcript-synced has no path field; prune must not drop it."""
    _write_row(
        lore_root, event="transcript-synced", transcript_id="t1",
    )
    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "nothing to prune" in result.stdout
    assert len(_read_rows(lore_root)) == 1


def test_prune_keeps_rows_without_path(lore_root, runner):
    """A note-style row with no `data.path` is suspicious but kept —
    prune evicts on path-existence, not on schema completeness."""
    _write_row(
        lore_root, event="note-filed", wikilink="[[no-path]]",
    )
    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "nothing to prune" in result.stdout
    assert len(_read_rows(lore_root)) == 1


def test_prune_preserves_malformed_lines(lore_root, runner):
    """Malformed JSON is kept verbatim — prune is not a validator."""
    path = lore_root / ".lore" / "drain" / f"{SYSTEM_SESSION}.jsonl"
    _write_row(
        lore_root, event="note-filed",
        wikilink="[[orphan]]", path="/tmp/gone.md",
    )
    with path.open("a") as fp:
        fp.write("NOT JSON\n")
    _write_row(lore_root, event="transcript-synced", transcript_id="t2")

    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "pruned 1 orphan row" in result.stdout

    raw = path.read_text().splitlines()
    assert "NOT JSON" in raw
    # The orphan is gone; transcript-synced survives.
    assert all("[[orphan]]" not in line for line in raw)
    assert any("transcript-synced" in line for line in raw)


def test_prune_dry_run_does_not_modify_file(lore_root, runner):
    _write_row(
        lore_root, event="note-filed",
        wikilink="[[orphan]]", path="/tmp/gone.md",
    )
    before = _read_rows(lore_root)

    result = runner.invoke(app, ["prune", "--dry-run"])
    assert result.exit_code == 0
    assert "would prune 1 orphan row" in result.stdout
    assert "[[orphan]]" in result.stdout

    after = _read_rows(lore_root)
    assert before == after


def test_prune_handles_missing_drain_file(lore_root, runner):
    """Fresh vault with no `_system.jsonl` yet — graceful no-op."""
    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "no _system.jsonl" in result.stdout


def test_prune_drops_multiple_orphans(lore_root, runner):
    _write_row(lore_root, event="note-filed",
               wikilink="[[a]]", path="/tmp/a-gone.md")
    _write_row(lore_root, event="note-appended",
               wikilink="[[b]]", path="/tmp/b-gone.md")
    _write_row(lore_root, event="surface-proposed",
               wikilink="[[c]]", path="/tmp/c-gone.md")
    _write_row(lore_root, event="transcript-synced", transcript_id="t1")

    result = runner.invoke(app, ["prune"])
    assert result.exit_code == 0
    assert "pruned 3 orphan rows" in result.stdout

    rows = _read_rows(lore_root)
    assert len(rows) == 1
    assert rows[0]["event"] == "transcript-synced"
