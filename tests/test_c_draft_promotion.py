"""Task 9: draft-promotion proposal pass (time-based; proposes only)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest


def _write_draft(
    path: Path,
    *,
    created: date,
    draft: bool = True,
    promotion_candidate: bool | None = None,
) -> Path:
    lines = [
        "---",
        "type: concept",
        f"created: {created.isoformat()}",
        f"last_reviewed: {created.isoformat()}",
        "description: draft note",
        "tags: []",
    ]
    if draft:
        lines.append("draft: true")
    if promotion_candidate is not None:
        lines.append(f"promotion_candidate: {str(promotion_candidate).lower()}")
    lines += ["---", "", "body"]
    path.write_text("\n".join(lines) + "\n")
    return path


def _seed(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "sessions").mkdir(parents=True)
    return wiki


# ---------------------------------------------------------------------------
# _pass_draft_promotion unit tests
# ---------------------------------------------------------------------------


def test_promotion_proposes_on_15d_old_draft(tmp_path: Path) -> None:
    from lore_curator.defrag_curator import _pass_draft_promotion

    wiki = _seed(tmp_path)
    today = date(2026, 4, 21)
    _write_draft(wiki / "sessions" / "a.md", created=today - timedelta(days=15))

    actions = _pass_draft_promotion(wiki, today)
    assert len(actions) == 1
    assert actions[0].kind == "promote-draft"
    assert actions[0].patch == {"promotion_candidate": True}


def test_promotion_skips_at_exact_14d(tmp_path: Path) -> None:
    """Boundary: created EXACTLY 14d ago → NOT a candidate (exclusive)."""
    from lore_curator.defrag_curator import _pass_draft_promotion

    wiki = _seed(tmp_path)
    today = date(2026, 4, 21)
    _write_draft(wiki / "sessions" / "a.md", created=today - timedelta(days=14))

    actions = _pass_draft_promotion(wiki, today)
    assert actions == [], "14d boundary exclusive — must NOT propose"


def test_promotion_skips_recent_drafts(tmp_path: Path) -> None:
    from lore_curator.defrag_curator import _pass_draft_promotion

    wiki = _seed(tmp_path)
    today = date(2026, 4, 21)
    _write_draft(wiki / "sessions" / "a.md", created=today - timedelta(days=3))

    assert _pass_draft_promotion(wiki, today) == []


def test_promotion_never_flips_draft_false(tmp_path: Path) -> None:
    """The action patch writes promotion_candidate only; draft stays true."""
    from lore_curator.defrag_curator import _pass_draft_promotion, _apply_patch

    wiki = _seed(tmp_path)
    today = date(2026, 4, 21)
    path = _write_draft(
        wiki / "sessions" / "a.md", created=today - timedelta(days=30)
    )

    actions = _pass_draft_promotion(wiki, today)
    assert len(actions) == 1
    new_text = _apply_patch(path.read_text(), actions[0].patch)
    # Patch must add promotion_candidate but leave draft: true intact.
    assert "promotion_candidate: true" in new_text
    assert "draft: true" in new_text


def test_promotion_skips_non_drafts(tmp_path: Path) -> None:
    from lore_curator.defrag_curator import _pass_draft_promotion

    wiki = _seed(tmp_path)
    today = date(2026, 4, 21)
    _write_draft(
        wiki / "sessions" / "a.md", created=today - timedelta(days=30), draft=False
    )

    assert _pass_draft_promotion(wiki, today) == []


def test_promotion_idempotent(tmp_path: Path) -> None:
    """Once promotion_candidate is set, don't propose again."""
    from lore_curator.defrag_curator import _pass_draft_promotion

    wiki = _seed(tmp_path)
    today = date(2026, 4, 21)
    _write_draft(
        wiki / "sessions" / "a.md",
        created=today - timedelta(days=30),
        promotion_candidate=True,
    )

    assert _pass_draft_promotion(wiki, today) == []
