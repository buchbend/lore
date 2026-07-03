"""End-to-end withhold side-effects for the publish gate.

On a terminal WITHHELD verdict the gate owns two side-effects, testable
without the real compose loop: a deterministic withheld-marker chapter is
appended to the note, and the composed text is stored in the private
quarantine sidecar. The shared note never carries the unsafe content —
only a safe marker and the turn range.
"""

from __future__ import annotations

import secrets as _secrets
from pathlib import Path

from lore_core import note_document as nd
from lore_core import publish_gate as pg
from lore_core import quarantine


def _fixture_note(tmp_path: Path) -> Path:
    path = tmp_path / "wiki" / "lore" / "sessions" / "2026" / "07" / "03-demo.md"
    nd.create_note(
        path,
        title="Demo session",
        description="A demo session note",
        scope="lore",
    )
    return path


class TestPlantedSecretEndToEnd:
    def test_gate_withholds_planted_secret(self, tmp_path: Path):
        token = _secrets.token_urlsafe(40)
        composed = f"**Notes on the leak** \n\nkey sk-{token} was pasted. @11"
        result = pg.evaluate(composed)
        assert result.passed is False
        assert result.category == pg.CATEGORY_SECRET

    def test_withhold_appends_marker_and_quarantines(self, tmp_path: Path):
        note = _fixture_note(tmp_path)
        token = _secrets.token_urlsafe(40)
        composed = f"**Notes on the leak** \n\nkey sk-{token} was pasted. @11"

        result = pg.evaluate(composed)
        outcome = pg.apply_withhold(
            note,
            result=result,
            composed_text=composed,
            slice_from_turn=10,
            slice_to_turn=20,
            lore_root=tmp_path,
        )

        # A withheld marker chapter now sits in the note.
        view = nd.read_note(note)
        markers = [c for c in view.chapters if c.get("kind") == "marker"]
        assert len(markers) == 1
        assert markers[0]["marker"] == nd.MARKER_WITHHELD
        assert outcome.chapter_n == markers[0]["n"]

        # The unsafe composed text NEVER reaches the shared note on disk.
        on_disk = note.read_text()
        assert token not in on_disk

        # A quarantine entry holds the full composed text privately.
        entries = quarantine.list_entries(lore_root=tmp_path)
        assert len(entries) == 1
        assert entries[0].id == outcome.entry_id
        assert entries[0].composed_text == composed
        assert token in entries[0].composed_text  # held for review
        assert entries[0].category == pg.CATEGORY_SECRET
        assert entries[0].from_turn == 10
        assert entries[0].to_turn == 20

    def test_marker_reason_does_not_leak_the_secret(self, tmp_path: Path):
        note = _fixture_note(tmp_path)
        token = _secrets.token_urlsafe(40)
        composed = f"**Leak** \n\nkey sk-{token}. @11"
        result = pg.evaluate(composed)
        pg.apply_withhold(
            note,
            result=result,
            composed_text=composed,
            slice_from_turn=10,
            slice_to_turn=20,
            lore_root=tmp_path,
        )
        view = nd.read_note(note)
        marker = next(c for c in view.chapters if c.get("kind") == "marker")
        assert token not in str(marker.get("reason", ""))
        assert token not in view.body


class TestPhrasingGiveUpPath:
    def test_phrasing_hit_produces_withheld_with_feedback(self, tmp_path: Path):
        # First compose attempt: a lint hit yields WITHHELD-with-feedback,
        # which the retry loop injects into the next compose prompt.
        result = pg.evaluate("**Fix the flush race**\n\ndetail. @1")
        assert result.passed is False
        assert result.category == pg.CATEGORY_PHRASING
        assert result.feedback

    def test_give_up_withhold_side_effects(self, tmp_path: Path):
        # Second attempt also hits: the give-up path runs the terminal
        # withhold side-effects (marker + quarantine).
        note = _fixture_note(tmp_path)
        composed = "**Fix the flush race**\n\nstill imperative. @1"
        result = pg.evaluate(composed)
        outcome = pg.apply_withhold(
            note,
            result=result,
            composed_text=composed,
            slice_from_turn=1,
            slice_to_turn=5,
            lore_root=tmp_path,
        )
        view = nd.read_note(note)
        marker = next(c for c in view.chapters if c.get("kind") == "marker")
        assert marker["marker"] == nd.MARKER_WITHHELD
        (entry,) = quarantine.list_entries(lore_root=tmp_path)
        assert entry.id == outcome.entry_id
        assert entry.category == pg.CATEGORY_PHRASING


class TestGateIsTheOnlyDoor:
    def test_passing_chapter_is_not_quarantined(self, tmp_path: Path):
        # A clean chapter produces no quarantine side-effect; the caller
        # appends it normally (the compose/integration layer, not the gate).
        result = pg.evaluate("**Traced the flush race**\n\nprose. @1")
        assert result.passed is True
        assert quarantine.list_entries(lore_root=tmp_path) == []
