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

import yaml
from lore_core import note_document as nd
from lore_core.linkage import Linkage
from lore_core.schema import parse_frontmatter, strip_frontmatter
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


def _seed_note(
    tmp_path: Path, *, title: str, description: str, linkage: Linkage | None = None
) -> Path:
    """Write a minimal note file directly.

    The chapter lifecycle that once built one (create_note, append_chapter,
    Chapter, TopicBlock) is gone — seed_epic only reads a note through
    read_note, so the fixture writes raw frontmatter + body to match.
    """
    path = _note_path(tmp_path)
    fm = {
        "schema_version": 2,
        "type": "session",
        "note_status": "open",
        "created": "2026-07-10",
        "last_reviewed": "2026-07-10",
        "title": title,
        "description": description,
        "scope": "lore",
        "chapters": [],
    }
    if linkage is not None:
        fm["linkage"] = {
            "schema_version": linkage.schema_version,
            "repo": linkage.repo,
            "branch": linkage.branch,
            "issues": list(linkage.issues),
            "prs": list(linkage.prs),
            "epics": list(linkage.epics),
            "author": linkage.author,
            "trace_id": linkage.trace_id,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{nd.DISCLAIMER}\n")
    return path


def _append_topic_chapter(
    path: Path, *, lead: str, body: str, anchor_turn: int, from_turn: int, to_turn: int
) -> None:
    """Append a topic chapter in the exact shape the old renderer wrote.

    seed_epic parses this straight out of the body text (_CHAPTER_HEADER_RE),
    so the fixture writes the markdown by hand rather than through the
    deleted Chapter/TopicBlock/append_chapter API.
    """
    fm = parse_frontmatter(path.read_text())
    existing_body = strip_frontmatter(path.read_text())
    n = len(fm.get("chapters") or []) + 1
    delimiter = f"<!-- lore:chapter {n} @{from_turn}-{to_turn} -->"
    segment = f"{delimiter}\n\n**{lead}** {body}\n\n@{anchor_turn}"
    new_body = f"{existing_body.rstrip()}\n\n{segment}"
    chapters = list(fm.get("chapters") or [])
    chapters.append({"n": n, "kind": "topic", "from_turn": from_turn, "to_turn": to_turn})
    fm["chapters"] = chapters
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{new_body.rstrip()}\n")


def _note_with_chapter(tmp_path: Path, *, linkage: Linkage | None = None) -> Path:
    path = _seed_note(
        tmp_path, title="Seed lift plumbing", description="deterministic seed lift", linkage=linkage
    )
    _append_topic_chapter(
        path,
        lead="The buffer-flush path drops linkage on marker chapters.",
        body="Root-caused to `_apply_linkage` never being called from the marker path.",
        anchor_turn=12,
        from_turn=0,
        to_turn=12,
    )
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
    path = _seed_note(tmp_path, title="Empty session", description="nothing happened")

    assert compose_seed_lift(path) is None


def test_compose_seed_lift_returns_none_when_only_marker_chapters(tmp_path: Path) -> None:
    # Withheld/failed marker chapters are procedural bookkeeping, not
    # findings — a note with only markers and no linkage is still thin.
    path = _seed_note(
        tmp_path, title="Gate-withheld session", description="publish gate withheld the chapter"
    )
    nd.append_marker_chapter(
        path, kind=nd.MARKER_WITHHELD, reason="quote mismatch", slice_from_turn=0, slice_to_turn=5
    )

    assert compose_seed_lift(path) is None
