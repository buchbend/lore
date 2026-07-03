"""Tests for ``lore_core.quarantine`` — the private quarantine sidecar.

Withheld chapter text is held here (never in the shared wiki) for the
reviewer to inspect and dispose of. Storage is one JSON file per entry
under ``<lore_root>/.lore/quarantine/`` so parallel sessions never race
on a shared index.
"""

from __future__ import annotations

from pathlib import Path

from lore_core import quarantine


def _add(lore_root: Path, **overrides) -> quarantine.QuarantineEntry:
    kwargs = {
        "lore_root": lore_root,
        "category": "secret",
        "note_path": "sessions/2026/07/03-demo.md",
        "from_turn": 10,
        "to_turn": 20,
        "composed_text": "a chapter mentioning sk-secret-value",
    }
    kwargs.update(overrides)
    return quarantine.add_entry(**kwargs)


class TestLocation:
    def test_quarantine_dir_is_private_lore_dir(self, tmp_path: Path):
        d = quarantine.quarantine_dir_for(tmp_path)
        # Must live under the private .lore/ area, never under wiki/.
        assert d == tmp_path / ".lore" / "quarantine"
        assert "wiki" not in d.parts


class TestAddEntry:
    def test_add_returns_entry_with_id_and_fields(self, tmp_path: Path):
        entry = _add(tmp_path, category="email", composed_text="secret body")
        assert entry.id
        assert entry.category == "email"
        assert entry.note_path == "sessions/2026/07/03-demo.md"
        assert entry.from_turn == 10
        assert entry.to_turn == 20
        assert entry.composed_text == "secret body"
        assert entry.created  # ISO timestamp

    def test_add_persists_file_on_disk(self, tmp_path: Path):
        entry = _add(tmp_path)
        f = quarantine.quarantine_dir_for(tmp_path) / f"{entry.id}.json"
        assert f.exists()

    def test_ids_are_unique(self, tmp_path: Path):
        a = _add(tmp_path)
        b = _add(tmp_path)
        assert a.id != b.id


class TestListEntries:
    def test_empty_when_no_quarantine(self, tmp_path: Path):
        assert quarantine.list_entries(lore_root=tmp_path) == []

    def test_lists_all_added(self, tmp_path: Path):
        _add(tmp_path, composed_text="one")
        _add(tmp_path, composed_text="two")
        entries = quarantine.list_entries(lore_root=tmp_path)
        assert len(entries) == 2
        assert {e.composed_text for e in entries} == {"one", "two"}

    def test_full_composed_text_round_trips(self, tmp_path: Path):
        planted = "chapter body with sk-ant-api03-PLANTEDSECRETVALUE inside"
        _add(tmp_path, composed_text=planted)
        (entry,) = quarantine.list_entries(lore_root=tmp_path)
        assert entry.composed_text == planted


class TestGetEntry:
    def test_get_returns_matching(self, tmp_path: Path):
        e = _add(tmp_path)
        got = quarantine.get_entry(e.id, lore_root=tmp_path)
        assert got is not None
        assert got.id == e.id

    def test_get_missing_returns_none(self, tmp_path: Path):
        assert quarantine.get_entry("does-not-exist", lore_root=tmp_path) is None


class TestClearEntry:
    def test_clear_removes_one(self, tmp_path: Path):
        a = _add(tmp_path, composed_text="one")
        _add(tmp_path, composed_text="two")
        assert quarantine.clear_entry(a.id, lore_root=tmp_path) is True
        remaining = quarantine.list_entries(lore_root=tmp_path)
        assert len(remaining) == 1
        assert remaining[0].composed_text == "two"

    def test_clear_missing_returns_false(self, tmp_path: Path):
        assert quarantine.clear_entry("nope", lore_root=tmp_path) is False


class TestKillAll:
    def test_kill_removes_everything_and_returns_count(self, tmp_path: Path):
        _add(tmp_path)
        _add(tmp_path)
        _add(tmp_path)
        n = quarantine.kill_all(lore_root=tmp_path)
        assert n == 3
        assert quarantine.list_entries(lore_root=tmp_path) == []

    def test_kill_empty_returns_zero(self, tmp_path: Path):
        assert quarantine.kill_all(lore_root=tmp_path) == 0
