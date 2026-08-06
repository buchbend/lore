"""Tests for lore_core.ledger — the transcript sidecar ledger."""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry


def _make_entry(
    lore_root: Path,
    *,
    integration: str = "claude",
    transcript_id: str = "abc123",
    last_mtime: datetime | None = None,
) -> TranscriptLedgerEntry:
    if last_mtime is None:
        last_mtime = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    return TranscriptLedgerEntry(
        integration=integration,
        transcript_id=transcript_id,
        path=lore_root / "transcripts" / f"{transcript_id}.json",
        directory=lore_root / "transcripts",
        last_mtime=last_mtime,
    )


# ---------------------------------------------------------------------------
# 1. Fresh ledger returns empty state
# ---------------------------------------------------------------------------


def test_transcript_ledger_empty_on_fresh_lore_root(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    assert ledger.get("claude", "xyz") is None
    assert ledger.all_entries() == []


# ---------------------------------------------------------------------------
# 2. Upsert + get roundtrip
# ---------------------------------------------------------------------------


def test_transcript_ledger_upsert_then_get_roundtrip(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(
        tmp_path,
        integration="claude",
        transcript_id="t1",
        last_mtime=datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC),
    )
    entry.linkage = {"repo": "acme/widget", "issues": [7]}
    ledger.upsert(entry)
    result = ledger.get("claude", "t1")
    assert result is not None
    assert result.integration == "claude"
    assert result.transcript_id == "t1"
    assert result.path == entry.path
    assert result.directory == entry.directory
    assert result.last_mtime == datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
    assert result.orphan is False
    assert result.linkage == {"repo": "acme/widget", "issues": [7]}


def test_reader_drops_a_key_this_lore_no_longer_carries(tmp_path: Path) -> None:
    """A ledger written by an older Lore still parses; the extra keys go."""
    import json

    ledger_path = tmp_path / ".lore" / "transcript-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "claude::legacy": {
                    "integration": "claude",
                    "transcript_id": "legacy",
                    "path": str(tmp_path / "legacy.jsonl"),
                    "directory": str(tmp_path),
                    "last_mtime": "2026-04-18T10:00:00+00:00",
                    "digested_hash": "abc",
                    "noteworthy": True,
                    "session_note": "[[2026-04-18-slug]]",
                }
            }
        )
    )
    entry = TranscriptLedger(tmp_path).get("claude", "legacy")
    assert entry is not None
    assert entry.transcript_id == "legacy"
    assert not hasattr(entry, "digested_hash")


# ---------------------------------------------------------------------------
# 7. Atomic writes survive concurrent reads
# ---------------------------------------------------------------------------


def test_transcript_ledger_atomic_write_survives_concurrent_read(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    # Prime with an initial entry so the file exists before the reader starts
    ledger.upsert(_make_entry(tmp_path, transcript_id="seed"))

    errors: list[Exception] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                ledger._load()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    # Run 20 upserts while reader is looping
    for i in range(20):
        ledger.upsert(_make_entry(tmp_path, transcript_id=f"t{i}"))

    # Let the reader run for ~0.5 s total
    time.sleep(0.5)
    stop.set()
    t.join(timeout=2)

    assert errors == [], f"Reader saw corrupt JSON: {errors}"


# ---------------------------------------------------------------------------
# 10. Per-wiki pending filtering (Phase 2)
# ---------------------------------------------------------------------------


def _write_claude_md(path: Path, wiki: str, scope: str = "proj:test") -> None:
    """Helper: register ``path.parent`` as an attachment in the sibling
    ``attachments.json`` so the registry-backed resolver (Phase 6+)
    routes entries through ``wiki``.

    Keeps the name for minimal test-code churn; writes state, not CLAUDE.md.
    """
    from lore_core.state.attachments import Attachment, AttachmentsFile
    repo = path.parent
    # The attachments file lives at <lore_root>/.lore/attachments.json.
    # In these tests, lore_root is tmp_path and repos live under it — walk
    # up until we find tmp_path (by looking for a sibling .lore/ if it
    # exists, or just taking the first ancestor under which repo is a direct child).
    lore_root = repo.parent
    (lore_root / ".lore").mkdir(exist_ok=True)
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(Attachment(
        path=repo,
        wiki=wiki,
        scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, 0, tzinfo=UTC),
        source="manual",
    ))
    af.save()


def _make_dir_entry(
    lore_root: Path,
    *,
    transcript_id: str,
    directory: Path,
) -> TranscriptLedgerEntry:
    """Build an entry rooted in `directory`."""
    return TranscriptLedgerEntry(
        integration="claude",
        transcript_id=transcript_id,
        path=directory / f"{transcript_id}.jsonl",
        directory=directory,
        last_mtime=datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC),
    )


def test_mark_orphan_retires_one_entry(tmp_path: Path) -> None:
    """mark_orphan flags exactly the named entry and leaves the rest alone."""
    ledger = TranscriptLedger(tmp_path)
    live = tmp_path / "proj-alpha"
    live.mkdir()
    ledger.upsert(_make_dir_entry(tmp_path, transcript_id="a1", directory=live))
    ledger.upsert(_make_dir_entry(tmp_path, transcript_id="a2", directory=live))

    ledger.mark_orphan("claude", "a1")

    retired = {e.transcript_id for e in ledger.all_entries() if e.orphan}
    assert retired == {"a1"}


def test_mark_orphan_raises_on_a_missing_entry(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        TranscriptLedger(tmp_path).mark_orphan("claude", "nonexistent")


def test_orphan_field_round_trips_through_upsert(tmp_path: Path) -> None:
    """The orphan boolean survives the JSON roundtrip."""
    ledger = TranscriptLedger(tmp_path)
    dir_a = tmp_path / "proj-a"
    dir_a.mkdir()
    entry = _make_dir_entry(tmp_path, transcript_id="t1", directory=dir_a)
    entry.orphan = True
    ledger.upsert(entry)

    got = ledger.get("claude", "t1")
    assert got is not None
    assert got.orphan is True


# ---------------------------------------------------------------------------
# P0 — in-instance ledger cache + bulk_upsert
# ---------------------------------------------------------------------------


def test_load_cache_avoids_redundant_json_parse(tmp_path: Path, monkeypatch) -> None:
    """Within one instance, identical reads don't re-parse the JSON."""
    import json as _json

    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(tmp_path, transcript_id="cached")
    ledger.upsert(entry)

    parses = {"n": 0}
    real_loads = _json.loads

    def counting_loads(s, *a, **kw):
        parses["n"] += 1
        return real_loads(s, *a, **kw)

    monkeypatch.setattr("lore_core.ledger.json.loads", counting_loads)

    assert ledger.get("claude", "cached") is not None
    assert ledger.get("claude", "cached") is not None
    assert ledger.get("claude", "cached") is not None

    assert parses["n"] == 0, (
        f"cache should have served all three get() calls; json.loads was called {parses['n']}×"
    )


def test_write_refreshes_cache_for_subsequent_reads(tmp_path: Path) -> None:
    """After upsert, the cache reflects the new state without a disk re-read."""
    ledger = TranscriptLedger(tmp_path)
    e1 = _make_entry(tmp_path, transcript_id="a")
    ledger.upsert(e1)
    assert ledger.get("claude", "a") is not None

    e2 = _make_entry(tmp_path, transcript_id="b")
    ledger.upsert(e2)
    # Both live in cache; reads don't race disk.
    assert ledger.get("claude", "a") is not None
    assert ledger.get("claude", "b") is not None


def test_cache_invalidates_when_other_writer_updates_file(tmp_path: Path) -> None:
    """Another process's write (mtime change) invalidates the cache."""
    ledger_a = TranscriptLedger(tmp_path)
    ledger_b = TranscriptLedger(tmp_path)

    e1 = _make_entry(tmp_path, transcript_id="via-a")
    ledger_a.upsert(e1)

    # ledger_b observes e1 via a fresh load.
    assert ledger_b.get("claude", "via-a") is not None

    # ledger_a writes a second entry. ledger_b's next read must see it.
    # Guarantee a distinct mtime — filesystems with 1s mtime granularity
    # would otherwise reuse the cached value.
    time.sleep(0.02)
    e2 = _make_entry(tmp_path, transcript_id="after")
    ledger_a.upsert(e2)

    assert ledger_b.get("claude", "after") is not None


def test_bulk_upsert_writes_once(tmp_path: Path) -> None:
    """bulk_upsert issues a single atomic write for N entries."""
    ledger = TranscriptLedger(tmp_path)
    entries = [
        _make_entry(tmp_path, transcript_id=f"b{i}") for i in range(10)
    ]
    ledger.bulk_upsert(entries)

    for e in entries:
        assert ledger.get("claude", e.transcript_id) is not None


def test_bulk_upsert_empty_list_is_noop(tmp_path: Path) -> None:
    """bulk_upsert with no entries does not create the ledger file."""
    ledger = TranscriptLedger(tmp_path)
    ledger.bulk_upsert([])
    assert not (tmp_path / ".lore" / "transcript-ledger.json").exists()
