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

import yaml
from lore_core import note_document as nd
from lore_core import publish_gate as pg
from lore_core import quarantine


def _fixture_note(tmp_path: Path) -> Path:
    """Seed a minimal note file directly.

    append_marker_chapter only reads/appends an existing file — the
    chapter lifecycle that once created one (create_note) is gone.
    """
    path = tmp_path / "wiki" / "lore" / "sessions" / "2026" / "07" / "03-demo.md"
    fm = {
        "schema_version": 2,
        "type": "session",
        "note_status": "open",
        "created": "2026-07-03",
        "last_reviewed": "2026-07-03",
        "title": "Demo session",
        "description": "A demo session note",
        "scope": "lore",
        "chapters": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{nd.DISCLAIMER}\n")
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


class TestStyleIsNotSafety:
    def test_directive_styled_prose_is_never_withheld(self, tmp_path: Path):
        # Imperative leads / TODO / must-should language are the compose
        # prompt's problem, not the gate's: no withhold, no quarantine.
        result = pg.evaluate("**Fix the flush race**\n\nTODO: it should be retried. @1")
        assert result.passed is True
        assert quarantine.list_entries(lore_root=tmp_path) == []

    def test_give_up_withhold_side_effects_for_pii(self, tmp_path: Path):
        # A non-secret safety category runs the same terminal withhold
        # side-effects (marker + quarantine).
        note = _fixture_note(tmp_path)
        composed = "**Coordinated the rollout**\n\nmail bob@example.com about it. @1"
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
        assert entry.category == pg.CATEGORY_EMAIL


class TestGateIsTheOnlyDoor:
    def test_passing_chapter_is_not_quarantined(self, tmp_path: Path):
        # A clean chapter produces no quarantine side-effect; the caller
        # appends it normally (the compose/integration layer, not the gate).
        result = pg.evaluate("**Traced the flush race**\n\nprose. @1")
        assert result.passed is True
        assert quarantine.list_entries(lore_root=tmp_path) == []
