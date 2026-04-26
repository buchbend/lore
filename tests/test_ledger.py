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
    integration: str = "claude",
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
        integration=integration,
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
        integration="claude",
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
    assert result.integration == "claude"
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


def test_transcript_ledger_advance_sets_curator_a_run(tmp_path: Path) -> None:
    """Regression for buchbend/lore#14 — advance must persist curator_a_run.

    Without it, the mtime-based re-trigger in pending() is permanently dead
    for already-digested entries.
    """
    ledger = TranscriptLedger(tmp_path)
    entry = _make_entry(tmp_path, transcript_id="run-stamp")
    ledger.upsert(entry)
    run_ts = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
    ledger.advance(
        "claude",
        "run-stamp",
        digested_hash="h1",
        digested_index_hint=10,
        noteworthy=True,
        curator_a_run=run_ts,
    )
    result = ledger.get("claude", "run-stamp")
    assert result is not None
    assert result.curator_a_run == run_ts


def test_transcript_ledger_pending_reappears_when_last_mtime_exceeds_curator_a_run(
    tmp_path: Path,
) -> None:
    """Regression for buchbend/lore#14 — entry must re-appear in pending()
    when its transcript grows after a previous curator pass.
    """
    ledger = TranscriptLedger(tmp_path)
    initial_mtime = datetime(2026, 4, 19, 10, 0, 0, tzinfo=UTC)
    entry = _make_entry(tmp_path, transcript_id="growing", last_mtime=initial_mtime)
    ledger.upsert(entry)

    # First pass — advance with a curator_a_run timestamp.
    run1_ts = datetime(2026, 4, 19, 11, 0, 0, tzinfo=UTC)
    ledger.advance(
        "claude",
        "growing",
        digested_hash="h-pass1",
        digested_index_hint=20,
        noteworthy=True,
        curator_a_run=run1_ts,
    )
    assert ledger.pending() == []  # nothing pending immediately after

    # Simulate transcript growth — bump last_mtime past curator_a_run.
    grown = _make_entry(
        tmp_path,
        transcript_id="growing",
        digested_hash="h-pass1",
        digested_index_hint=20,
        last_mtime=datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC),
        curator_a_run=run1_ts,
        noteworthy=True,
    )
    ledger.upsert(grown)

    # Now the entry must re-appear in pending().
    pending = ledger.pending()
    assert len(pending) == 1
    assert pending[0].transcript_id == "growing"


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


def _make_pending_entry(
    lore_root: Path,
    *,
    transcript_id: str,
    directory: Path,
) -> TranscriptLedgerEntry:
    """Build a never-digested (pending) entry rooted in `directory`."""
    return TranscriptLedgerEntry(
        integration="claude",
        transcript_id=transcript_id,
        path=directory / f"{transcript_id}.jsonl",
        directory=directory,
        digested_hash=None,
        digested_index_hint=None,
        synthesised_hash=None,
        last_mtime=datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC),
        curator_a_run=None,
        noteworthy=None,
        session_note=None,
    )


def test_pending_by_wiki_buckets_orphan_and_unattached_correctly(tmp_path: Path) -> None:
    """pending_by_wiki() returns dict keyed by wiki name, with
    __orphan__ (cwd gone) and __unattached__ (no ## Lore) buckets."""
    ledger = TranscriptLedger(tmp_path)

    # wiki-A: attached directory with CLAUDE.md pointing to wiki "alpha"
    dir_a = tmp_path / "proj-alpha"
    dir_a.mkdir()
    _write_claude_md(dir_a / "CLAUDE.md", wiki="alpha", scope="proj:alpha")
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="a1", directory=dir_a))
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="a2", directory=dir_a))

    # wiki-B: attached directory pointing to wiki "beta"
    dir_b = tmp_path / "proj-beta"
    dir_b.mkdir()
    _write_claude_md(dir_b / "CLAUDE.md", wiki="beta", scope="proj:beta")
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="b1", directory=dir_b))

    # orphan: directory no longer exists on disk
    orphan_dir = tmp_path / "gone"
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="o1", directory=orphan_dir))

    # unattached: directory exists but no CLAUDE.md
    dir_u = tmp_path / "proj-unattached"
    dir_u.mkdir()
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="u1", directory=dir_u))

    buckets = ledger.pending_by_wiki()

    assert set(buckets.keys()) == {"alpha", "beta", "__orphan__", "__unattached__"}
    assert {e.transcript_id for e in buckets["alpha"]} == {"a1", "a2"}
    assert {e.transcript_id for e in buckets["beta"]} == {"b1"}
    assert {e.transcript_id for e in buckets["__orphan__"]} == {"o1"}
    assert {e.transcript_id for e in buckets["__unattached__"]} == {"u1"}


def test_pending_filters_by_wiki_when_wiki_arg_given(tmp_path: Path) -> None:
    """pending(wiki='alpha') returns only entries resolving to 'alpha'."""
    ledger = TranscriptLedger(tmp_path)

    dir_a = tmp_path / "proj-alpha"
    dir_a.mkdir()
    _write_claude_md(dir_a / "CLAUDE.md", wiki="alpha")
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="a1", directory=dir_a))

    dir_b = tmp_path / "proj-beta"
    dir_b.mkdir()
    _write_claude_md(dir_b / "CLAUDE.md", wiki="beta")
    ledger.upsert(_make_pending_entry(tmp_path, transcript_id="b1", directory=dir_b))

    only_alpha = ledger.pending(wiki="alpha")
    assert {e.transcript_id for e in only_alpha} == {"a1"}

    only_beta = ledger.pending(wiki="beta")
    assert {e.transcript_id for e in only_beta} == {"b1"}


def test_pending_excludes_orphan_flagged_entries(tmp_path: Path) -> None:
    """Entries with entry.orphan=True never reappear in pending()."""
    ledger = TranscriptLedger(tmp_path)

    dir_a = tmp_path / "proj-a"
    dir_a.mkdir()
    _write_claude_md(dir_a / "CLAUDE.md", wiki="alpha")
    entry = _make_pending_entry(tmp_path, transcript_id="orph-1", directory=dir_a)
    entry.orphan = True
    ledger.upsert(entry)

    # Baseline: pending() respects the orphan flag
    assert ledger.pending() == []
    assert "alpha" not in ledger.pending_by_wiki()


def test_orphan_field_round_trips_through_upsert(tmp_path: Path) -> None:
    """The orphan boolean survives the JSON roundtrip."""
    ledger = TranscriptLedger(tmp_path)
    dir_a = tmp_path / "proj-a"
    dir_a.mkdir()
    entry = _make_pending_entry(tmp_path, transcript_id="t1", directory=dir_a)
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
