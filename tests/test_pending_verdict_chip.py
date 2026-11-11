"""Tests for the pending-verdict chip — slice 8 of PRD #65."""

from __future__ import annotations

import json
from pathlib import Path

from lore_core.session_start import pending_verdict_chip as _pending_verdict_chip
from lore_core.freshness import count_pending_verdicts


def _write_catalog(wiki: Path, sections: dict, orphan_set: list[str] | None = None) -> None:
    payload = {
        "wiki": wiki.name,
        "sections": sections,
        "orphan_set": orphan_set or [],
    }
    (wiki / "_catalog.json").write_text(json.dumps(payload))


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


def test_count_superseded_by_is_pending(tmp_path):
    _write_catalog(
        tmp_path,
        sections={
            "concepts": [
                {"path": "concepts/a.md", "name": "a", "superseded_by": "[[b]]"}
            ]
        },
    )
    assert count_pending_verdicts(tmp_path) == (1, False)


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
