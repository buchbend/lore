"""Tests for `lore_core.janitor.prune_orphans` — legacy drain-orphan cleanup.

There is no more `lore drain prune` CLI (#195 removed it as vestigial —
`.lore/drain/_system.jsonl` hasn't had a writer since #188 moved drain
emission onto the spine). `prune_orphans` now lives here and is called
opportunistically by the retention janitor
(`lore_cli._janitor_entry.run_opportunistic_janitor`) as pure upgrade
cleanup for rows a pre-migration install left behind.
"""

from __future__ import annotations

import json
from pathlib import Path

from lore_core.janitor import prune_orphans
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


def test_prune_orphans_keeps_rows_without_path(tmp_path: Path):
    """A note-style row with no `data.path` is suspicious but kept —
    prune evicts on path-existence, not on schema completeness."""
    _write_system_jsonl(tmp_path, [{"event": "note-filed", "data": {"wikilink": "[[no-path]]"}}])
    result = prune_orphans(tmp_path)
    assert result.dropped_count == 0


def test_prune_orphans_drops_multiple_orphan_types_in_one_pass(tmp_path: Path):
    a, b, c = (tmp_path / n for n in ("a-gone.md", "b-gone.md", "c-gone.md"))
    target = _write_system_jsonl(
        tmp_path,
        [
            {"event": "note-filed", "data": {"path": str(a), "wikilink": "[[a]]"}},
            {"event": "note-appended", "data": {"path": str(b), "wikilink": "[[b]]"}},
            {"event": "surface-proposed", "data": {"path": str(c), "wikilink": "[[c]]"}},
            {"event": "transcript-synced", "data": {}},
        ],
    )
    result = prune_orphans(tmp_path)
    assert result.dropped_count == 3
    lines = [json.loads(x) for x in target.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["event"] == "transcript-synced"


def test_prune_orphans_preserves_malformed_lines(tmp_path: Path):
    """Malformed JSON is kept verbatim — prune is not a validator."""
    target = _write_system_jsonl(
        tmp_path, [{"event": "note-filed", "data": {"path": str(tmp_path / "gone.md")}}]
    )
    with target.open("a") as fp:
        fp.write("NOT JSON\n")
        fp.write(json.dumps({"event": "transcript-synced", "data": {}}) + "\n")

    result = prune_orphans(tmp_path)
    assert result.dropped_count == 1

    raw = target.read_text().splitlines()
    assert "NOT JSON" in raw
    assert any("transcript-synced" in line for line in raw)


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
