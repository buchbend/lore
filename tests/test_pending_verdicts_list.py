"""Tests for ``lore_core.freshness.list_pending_verdicts`` and the
shared ``_is_pending_from_catalog_entry`` predicate.

These cover the picker-ready data the new ``lore_pending_verdicts``
MCP tool returns. The same predicate gates ``count_pending_verdicts``
(the status-line chip) and ``list_pending_verdicts`` (the picker), so
the regression bar is "if the chip says N, the picker shows N rows
with the same flagged-ness."
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lore_core.freshness import (
    PendingEntry,
    _is_pending_from_catalog_entry,
    count_pending_verdicts,
    list_pending_verdicts,
    pending_entry_to_dict,
)


# ---------------------------------------------------------------------------
# Catalog fixture helpers
# ---------------------------------------------------------------------------


def _write_catalog(
    wiki: Path, sections: dict[str, list[dict]], orphan_set: list[str] | None = None
) -> None:
    data = {"sections": sections}
    if orphan_set is not None:
        data["orphan_set"] = orphan_set
    (wiki / "_catalog.json").write_text(json.dumps(data))


def _write_note(wiki: Path, rel: str, frontmatter: dict, body: str = "body") -> Path:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, str):
            lines.append(f"{k}: '{v}'")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(body)
    p.write_text("\n".join(lines))
    return p


# ---------------------------------------------------------------------------
# Shared predicate — the contract both the chip and the picker depend on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry, orphans, expected",
    [
        ({"path": "a.md", "status": "stale"}, set(), True),
        ({"path": "a.md", "status": "STALE"}, set(), True),  # case-insensitive
        ({"path": "a.md", "superseded_by": "[[b]]"}, set(), True),
        ({"path": "a.md"}, {"a.md"}, True),
        ({"path": "a.md"}, set(), False),
        ({"path": "a.md", "status": "draft"}, set(), False),
        ({"path": "", "status": "stale"}, set(), True),  # status alone wins
        ({"path": "", "superseded_by": ""}, set(), False),  # empty string falsy
        ({"path": "", "superseded_by": None}, set(), False),
        ({}, {"a.md"}, False),  # empty path → orphan check skipped
        ("not-a-dict", set(), False),  # type guard
    ],
)
def test_is_pending_from_catalog_entry(entry, orphans, expected) -> None:
    assert _is_pending_from_catalog_entry(entry, orphans) is expected


def test_count_and_list_agree_on_flagged_set(tmp_path: Path) -> None:
    """The chip predicate and the picker predicate must never drift.

    Same catalog → same count from both surfaces.
    """
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "a.md", {"status": "stale", "stale_by": "u", "stale_at": "2026-05-01", "stale_reason": "x"})
    _write_note(wiki, "b.md", {"superseded_by": "[[c]]"})
    _write_note(wiki, "c.md", {})  # plain, no markers — but in orphan set
    _write_note(wiki, "d.md", {})  # not flagged anywhere
    _write_catalog(
        wiki,
        {
            "notes": [
                {"path": "a.md", "status": "stale"},
                {"path": "b.md", "superseded_by": "[[c]]"},
                {"path": "c.md"},
                {"path": "d.md"},
            ]
        },
        orphan_set=["c.md"],
    )

    count, capped = count_pending_verdicts(wiki)
    entries = list_pending_verdicts(wiki, handle="")

    assert capped is False
    assert count == len(entries) == 3


# ---------------------------------------------------------------------------
# list_pending_verdicts — empty / cache miss
# ---------------------------------------------------------------------------


def test_list_empty_when_no_catalog(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    assert list_pending_verdicts(wiki, handle="") == []


def test_list_empty_when_no_flagged_entries(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "a.md", {})
    _write_catalog(wiki, {"notes": [{"path": "a.md"}]})
    assert list_pending_verdicts(wiki, handle="") == []


def test_list_empty_when_catalog_malformed(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    (wiki / "_catalog.json").write_text("not json {{{")
    assert list_pending_verdicts(wiki, handle="") == []


# ---------------------------------------------------------------------------
# Cause + reason wiring
# ---------------------------------------------------------------------------


def test_authored_marker_cause_with_status_stale(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(
        wiki,
        "concepts/old.md",
        {
            "status": "stale",
            "stale_by": "alice",
            "stale_at": "2026-05-01",
            "stale_reason": "rewritten",
        },
    )
    _write_catalog(wiki, {"notes": [{"path": "concepts/old.md", "status": "stale"}]})

    entries = list_pending_verdicts(wiki, handle="")

    assert len(entries) == 1
    e = entries[0]
    assert e.cause == "authored_marker"
    assert e.reason == "marked stale"
    assert e.slug == "old"
    assert e.path == "concepts/old.md"
    assert e.disagreement is None
    assert e.confirmed_at is None


def test_authored_marker_cause_with_superseded_by(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "a.md", {"superseded_by": "[[newer]]"})
    _write_catalog(wiki, {"notes": [{"path": "a.md", "superseded_by": "[[newer]]"}]})

    entries = list_pending_verdicts(wiki, handle="")
    assert len(entries) == 1
    assert entries[0].cause == "authored_marker"
    assert "superseded by" in entries[0].reason


def test_orphan_broken_cause(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "linky.md", {})  # no authored markers
    _write_catalog(
        wiki,
        {"notes": [{"path": "linky.md"}]},
        orphan_set=["linky.md"],
    )

    entries = list_pending_verdicts(wiki, handle="")
    assert len(entries) == 1
    assert entries[0].cause == "orphan_broken"
    assert entries[0].reason == "contains a broken wikilink"


def test_authored_takes_precedence_over_orphan(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "n.md", {"status": "stale"})
    _write_catalog(
        wiki,
        {"notes": [{"path": "n.md", "status": "stale"}]},
        orphan_set=["n.md"],
    )

    entries = list_pending_verdicts(wiki, handle="")
    assert len(entries) == 1
    assert entries[0].cause == "authored_marker"


def test_catalog_flagged_but_no_per_note_signal_skipped(tmp_path: Path) -> None:
    """Catalog says flagged, but the note's frontmatter no longer
    matches and it's not in the orphan set. Sanitised mid-flight.
    The picker silently drops the entry rather than emit a no-cause row.
    """
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "n.md", {})  # frontmatter has no markers anymore
    # Catalog still has stale state — picker should reconcile.
    _write_catalog(wiki, {"notes": [{"path": "n.md", "status": "stale"}]})

    assert list_pending_verdicts(wiki, handle="") == []


# ---------------------------------------------------------------------------
# Disagreement detection wires through
# ---------------------------------------------------------------------------


def test_disagreement_surfaced_when_personal_confirm_after_stale(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(
        wiki,
        "n.md",
        {
            "status": "stale",
            "stale_by": "alice",
            "stale_at": "2026-05-01",
            "stale_reason": "ohno",
        },
    )
    # Personal sidecar — current user confirmed AFTER the stale verdict
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "bob.json").write_text(
        json.dumps({"confirmed": {"n.md": "2026-05-05"}})
    )
    _write_catalog(wiki, {"notes": [{"path": "n.md", "status": "stale"}]})

    entries = list_pending_verdicts(wiki, handle="bob")
    assert len(entries) == 1
    e = entries[0]
    assert e.disagreement is not None
    assert e.disagreement.stale_by == "alice"
    assert e.disagreement.stale_reason == "ohno"
    assert e.confirmed_at == date(2026, 5, 5)


def test_no_disagreement_when_confirm_precedes_stale(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(
        wiki,
        "n.md",
        {
            "status": "stale",
            "stale_by": "alice",
            "stale_at": "2026-05-10",
            "stale_reason": "x",
        },
    )
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "bob.json").write_text(
        json.dumps({"confirmed": {"n.md": "2026-05-01"}})  # before
    )
    _write_catalog(wiki, {"notes": [{"path": "n.md", "status": "stale"}]})

    entries = list_pending_verdicts(wiki, handle="bob")
    assert entries[0].disagreement is None


def test_no_handle_means_no_sidecar_read(tmp_path: Path) -> None:
    """Empty handle → confirmed_at always None regardless of sidecar contents."""
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "n.md", {"status": "stale", "stale_at": "2026-05-01", "stale_by": "alice", "stale_reason": "x"})
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "bob.json").write_text(
        json.dumps({"confirmed": {"n.md": "2026-05-05"}})
    )
    _write_catalog(wiki, {"notes": [{"path": "n.md", "status": "stale"}]})

    entries = list_pending_verdicts(wiki, handle="")
    assert entries[0].confirmed_at is None
    assert entries[0].disagreement is None


# ---------------------------------------------------------------------------
# Sort order: disagreements → authored_marker → orphan_broken
# ---------------------------------------------------------------------------


def test_sort_disagreements_first(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    # Two stale entries — one with disagreement, one without.
    _write_note(
        wiki,
        "a-plain.md",
        {"status": "stale", "stale_at": "2026-05-10", "stale_by": "u", "stale_reason": "x"},
    )
    _write_note(
        wiki,
        "b-disagreement.md",
        {"status": "stale", "stale_at": "2026-05-01", "stale_by": "alice", "stale_reason": "x"},
    )
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "bob.json").write_text(
        json.dumps({"confirmed": {"b-disagreement.md": "2026-05-05"}})
    )
    _write_catalog(
        wiki,
        {"notes": [
            {"path": "a-plain.md", "status": "stale"},
            {"path": "b-disagreement.md", "status": "stale"},
        ]},
    )

    entries = list_pending_verdicts(wiki, handle="bob")
    assert [e.path for e in entries] == ["b-disagreement.md", "a-plain.md"]


def test_sort_authored_marker_before_orphan_broken(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "x-orphan.md", {})
    _write_note(wiki, "y-authored.md", {"status": "stale", "stale_at": "2026-05-01", "stale_by": "u", "stale_reason": "x"})
    _write_catalog(
        wiki,
        {"notes": [
            {"path": "x-orphan.md"},
            {"path": "y-authored.md", "status": "stale"},
        ]},
        orphan_set=["x-orphan.md"],
    )

    entries = list_pending_verdicts(wiki, handle="")
    assert [e.cause for e in entries] == ["authored_marker", "orphan_broken"]


def test_sort_most_recent_stale_first_within_bucket(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(
        wiki,
        "older.md",
        {"status": "stale", "stale_at": "2026-04-01", "stale_by": "u", "stale_reason": "x"},
    )
    _write_note(
        wiki,
        "newer.md",
        {"status": "stale", "stale_at": "2026-05-10", "stale_by": "u", "stale_reason": "x"},
    )
    _write_catalog(
        wiki,
        {"notes": [
            {"path": "older.md", "status": "stale"},
            {"path": "newer.md", "status": "stale"},
        ]},
    )

    entries = list_pending_verdicts(wiki, handle="")
    assert [e.path for e in entries] == ["newer.md", "older.md"]


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def test_pending_entry_to_dict_shape(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(
        wiki,
        "n.md",
        {
            "status": "stale",
            "stale_by": "alice",
            "stale_at": "2026-05-01",
            "stale_reason": "rewritten",
        },
    )
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "bob.json").write_text(
        json.dumps({"confirmed": {"n.md": "2026-05-05"}})
    )
    _write_catalog(wiki, {"notes": [{"path": "n.md", "status": "stale"}]})

    entries = list_pending_verdicts(wiki, handle="bob")
    d = pending_entry_to_dict(entries[0])
    assert d == {
        "path": "n.md",
        "slug": "n",
        "cause": "authored_marker",
        "reason": "marked stale",
        "confirmed_at": "2026-05-05",
        "disagreement": {
            "stale_by": "alice",
            "stale_at": "2026-05-01",
            "stale_reason": "rewritten",
            "self_confirmed_at": "2026-05-05",
        },
    }


def test_pending_entry_to_dict_minimal(tmp_path: Path) -> None:
    wiki = tmp_path / "w"
    wiki.mkdir()
    _write_note(wiki, "n.md", {"superseded_by": "[[other]]"})
    _write_catalog(wiki, {"notes": [{"path": "n.md", "superseded_by": "[[other]]"}]})

    entries = list_pending_verdicts(wiki, handle="")
    d = pending_entry_to_dict(entries[0])
    assert d["confirmed_at"] is None
    assert d["disagreement"] is None
    assert d["cause"] == "authored_marker"
