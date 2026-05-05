"""Tests for Phase 6 — Curator B section-aware session-note input.

Pre-Phase-6 Curator B sliced ``body[:800]`` to feed clustering and the
abstract step. With the revised body shape (``# title`` → ``## Summary``
→ ``## Decisions made`` → ``## What we worked on`` → ``## Activity``
→ ``## Loose ends``), the first 800 chars depend on how long the
Summary paragraph runs — sometimes you'd land mid-Activity, sometimes
inside Loose ends grammar. Section-aware extraction always lands on
the rationale layer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_curator.daily_curator import (
    _extract_section_text,
    _load_recent_session_notes,
    _section_aware_summary,
)


# ---------------------------------------------------------------------------
# _extract_section_text
# ---------------------------------------------------------------------------


def test_extract_section_text_returns_paragraph_under_h2():
    body = (
        "# Title\n\n"
        "## Summary\n\n"
        "Did the thing for the reason. "
        "Trade-off was X over Y because Z.\n\n"
        "## Decisions made\n\n"
        "- **A** chose path A\n"
    )
    text = _extract_section_text(body, "## Summary", max_chars=1000)
    assert "Did the thing" in text
    assert "Trade-off" in text
    assert "## Decisions" not in text
    assert "**A**" not in text


def test_extract_section_text_stops_at_h3_under_activity():
    """Nested H3 (e.g. ``### Commits`` under ``## Activity``) bounds
    the slice — the prior section's text doesn't bleed into the next
    one."""
    body = (
        "## Summary\n\n"
        "Summary paragraph.\n\n"
        "## Activity\n"
        "### Commits\n"
        "- abc1234 add ledger\n"
    )
    summary = _extract_section_text(body, "## Summary", max_chars=1000)
    assert summary == "Summary paragraph."
    assert "Commits" not in summary

    activity = _extract_section_text(body, "## Activity", max_chars=1000)
    assert activity == ""  # only H3 content under Activity, no inline body


def test_extract_section_text_truncates_at_max_chars():
    long_para = "X" * 5000
    body = f"## Summary\n\n{long_para}\n"
    text = _extract_section_text(body, "## Summary", max_chars=200)
    assert len(text) <= 201  # 200 + the …
    assert text.endswith("…")


def test_extract_section_text_returns_empty_when_heading_absent():
    body = "## Other section\n\nstuff\n"
    assert _extract_section_text(body, "## Summary", max_chars=1000) == ""


# ---------------------------------------------------------------------------
# _section_aware_summary
# ---------------------------------------------------------------------------


def test_section_aware_summary_combines_summary_and_decisions():
    body = (
        "# Add Ledger\n\n"
        "## Summary\n\nAdded an append-only ledger module for tracking curator runs.\n\n"
        "## Decisions made\n\n- **JSONL over SQLite** — grep-friendly\n\n"
        "## What we worked on\n\n- wrote ledger.py\n\n"
        "## Loose ends\n\n- batching strategy was deferred\n"
    )
    extract = _section_aware_summary(body)
    assert "append-only ledger" in extract
    assert "JSONL over SQLite" in extract
    # Activity / Loose ends content stays out of B's input — it's not
    # rationale, and B's clustering should not be steered by it.
    assert "wrote ledger.py" not in extract
    assert "batching strategy" not in extract


def test_section_aware_summary_falls_back_for_legacy_notes():
    """Legacy notes lack ``## Summary`` and ``## Decisions made``;
    cluster behaviour should stay close to pre-Phase-6 (flat prefix)
    until the vault rolls forward."""
    body = (
        "### Summary\n- bullet\n\n"
        "### Decisions\n- did the thing\n\n"
        "Entities: [[a]]\n"
    )
    extract = _section_aware_summary(body)
    assert "### Summary" in extract  # whole prefix lands in the fallback
    assert extract == body[:800]


def test_section_aware_summary_handles_summary_only():
    body = "## Summary\n\nJust a summary, no decisions made.\n"
    extract = _section_aware_summary(body)
    assert "Just a summary" in extract


def test_section_aware_summary_handles_decisions_only():
    body = "## Decisions made\n\n- **A** chose path A\n"
    extract = _section_aware_summary(body)
    assert "**A**" in extract


def test_section_aware_summary_uses_discussion_when_decisions_absent():
    """step-8 of yes-do-that-keen-yeti: discussion-shape notes have no
    ``## Decisions made`` (it's structurally gated). B's prefix window
    should fall through to ``## Discussion`` so cluster topic-discrimination
    keeps a rationale-rich anchor for non-work sessions."""
    body = (
        "# Discussed: docs Diátaxis spine\n\n"
        "## Summary\n\nExplored a four-quadrant restructure for the data-transfer docs.\n\n"
        "## Discussion\n\n"
        "- **Diátaxis spine** — split tutorials/how-to/reference/explanation\n"
        "- **ADR extraction** — promote philosophy.md essays into 7 ADRs\n\n"
        "## Loose ends\n\n- ADR backlog was not validated.\n"
    )
    extract = _section_aware_summary(body)
    assert "four-quadrant restructure" in extract
    assert "Diátaxis spine" in extract
    # Loose ends remains out of B's prefix window.
    assert "not validated" not in extract


def test_section_aware_summary_prefers_decisions_over_discussion_when_both_present():
    """Mixed-shape continuation merge (step-7 limitation): a note with
    BOTH ``## Decisions made`` and ``## Discussion`` is rare but valid.
    Decisions wins for B's prefix — it's the higher-signal section."""
    body = (
        "## Summary\n\nMixed-shape part-1 + part-2 merged note.\n\n"
        "## Discussion\n\n- **option A** — considered\n\n"
        "## Decisions made\n\n- **chose option B** because rationale\n"
    )
    extract = _section_aware_summary(body)
    assert "chose option B" in extract
    # Discussion content drops out — Decisions wins the slot.
    assert "option A" not in extract


# ---------------------------------------------------------------------------
# Integration: _load_recent_session_notes feeds the section-aware extract
# ---------------------------------------------------------------------------


def test_load_recent_session_notes_uses_section_aware_extract(tmp_path):
    sessions_dir = tmp_path / "sessions" / "2026" / "04"
    sessions_dir.mkdir(parents=True)
    note_path = sessions_dir / "27-real.md"
    note_path.write_text(
        "---\n"
        "type: session\n"
        "created: '2026-04-27'\n"
        "title: 'Real work'\n"
        "description: 'Did real work.'\n"
        "---\n\n"
        "# Real work\n\n"
        "## Summary\n\nMain narrative goes here.\n\n"
        "## Decisions made\n\n- **A** chose path A because reason\n\n"
        "## What we worked on\n\n- did stuff\n"
    )
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    notes = _load_recent_session_notes(tmp_path / "sessions", cutoff=cutoff)
    assert len(notes) == 1
    summary = notes[0]["summary"]
    assert "Main narrative" in summary
    assert "**A**" in summary
    # ``did stuff`` is in What-we-worked-on — out of scope for B's input.
    assert "did stuff" not in summary
