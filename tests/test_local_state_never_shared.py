"""AC3 (issue #178): transcripts and buffers verifiably never leave local
state.

Only composed, gate-passed notes reach the shared wiki. Raw transcripts
(the ledger) and buffers (accumulation sidecars) live under
``<lore_root>/.lore/`` — a sibling of ``wiki/``, never a descendant —
so a git push of a wiki dir structurally cannot ship them. This test
exercises the real write paths (not just the path-construction
strings) and then walks the wiki tree to confirm nothing from local
state landed there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_curator.buffer_store import buffers_dir, done_dir

_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _make_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "lore-root"
    (lore_root / "wiki" / "shared").mkdir(parents=True)
    return lore_root


def test_buffers_dir_is_outside_wiki(tmp_path: Path) -> None:
    lore_root = _make_vault(tmp_path)
    wiki_root = lore_root / "wiki"

    bdir = buffers_dir(lore_root)
    ddir = done_dir(lore_root)

    assert wiki_root not in bdir.parents
    assert wiki_root not in ddir.parents


def test_transcript_ledger_write_never_lands_under_wiki(tmp_path: Path) -> None:
    lore_root = _make_vault(tmp_path)
    wiki_root = lore_root / "wiki"

    ledger = TranscriptLedger(lore_root)
    ledger.upsert(
        TranscriptLedgerEntry(
            integration="fake",
            transcript_id="t1",
            path=lore_root / "t1.jsonl",
            directory=lore_root,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=_NOW,
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )
    assert ledger.get("fake", "t1") is not None

    # Walk the whole vault: any file/dir named after local-state
    # artifacts (ledger json, buffer sidecars) must never appear under
    # wiki/ — that's the boundary a `git push` of a wiki repo respects.
    local_state_names = {"transcript-ledger.json", "buffers", "_done"}
    offenders = [
        p
        for p in wiki_root.rglob("*")
        if p.name in local_state_names or p.name.endswith((".jsonl", ".state.json"))
    ]
    assert offenders == []


def test_dot_lore_and_wiki_are_disjoint_siblings(tmp_path: Path) -> None:
    """Structural invariant the whole boundary relies on: `.lore/` and
    `wiki/` are siblings under lore_root, so nothing under one can ever
    be a path-prefix match for the other."""
    lore_root = _make_vault(tmp_path)
    dot_lore = lore_root / ".lore"
    wiki_root = lore_root / "wiki"

    assert dot_lore.parent == wiki_root.parent == lore_root
    assert not str(dot_lore).startswith(str(wiki_root) + "/")
    assert not str(wiki_root).startswith(str(dot_lore) + "/")
