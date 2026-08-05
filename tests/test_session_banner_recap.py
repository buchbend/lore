"""SessionStart recap of the last active day, rendered from the ledger.

Replaces the last-session note hints: deterministic, zero LLM calls, and
alive after the session-note files are gone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_core.session_start import collect_session_facts, last_active_day_recap


def _entry(lore_root: Path, tid: str, when: datetime, **linkage) -> TranscriptLedgerEntry:
    return TranscriptLedgerEntry(
        integration="claude-code",
        transcript_id=tid,
        path=lore_root / f"{tid}.jsonl",
        directory=lore_root / "proj",
        digested_hash=None,
        digested_index_hint=None,
        synthesised_hash=None,
        last_mtime=when,
        curator_a_run=None,
        noteworthy=None,
        session_note=None,
        linkage=linkage,
    )


def test_recap_summarises_the_last_active_day(tmp_path: Path) -> None:
    """AC5: repo, branch, PRs, issues and the session count for the most
    recent day that has entries — at most three lines."""
    ledger = TranscriptLedger(tmp_path)
    ledger.bulk_upsert(
        [
            _entry(
                tmp_path,
                "old",
                datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
                repo="buchbend/lore",
                branch="main",
                prs=[],
                issues=[1],
            ),
            _entry(
                tmp_path,
                "a",
                datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
                repo="buchbend/lore",
                branch="feat/358-ledger",
                prs=[364],
                issues=[358],
            ),
            _entry(
                tmp_path,
                "b",
                datetime(2026, 8, 4, 17, 0, tzinfo=UTC),
                repo="buchbend/lore",
                branch="feat/357-flag",
                prs=[],
                issues=[357],
            ),
        ]
    )

    lines = last_active_day_recap(tmp_path)

    assert len(lines) <= 3
    assert lines[0] == "Last active 2026-08-04 — 2 sessions in buchbend/lore"
    assert "feat/357-flag" in lines[1] and "feat/358-ledger" in lines[1]
    assert "#357" in lines[2] and "#358" in lines[2] and "#364" in lines[2]


def test_recap_is_empty_when_the_ledger_is(tmp_path: Path) -> None:
    assert last_active_day_recap(tmp_path) == ()


def test_recap_skips_orphaned_entries(tmp_path: Path) -> None:
    """An orphan is a retired session; it must not define the last day."""
    ledger = TranscriptLedger(tmp_path)
    live = _entry(
        tmp_path, "live", datetime(2026, 8, 1, 9, 0, tzinfo=UTC), repo="o/r", branch="main"
    )
    dead = _entry(
        tmp_path, "dead", datetime(2026, 8, 4, 9, 0, tzinfo=UTC), repo="o/r", branch="gone"
    )
    dead.orphan = True
    ledger.bulk_upsert([live, dead])

    assert last_active_day_recap(tmp_path)[0].startswith("Last active 2026-08-01 — 1 session ")


def test_collect_session_facts_carries_the_recap(tmp_path: Path) -> None:
    lore_root = tmp_path
    wiki = lore_root / "wiki" / "demo"
    wiki.mkdir(parents=True)
    TranscriptLedger(lore_root).upsert(
        _entry(
            lore_root,
            "a",
            datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
            repo="buchbend/lore",
            branch="main",
        )
    )

    facts = collect_session_facts(wiki, None)

    assert facts.recap[0] == "Last active 2026-08-04 — 1 session in buchbend/lore"


# ---------------------------------------------------------------------------
# Render side — the recap replaces the last-session note hints
# ---------------------------------------------------------------------------


def test_banner_renders_the_recap_instead_of_session_note_hints() -> None:
    import pytest
    from lore_core import session_start
    from lore_core.session_start import SessionFacts, render_session_banner

    mp = pytest.MonkeyPatch()
    mp.setattr(session_start, "lore_version", lambda: "9.9.9")
    mp.setattr(session_start, "load_directive_lines", lambda: ["## Directive"])
    try:
        facts = SessionFacts(
            wiki_name="ccat",
            repo="o/r",
            scope="ccat:data-center",
            project_entry=None,
            session_hints=(("12-1530-fix", "fixed the thing"),),
            recap=(
                "Last active 2026-08-04 — 2 sessions in buchbend/lore",
                "Branches: feat/358-ledger",
                "Refs: #358, #364",
            ),
        )
        out = render_session_banner(facts)
    finally:
        mp.undo()

    assert "Last: [[12-1530-fix]]" not in out
    assert "fixed the thing" not in out
    assert "Last active 2026-08-04 — 2 sessions in buchbend/lore" in out
    assert "Branches: feat/358-ledger" in out
    assert "Refs: #358, #364" in out
    assert out.splitlines()[0] == "lore 9.9.9: active · ccat:data-center · last active 2026-08-04"
