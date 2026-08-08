"""Tests for the pending-verdict chip — slice 8 of PRD #65.

The chip counts the rows the picker returns, so each fixture writes the
note file alongside the catalog entry. A bare ``status: stale`` marker
carries no reason, which keeps the note on the worklist; see
`test_pending_verdict_resolution.py` for the recorded-verdict rules.
"""

from __future__ import annotations

import json
from pathlib import Path

from lore_core.session_start import pending_verdict_chip as _pending_verdict_chip
from lore_core.freshness import count_pending_verdicts


def _write_catalog(wiki: Path, sections: dict, orphan_set: list[str] | None = None) -> None:
    """Write the catalog and a matching note for every entry."""
    payload = {
        "wiki": wiki.name,
        "sections": sections,
        "orphan_set": orphan_set or [],
    }
    (wiki / "_catalog.json").write_text(json.dumps(payload))
    for entries in sections.values():
        for entry in entries:
            _write_note(wiki, entry)


def _write_note(wiki: Path, entry: dict) -> None:
    p = wiki / entry["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: concept"]
    for key in ("status", "superseded_by"):
        if entry.get(key):
            lines.append(f"{key}: '{entry[key]}'")
    lines += ["---", "body"]
    p.write_text("\n".join(lines))


def test_count_zero_when_no_catalog(tmp_path):
    assert count_pending_verdicts(tmp_path) == (0, False)


def test_count_zero_when_all_confirmed(tmp_path):
    _write_catalog(
        tmp_path,
        sections={
            "concepts": [{"path": "concepts/a.md", "name": "a", "status": "active"}]
        },
    )
    assert count_pending_verdicts(tmp_path) == (0, False)


def test_count_status_stale_is_pending(tmp_path):
    _write_catalog(
        tmp_path,
        sections={
            "concepts": [{"path": "concepts/a.md", "name": "a", "status": "stale"}]
        },
    )
    assert count_pending_verdicts(tmp_path) == (1, False)


def test_count_superseded_by_is_not_pending(tmp_path):
    """Supersession is a recorded verdict, so the chip stays silent."""
    _write_catalog(
        tmp_path,
        sections={
            "concepts": [
                {"path": "concepts/a.md", "name": "a", "superseded_by": "[[b]]"}
            ]
        },
    )
    assert count_pending_verdicts(tmp_path) == (0, False)


def test_count_orphan_is_pending(tmp_path):
    _write_catalog(
        tmp_path,
        sections={"concepts": [{"path": "concepts/a.md", "name": "a"}]},
        orphan_set=["concepts/a.md"],
    )
    assert count_pending_verdicts(tmp_path) == (1, False)


def test_soft_cap_returns_capped_flag(tmp_path):
    entries = [
        {"path": f"concepts/{i}.md", "name": str(i), "status": "stale"}
        for i in range(20)
    ]
    _write_catalog(tmp_path, sections={"concepts": entries})
    count, capped = count_pending_verdicts(tmp_path, soft_cap=9)
    assert count == 9
    assert capped is True


def test_chip_zero_state_suppressed(tmp_path):
    _write_catalog(
        tmp_path,
        sections={"concepts": [{"path": "a.md", "name": "a"}]},
    )
    assert _pending_verdict_chip(tmp_path) == ""


def test_chip_renders_count(tmp_path):
    _write_catalog(
        tmp_path,
        sections={
            "concepts": [
                {"path": "a.md", "name": "a", "status": "stale"},
                {"path": "b.md", "name": "b", "status": "stale"},
            ]
        },
    )
    assert _pending_verdict_chip(tmp_path) == "2 pending verdicts"


def test_chip_singular_form_for_one(tmp_path):
    _write_catalog(
        tmp_path,
        sections={"concepts": [{"path": "a.md", "name": "a", "status": "stale"}]},
    )
    assert _pending_verdict_chip(tmp_path) == "1 pending verdict"


def test_chip_renders_capped_number(tmp_path):
    entries = [
        {"path": f"concepts/{i}.md", "name": str(i), "status": "stale"}
        for i in range(20)
    ]
    _write_catalog(tmp_path, sections={"concepts": entries})
    chip = _pending_verdict_chip(tmp_path)
    assert chip.startswith("9+")
    assert "verdict" in chip
