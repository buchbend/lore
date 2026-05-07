"""Issue #53: ``_resolve_active_part`` scans ``_done/`` for Part-N continuation.

Verifies the acceptance criteria directly: a raw `_done/<stem>.state.json`
(no live buffer) is discovered and yields ``(2, <prior_stem>)``.  The
stem-prefix filter is exercised by placing an unrelated entry in ``_done/``
and confirming it does not affect the result.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lore_curator.buffer_store import done_dir
from lore_curator.session_curator import _resolve_active_part

TRANSCRIPT_X = "transcript_x"
DATE_Y = "2026-05-01"
_COMPACT = DATE_Y.replace("-", "")
STEM_PART1 = f"{TRANSCRIPT_X}__{_COMPACT}"


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    return tmp_path


def _write_done_sidecar(
    lore_root: Path,
    *,
    stem: str,
    transcript_id: str,
    local_date: str,
    part_index: int,
    state: str = "closed",
) -> Path:
    """Drop a minimal sidecar JSON into ``_done/`` without buffer machinery."""
    d = done_dir(lore_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stem}.state.json"
    p.write_text(
        json.dumps(
            {
                "transcript_id": transcript_id,
                "local_date": local_date,
                "part_index": part_index,
                "state": state,
            }
        )
    )
    return p


def test_resolve_after_done_part1_returns_part2(lore_root: Path) -> None:
    """Single ``_done/`` sidecar for (transcript_X, date_Y, part=1) and no
    live buffer → resolver must return ``(2, <prior_stem>)``."""
    _write_done_sidecar(
        lore_root,
        stem=STEM_PART1,
        transcript_id=TRANSCRIPT_X,
        local_date=DATE_Y,
        part_index=1,
    )

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id=TRANSCRIPT_X, local_date=DATE_Y
    )

    assert part_index == 2
    assert prev == STEM_PART1


def test_stem_prefix_filter_ignores_unrelated_done_entry(lore_root: Path) -> None:
    """Unrelated ``_done/`` entry (different transcript_id) must not bump the
    part_index for our pair — confirms the stem-prefix filter is effective."""
    _write_done_sidecar(
        lore_root,
        stem=f"other_transcript__{_COMPACT}",
        transcript_id="other_transcript",
        local_date=DATE_Y,
        part_index=99,
    )

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id=TRANSCRIPT_X, local_date=DATE_Y
    )

    assert part_index == 1
    assert prev is None


def test_resolve_after_done_part2_returns_part3(lore_root: Path) -> None:
    """Two archived parts → resolver returns ``(3, <part2_stem>)``."""
    stem_part2 = f"{TRANSCRIPT_X}__{_COMPACT}__part2"

    _write_done_sidecar(
        lore_root,
        stem=STEM_PART1,
        transcript_id=TRANSCRIPT_X,
        local_date=DATE_Y,
        part_index=1,
    )
    _write_done_sidecar(
        lore_root,
        stem=stem_part2,
        transcript_id=TRANSCRIPT_X,
        local_date=DATE_Y,
        part_index=2,
    )

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id=TRANSCRIPT_X, local_date=DATE_Y
    )

    assert part_index == 3
    assert prev == stem_part2
