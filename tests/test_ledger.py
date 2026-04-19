"""Tests for lore_core.ledger — transcript + wiki sidecar ledger."""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.ledger import (
    TranscriptLedger,
    TranscriptLedgerEntry,
    WikiLedger,
    WikiLedgerEntry,
)


def _make_entry(
    lore_root: Path,
    *,
    host: str = "claude",
    transcript_id: str = "abc123",
    digested_hash: str | None = None,
    digested_index_hint: int | None = None,
    synthesised_hash: str | None = None,
    last_mtime: datetime | None = None,
    curator_a_run: datetime | None = None,
    noteworthy: bool | None = None,
    session_note: str | None = None,
) -> TranscriptLedgerEntry:
    if last_mtime is None:
        last_mtime = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    return TranscriptLedgerEntry(
        host=host,
        transcript_id=transcript_id,
        path=lore_root / "transcripts" / f"{transcript_id}.json",
        directory=lore_root / "transcripts",
        digested_hash=digested_hash,
        digested_index_hint=digested_index_hint,
        synthesised_hash=synthesised_hash,
        last_mtime=last_mtime,
        curator_a_run=curator_a_run,
        noteworthy=noteworthy,
        session_note=session_note,
    )


# ---------------------------------------------------------------------------
# 1. Fresh ledger returns empty state
# ---------------------------------------------------------------------------


def test_transcript_ledger_empty_on_fresh_lore_root(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    assert ledger.get("claude", "xyz") is None
    assert ledger.pending() == []


# ---------------------------------------------------------------------------
# 2. Upsert + get roundtrip
# ---------------------------------------------------------------------------


def test_transcript_ledger_upsert_then_get_roundtrip(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(
        tmp_path,
        host="claude",
        transcript_id="t1",
        digested_hash="abc",
        digested_index_hint=5,
        synthesised_hash="syn1",
        last_mtime=datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC),
        curator_a_run=datetime(2026, 4, 18, 11, 0, 0, tzinfo=UTC),
        noteworthy=True,
        session_note="[[2026-04-18-slug]]",
    )
    ledger.upsert(entry)
    result = ledger.get("claude", "t1")
    assert result is not None
    assert result.host == "claude"
    assert result.transcript_id == "t1"
    assert result.path == entry.path
    assert result.directory == entry.directory
    assert result.digested_hash == "abc"
    assert result.digested_index_hint == 5
    assert result.synthesised_hash == "syn1"
    assert result.last_mtime == datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
    assert result.curator_a_run == datetime(2026, 4, 18, 11, 0, 0, tzinfo=UTC)
    assert result.noteworthy is True
    assert result.session_note == "[[2026-04-18-slug]]"


# ---------------------------------------------------------------------------
# 3. pending() includes entries where last_mtime > curator_a_run
# ---------------------------------------------------------------------------


def test_transcript_ledger_pending_mtime_gt_digested(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(
        tmp_path,
        transcript_id="t2",
        digested_hash="oldhash",
        last_mtime=datetime(2026, 4, 18, 14, 0, 0, tzinfo=UTC),
        curator_a_run=datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC),
    )
    ledger.upsert(entry)
    pending = ledger.pending()
    assert len(pending) == 1
    assert pending[0].transcript_id == "t2"


# ---------------------------------------------------------------------------
# 4. pending() excludes entries where last_mtime <= curator_a_run
# ---------------------------------------------------------------------------


def test_transcript_ledger_pending_excludes_current(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(
        tmp_path,
        transcript_id="t3",
        digested_hash="goodhash",
        last_mtime=datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC),
        curator_a_run=datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC),
    )
    ledger.upsert(entry)
    assert ledger.pending() == []


# ---------------------------------------------------------------------------
# 5. advance() updates hash + hint + noteworthy + session_note
# ---------------------------------------------------------------------------


def test_transcript_ledger_advance_updates_hash_and_hint(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(tmp_path, transcript_id="t4")
    ledger.upsert(entry)
    ledger.advance(
        "claude",
        "t4",
        digested_hash="newhash",
        digested_index_hint=42,
        noteworthy=False,
        session_note="[[2026-04-18-note]]",
    )
    result = ledger.get("claude", "t4")
    assert result is not None
    assert result.digested_hash == "newhash"
    assert result.digested_index_hint == 42
    assert result.noteworthy is False
    assert result.session_note == "[[2026-04-18-note]]"


# ---------------------------------------------------------------------------
# 6. advance() raises KeyError for missing entry
# ---------------------------------------------------------------------------


def test_transcript_ledger_advance_raises_on_missing(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    with pytest.raises(KeyError):
        ledger.advance(
            "claude",
            "nonexistent",
            digested_hash="h",
            digested_index_hint=0,
            noteworthy=False,
        )


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
# 8. WikiLedger returns defaults when file is absent
# ---------------------------------------------------------------------------


def test_wiki_ledger_defaults_on_missing_file(tmp_path: Path) -> None:
    wl = WikiLedger(tmp_path, "myproject")
    entry = wl.read()
    assert entry.wiki == "myproject"
    assert entry.last_curator_a is None
    assert entry.last_curator_b is None
    assert entry.last_briefing is None
    assert entry.pending_transcripts == 0
    assert entry.pending_tokens_est == 0


# ---------------------------------------------------------------------------
# 9. WikiLedger write → read roundtrip
# ---------------------------------------------------------------------------


def test_wiki_ledger_write_read_roundtrip(tmp_path: Path) -> None:
    wl = WikiLedger(tmp_path, "proj")
    entry = WikiLedgerEntry(
        wiki="proj",
        last_curator_a=datetime(2026, 4, 18, 9, 0, 0, tzinfo=UTC),
        last_curator_b=datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC),
        last_briefing=datetime(2026, 4, 18, 11, 0, 0, tzinfo=UTC),
        pending_transcripts=3,
        pending_tokens_est=15000,
    )
    wl.write(entry)
    result = wl.read()
    assert result.wiki == "proj"
    assert result.last_curator_a == datetime(2026, 4, 18, 9, 0, 0, tzinfo=UTC)
    assert result.last_curator_b == datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    assert result.last_briefing == datetime(2026, 4, 18, 11, 0, 0, tzinfo=UTC)
    assert result.pending_transcripts == 3
    assert result.pending_tokens_est == 15000
