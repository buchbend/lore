"""Tests `lore_workflow.seed_epic` — Origin/Findings lift for `/lore-workflow:seed-epic`.

The seed's Origin and Findings sections are lifted straight from the
current session's note (linkage + chapter body) instead of being
freehand-reconstructed. `compose_seed_lift` returns `None` — the signal
callers use to fall back to the existing freehand path — when there's no
note, or the note is too thin (no linkage provenance and no chapter
content beyond the disclaimer) to say anything a freehand pass wouldn't.
"""

from __future__ import annotations

from pathlib import Path

from lore_core import note_document as nd
from lore_core.linkage import Linkage
from lore_workflow.seed_epic import compose_seed_lift

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _note_path(tmp_path: Path) -> Path:
    return tmp_path / "wiki" / "sessions" / "2026" / "07" / "10-1200-topic.md"


def _linkage(**overrides) -> Linkage:
    kwargs = {
        "repo": "buchbend/lore",
        "branch": "feat/226-seed-epic-lift",
        "issues": [],
        "prs": [],
        "epics": [],
        "author": "",
    }
    kwargs.update(overrides)
    return Linkage(**kwargs)


def _note_with_chapter(tmp_path: Path, *, linkage: Linkage | None = None) -> Path:
    path = _note_path(tmp_path)
    nd.create_note(
        path,
        title="Seed lift plumbing",
        description="deterministic seed lift",
        scope="lore",
        created="2026-07-10",
        linkage=linkage,
    )
    chapter = nd.Chapter(
        blocks=[
            nd.TopicBlock(
                lead="The buffer-flush path drops linkage on marker chapters.",
                body="Root-caused to `_apply_linkage` never being called from the marker path.",
                anchor_turn=12,
            )
        ]
    )
    nd.append_chapter(path, chapter, slice_from_turn=0, slice_to_turn=12, linkage=linkage)
    return path


# ---------------------------------------------------------------------------
# Note-derived path (AC1)
# ---------------------------------------------------------------------------


def test_compose_seed_lift_derives_origin_from_linkage(tmp_path: Path) -> None:
    linkage = _linkage(epics=[229], prs=[235])
    path = _note_with_chapter(tmp_path, linkage=linkage)

    lift = compose_seed_lift(path)

    assert lift is not None
    assert "buchbend/lore" in lift.origin
    assert "#229" in lift.origin
    assert "#235" in lift.origin


def test_compose_seed_lift_derives_findings_from_chapter_body(tmp_path: Path) -> None:
    linkage = _linkage(epics=[229])
    path = _note_with_chapter(tmp_path, linkage=linkage)

    lift = compose_seed_lift(path)

    assert lift is not None
    assert "buffer-flush path drops linkage on marker chapters" in lift.findings
    assert "_apply_linkage" in lift.findings
    # Machine-written disclaimer and chapter delimiters are noise for a
    # human-facing seed, not findings — stripped.
    assert nd.DISCLAIMER not in lift.findings
    assert "lore:chapter" not in lift.findings


def test_compose_seed_lift_source_note_is_wiki_relative_when_wiki_root_given(
    tmp_path: Path,
) -> None:
    path = _note_with_chapter(tmp_path, linkage=_linkage(epics=[229]))
    wiki_root = tmp_path / "wiki"

    lift = compose_seed_lift(path, wiki_root=wiki_root)

    assert lift is not None
    assert lift.source_note == "sessions/2026/07/10-1200-topic.md"


def test_compose_seed_lift_source_note_falls_back_to_full_path_without_wiki_root(
    tmp_path: Path,
) -> None:
    path = _note_with_chapter(tmp_path, linkage=_linkage(epics=[229]))

    lift = compose_seed_lift(path)

    assert lift is not None
    assert lift.source_note == str(path)


def test_compose_seed_lift_singular_pr_wording(tmp_path: Path) -> None:
    path = _note_with_chapter(tmp_path, linkage=_linkage(prs=[100]))

    lift = compose_seed_lift(path)

    assert lift is not None
    assert "PR #100" in lift.origin
    assert "PRs #100" not in lift.origin


# ---------------------------------------------------------------------------
# Freehand-fallback path (AC2)
# ---------------------------------------------------------------------------


def test_compose_seed_lift_returns_none_when_note_is_missing(tmp_path: Path) -> None:
    assert compose_seed_lift(tmp_path / "sessions" / "nope.md") is None


def test_compose_seed_lift_returns_none_when_note_is_thin(tmp_path: Path) -> None:
    # A note with no linkage refs and no chapters — just the disclaimer —
    # carries nothing a freehand pass wouldn't already have.
    path = _note_path(tmp_path)
    nd.create_note(
        path,
        title="Empty session",
        description="nothing happened",
        scope="lore",
        created="2026-07-10",
    )

    assert compose_seed_lift(path) is None


def test_compose_seed_lift_returns_none_when_only_marker_chapters(tmp_path: Path) -> None:
    # Withheld/failed marker chapters are procedural bookkeeping, not
    # findings — a note with only markers and no linkage is still thin.
    path = _note_path(tmp_path)
    nd.create_note(
        path,
        title="Gate-withheld session",
        description="publish gate withheld the chapter",
        scope="lore",
        created="2026-07-10",
    )
    nd.append_marker_chapter(
        path, kind=nd.MARKER_WITHHELD, reason="quote mismatch", slice_from_turn=0, slice_to_turn=5
    )

    assert compose_seed_lift(path) is None
