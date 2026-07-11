"""Tests for the reusable orphan-pruning core behind `lore drain prune`.

`prune_orphans` is the extracted logic `cmd_prune` (the CLI command) and
the retention janitor (#190) both call — the janitor folds orphan pruning
into its opportunistic sweep instead of leaving it as a manual-only
escape hatch.
"""

from __future__ import annotations

import json
from pathlib import Path

from lore_cli.drain_cmd import prune_orphans
from lore_core.spine import read_spine, validate_envelope


def _write_system_jsonl(lore_root: Path, rows: list[dict]) -> Path:
    target = lore_root / ".lore" / "drain" / "_system.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return target


def test_prune_orphans_drops_rows_with_missing_path(tmp_path: Path):
    gone = tmp_path / "gone.md"  # never created
    kept = tmp_path / "kept.md"
    kept.write_text("x")
    target = _write_system_jsonl(
        tmp_path,
        [
            {"event": "note-filed", "data": {"path": str(gone), "wikilink": "[[gone]]"}},
            {"event": "note-filed", "data": {"path": str(kept), "wikilink": "[[kept]]"}},
            {"event": "transcript-synced", "data": {}},  # no path, always kept
        ],
    )
    result = prune_orphans(tmp_path)
    assert result.dropped_count == 1
    lines = [json.loads(x) for x in target.read_text().splitlines() if x.strip()]
    assert len(lines) == 2
    assert {r["event"] for r in lines} == {"note-filed", "transcript-synced"}


def test_prune_orphans_dry_run_does_not_modify_file(tmp_path: Path):
    gone = tmp_path / "gone.md"
    target = _write_system_jsonl(tmp_path, [{"event": "note-filed", "data": {"path": str(gone)}}])
    before = target.read_text()
    result = prune_orphans(tmp_path, dry_run=True)
    assert result.dropped_count == 1
    assert target.read_text() == before


def test_prune_orphans_missing_file_is_noop(tmp_path: Path):
    result = prune_orphans(tmp_path)
    assert result.dropped_count == 0


def test_prune_orphans_no_orphans_is_noop(tmp_path: Path):
    kept = tmp_path / "kept.md"
    kept.write_text("x")
    _write_system_jsonl(tmp_path, [{"event": "note-filed", "data": {"path": str(kept)}}])
    result = prune_orphans(tmp_path)
    assert result.dropped_count == 0


def test_prune_orphans_emits_janitor_spine_event(tmp_path: Path):
    gone = tmp_path / "gone.md"
    _write_system_jsonl(tmp_path, [{"event": "note-filed", "data": {"path": str(gone)}}])
    prune_orphans(tmp_path)
    events = [r for r in read_spine(tmp_path, source="janitor") if r["event"] == "retention-delete"]
    assert events
    assert events[0]["data"]["family"] == "drain-orphans"
    assert events[0]["data"]["dropped"] == 1
    validate_envelope(events[0])


def test_prune_orphans_dry_run_emits_no_spine_event(tmp_path: Path):
    gone = tmp_path / "gone.md"
    _write_system_jsonl(tmp_path, [{"event": "note-filed", "data": {"path": str(gone)}}])
    prune_orphans(tmp_path, dry_run=True)
    assert read_spine(tmp_path, source="janitor") == []


def test_prune_orphans_write_failure_emits_warn_event(tmp_path: Path, monkeypatch):
    gone = tmp_path / "gone.md"
    _write_system_jsonl(tmp_path, [{"event": "note-filed", "data": {"path": str(gone)}}])

    import os

    real_replace = os.replace

    def bad_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", bad_replace)
    result = prune_orphans(tmp_path)
    monkeypatch.setattr(os, "replace", real_replace)

    assert result.failed is True
    failures = [
        r for r in read_spine(tmp_path, source="janitor") if r["event"] == "retention-delete-failed"
    ]
    assert failures
    assert failures[0]["level"] == "warn"
    assert failures[0]["data"]["family"] == "drain-orphans"
