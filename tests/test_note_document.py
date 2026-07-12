"""Unit tests for the deterministic session-note document core.

The document core owns one note per session: a machine-written genre
disclaimer, machine-first frontmatter, and a chronological sequence of
chapters (each a set of topic blocks). The note is append-only until
``close``; after close it is immutable. Marker chapters record failed
and withheld chapters in deterministic text. No LLM is involved anywhere
in this layer.

Vocabulary (PRD 0001): session note, chapter, topic block, lead,
continuation block, disclaimer, marker chapter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core import note_document as nd
from lore_core.schema import parse_frontmatter, strip_frontmatter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions" / "2026" / "07" / "03-1200-topic.md"


def _create(tmp_path: Path, **overrides):
    path = overrides.pop("path", _note_path(tmp_path))
    kwargs = {
        "title": "Working on the buffer flush path",
        "description": "deterministic session note",
        "scope": "lore",
        "created": "2026-07-03",
    }
    kwargs.update(overrides)
    nd.create_note(path, **kwargs)
    return path


def _block(lead="A thing happened.", body="Some prose.", anchor=5, **kw):
    return nd.TopicBlock(lead=lead, body=body, anchor_turn=anchor, **kw)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_writes_disclaimer_and_open_frontmatter(tmp_path):
    path = _create(tmp_path)
    text = path.read_text()
    fm = parse_frontmatter(text)
    body = strip_frontmatter(text)

    # Fixed machine-written genre disclaimer travels in the body.
    assert nd.DISCLAIMER in body
    # Machine-first frontmatter, note is open (mutable) after create.
    assert fm["type"] == "session"
    assert fm["note_status"] == "open"
    assert fm["schema_version"] == 2
    assert fm["created"] == "2026-07-03"
    assert fm["title"] == "Working on the buffer flush path"
    assert fm["scope"] == "lore"
    assert nd.is_closed(path) is False


def test_create_records_session_facts_in_frontmatter_only(tmp_path):
    facts = nd.SessionFacts(
        commits=["abc1234 wire up buffer", "def5678 fix cap-trip"],
        prs=["#120"],
        files_modified=["lib/lore_core/note_document.py"],
        files_read=["docs/prd/0001-trim-to-lab-notebook-notes.md"],
        projects=["lore"],
        duration_seconds=3600,
    )
    path = _create(tmp_path, facts=facts)
    text = path.read_text()
    fm = parse_frontmatter(text)
    body = strip_frontmatter(text)

    # Session facts live in frontmatter.
    assert fm["commits"] == ["abc1234 wire up buffer", "def5678 fix cap-trip"]
    assert fm["prs"] == ["#120"]
    assert fm["files_modified"] == ["lib/lore_core/note_document.py"]
    assert fm["files_read"] == ["docs/prd/0001-trim-to-lab-notebook-notes.md"]
    assert fm["projects"] == ["lore"]
    assert fm["duration_seconds"] == 3600

    # ...and are NOT re-narrated in the body (no Activity/Commits section).
    assert "abc1234" not in body
    assert "## Commits" not in body
    assert "## Activity" not in body


def test_create_includes_handle_when_team_mode(tmp_path):
    path = _create(tmp_path, handle="alice")
    fm = parse_frontmatter(path.read_text())
    assert fm["user"] == "alice"


def test_create_exclusive_refuses_to_clobber_existing_note(tmp_path):
    """exclusive=True must refuse an existing path instead of overwriting it.

    Two authors/sessions racing on the same first-write path (same slug,
    same minute) must not have one silently clobber the other's note.
    """
    path = _note_path(tmp_path)
    _create(tmp_path, path=path, title="alice's note")
    with pytest.raises(FileExistsError):
        _create(tmp_path, path=path, title="bob's note", exclusive=True)
    # alice's note survives untouched.
    assert "alice's note" in path.read_text()


def test_create_exclusive_succeeds_for_a_free_path(tmp_path):
    path = _create(tmp_path, title="alice's note", exclusive=True)
    assert "alice's note" in path.read_text()


# ---------------------------------------------------------------------------
# linkage frontmatter (schema-versioned, round-trips)
# ---------------------------------------------------------------------------


def test_create_writes_linkage_block(tmp_path):
    path = _create(
        tmp_path,
        linkage=nd.Linkage(
            repo="buchbend/lore",
            branch="feat/175-linkage-frontmatter",
            issues=[175],
            epics=[162],
            author="Christof Buchbender",
        ),
    )
    fm = parse_frontmatter(path.read_text())
    assert fm["linkage"] == {
        "schema_version": 1,
        "repo": "buchbend/lore",
        "branch": "feat/175-linkage-frontmatter",
        "issues": [175],
        "prs": [],
        "epics": [162],
        "author": "Christof Buchbender",
        "trace_id": None,
    }


def test_create_without_linkage_omits_block(tmp_path):
    path = _create(tmp_path)
    fm = parse_frontmatter(path.read_text())
    assert "linkage" not in fm


def test_append_chapter_updates_linkage(tmp_path):
    path = _create(tmp_path, linkage=nd.Linkage(branch="main"))
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block()]),
        slice_from_turn=1,
        slice_to_turn=10,
        linkage=nd.Linkage(branch="main", issues=[175]),
    )
    fm = parse_frontmatter(path.read_text())
    assert fm["linkage"]["issues"] == [175]


def test_close_note_can_record_final_linkage(tmp_path):
    path = _create(tmp_path)
    nd.close_note(path, linkage=nd.Linkage(repo="buchbend/lore", branch="main"))
    fm = parse_frontmatter(path.read_text())
    assert fm["linkage"]["repo"] == "buchbend/lore"


# ---------------------------------------------------------------------------
# append chapter
# ---------------------------------------------------------------------------


def test_append_chapter_renders_blocks_and_records_range(tmp_path):
    path = _create(tmp_path)
    chapter = nd.Chapter(
        blocks=[
            _block(
                lead="Chose an append-only note model.",
                body="We decided the note file is append-only until close.",
                anchor=12,
            ),
            _block(
                lead="Removed Part-N splitting.",
                body="One session now yields exactly one note.",
                anchor=28,
            ),
        ]
    )
    n = nd.append_chapter(path, chapter, slice_from_turn=1, slice_to_turn=40)
    assert n == 1

    text = path.read_text()
    body = strip_frontmatter(text)
    fm = parse_frontmatter(text)

    # Leads are bold one-sentence; body prose present; one @turn anchor per block.
    assert "**Chose an append-only note model.**" in body
    assert "We decided the note file is append-only until close." in body
    assert "@12" in body
    assert "**Removed Part-N splitting.**" in body
    assert "@28" in body

    # Chapter<->slice turn range recorded deterministically in frontmatter.
    assert fm["chapters"] == [
        {"n": 1, "kind": "topic", "from_turn": 1, "to_turn": 40},
    ]


def test_append_chapter_renders_lead_and_body_as_one_inline_paragraph(tmp_path):
    """Note-format v2 (#222): lead sentence is inline with its body, not a
    standalone bold line floating above a blank-line-separated paragraph.
    """
    path = _create(tmp_path)
    chapter = nd.Chapter(
        blocks=[
            _block(
                lead="Chose an append-only note model.",
                body="We decided the note file is append-only until close.",
            )
        ]
    )
    nd.append_chapter(path, chapter, slice_from_turn=1, slice_to_turn=10)

    body = strip_frontmatter(path.read_text())
    assert (
        "**Chose an append-only note model.** "
        "We decided the note file is append-only until close."
    ) in body


def test_append_chapter_sets_title_only_on_first_chapter(tmp_path):
    """Note-format v2 (#222): the LLM never composes a title, so the flush
    passes a deterministically-derived one; only the first chapter may set
    it (the placeholder from create_note stands until then).
    """
    path = _create(tmp_path, title="lore session — 2026-07-03")
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block(lead="First.", anchor=3)]),
        slice_from_turn=1,
        slice_to_turn=20,
        title="lore: Traced the flush race",
    )
    fm = parse_frontmatter(path.read_text())
    assert fm["title"] == "lore: Traced the flush race"


def test_append_chapter_title_ignored_after_first_chapter(tmp_path):
    path = _create(tmp_path, title="lore session — 2026-07-03")
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block(lead="First.", anchor=3)]),
        slice_from_turn=1,
        slice_to_turn=20,
        title="lore: Traced the flush race",
    )
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block(lead="Second.", anchor=44)]),
        slice_from_turn=21,
        slice_to_turn=60,
        title="lore: Some later chapter's lead",
    )
    fm = parse_frontmatter(path.read_text())
    assert fm["title"] == "lore: Traced the flush race"


def test_append_multiple_chapters_are_chronological_with_ranges(tmp_path):
    path = _create(tmp_path)
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block(lead="First.", anchor=3)]),
        slice_from_turn=1,
        slice_to_turn=20,
    )
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block(lead="Second.", anchor=44)]),
        slice_from_turn=21,
        slice_to_turn=60,
    )

    text = path.read_text()
    body = strip_frontmatter(text)
    fm = parse_frontmatter(text)

    # Chronological order preserved in the body.
    assert body.index("**First.**") < body.index("**Second.**")
    assert [c["n"] for c in fm["chapters"]] == [1, 2]
    assert fm["chapters"][1] == {"n": 2, "kind": "topic", "from_turn": 21, "to_turn": 60}


def test_continuation_block_renders_continued_lead(tmp_path):
    path = _create(tmp_path)
    chapter = nd.Chapter(
        blocks=[
            _block(
                lead="Resolved the earlier open question.",
                body="The cap-trip now flushes in place.",
                anchor=51,
                continued=True,
                continued_topic="cap-trip handling",
            ),
        ]
    )
    nd.append_chapter(path, chapter, slice_from_turn=41, slice_to_turn=70)
    body = strip_frontmatter(path.read_text())
    assert "**Continued: cap-trip handling**" in body
    assert "The cap-trip now flushes in place." in body


def test_block_quote_renders_between_body_and_anchor(tmp_path):
    path = _create(tmp_path)
    chapter = nd.Chapter(
        blocks=[
            _block(
                lead="Found the root cause.",
                body="The cache never invalidated.",
                anchor=12,
                quote="the cache never gets cleared on deploy",
            ),
        ]
    )
    nd.append_chapter(path, chapter, slice_from_turn=1, slice_to_turn=40)
    body = strip_frontmatter(path.read_text())
    assert '"the cache never gets cleared on deploy"' in body
    assert body.index("The cache never invalidated.") < body.index("the cache never gets cleared")
    assert body.index("the cache never gets cleared") < body.index("@12")


def test_append_updates_session_facts(tmp_path):
    path = _create(tmp_path, facts=nd.SessionFacts(commits=["aaa0001 first"]))
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block()]),
        slice_from_turn=1,
        slice_to_turn=10,
        facts=nd.SessionFacts(commits=["aaa0001 first", "bbb0002 second"], files_modified=["a.py"]),
    )
    fm = parse_frontmatter(path.read_text())
    assert fm["commits"] == ["aaa0001 first", "bbb0002 second"]
    assert fm["files_modified"] == ["a.py"]


# ---------------------------------------------------------------------------
# marker chapters
# ---------------------------------------------------------------------------


def test_marker_chapter_withheld_is_deterministic_text(tmp_path):
    path = _create(tmp_path)
    n = nd.append_marker_chapter(
        path,
        kind=nd.MARKER_WITHHELD,
        reason="planted secret detected",
        slice_from_turn=41,
        slice_to_turn=80,
    )
    assert n == 1
    text = path.read_text()
    body = strip_frontmatter(text)
    fm = parse_frontmatter(text)

    assert "Withheld chapter" in body
    assert "planted secret detected" in body
    assert fm["chapters"] == [
        {
            "n": 1,
            "kind": "marker",
            "marker": "withheld",
            "reason": "planted secret detected",
            "from_turn": 41,
            "to_turn": 80,
        },
    ]


def test_marker_chapter_failed_is_deterministic_text(tmp_path):
    path = _create(tmp_path)
    nd.append_marker_chapter(
        path,
        kind=nd.MARKER_FAILED,
        reason="compose gave up after 2 attempts",
        slice_from_turn=1,
        slice_to_turn=120,
    )
    body = strip_frontmatter(path.read_text())
    assert "Failed chapter" in body
    assert "compose gave up after 2 attempts" in body


def test_marker_chapter_rejects_unknown_kind(tmp_path):
    path = _create(tmp_path)
    with pytest.raises(ValueError):
        nd.append_marker_chapter(
            path,
            kind="bogus",
            reason="x",
            slice_from_turn=1,
            slice_to_turn=2,
        )


def test_topic_and_marker_chapters_share_numbering(tmp_path):
    path = _create(tmp_path)
    nd.append_chapter(path, nd.Chapter(blocks=[_block()]), slice_from_turn=1, slice_to_turn=10)
    nd.append_marker_chapter(
        path, kind=nd.MARKER_WITHHELD, reason="pii", slice_from_turn=11, slice_to_turn=20
    )
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[_block(lead="After marker.")]),
        slice_from_turn=21,
        slice_to_turn=30,
    )
    fm = parse_frontmatter(path.read_text())
    assert [(c["n"], c["kind"]) for c in fm["chapters"]] == [
        (1, "topic"),
        (2, "marker"),
        (3, "topic"),
    ]


# ---------------------------------------------------------------------------
# close + immutability
# ---------------------------------------------------------------------------


def test_close_marks_note_closed(tmp_path):
    path = _create(tmp_path)
    nd.append_chapter(path, nd.Chapter(blocks=[_block()]), slice_from_turn=1, slice_to_turn=10)
    nd.close_note(path)
    fm = parse_frontmatter(path.read_text())
    assert fm["note_status"] == "closed"
    assert nd.is_closed(path) is True


def test_append_chapter_after_close_is_rejected_and_leaves_note_unchanged(tmp_path):
    path = _create(tmp_path)
    nd.append_chapter(
        path, nd.Chapter(blocks=[_block(lead="Only chapter.")]), slice_from_turn=1, slice_to_turn=10
    )
    nd.close_note(path)
    before = path.read_text()

    with pytest.raises(nd.NoteClosedError):
        nd.append_chapter(
            path,
            nd.Chapter(blocks=[_block(lead="Too late.")]),
            slice_from_turn=11,
            slice_to_turn=20,
        )

    assert path.read_text() == before  # immutable: no partial mutation


def test_marker_chapter_after_close_is_rejected(tmp_path):
    path = _create(tmp_path)
    nd.close_note(path)
    with pytest.raises(nd.NoteClosedError):
        nd.append_marker_chapter(
            path, kind=nd.MARKER_FAILED, reason="late", slice_from_turn=1, slice_to_turn=2
        )


def test_close_after_close_is_rejected(tmp_path):
    path = _create(tmp_path)
    nd.close_note(path)
    with pytest.raises(nd.NoteClosedError):
        nd.close_note(path)


def test_close_can_record_final_facts(tmp_path):
    path = _create(tmp_path)
    nd.close_note(path, facts=nd.SessionFacts(duration_seconds=7200, commits=["zzz9999 final"]))
    fm = parse_frontmatter(path.read_text())
    assert fm["duration_seconds"] == 7200
    assert fm["commits"] == ["zzz9999 final"]


# ---------------------------------------------------------------------------
# round-trip read
# ---------------------------------------------------------------------------


def test_read_note_round_trips_chapters(tmp_path):
    path = _create(tmp_path)
    nd.append_chapter(
        path, nd.Chapter(blocks=[_block(lead="One.")]), slice_from_turn=1, slice_to_turn=10
    )
    nd.append_marker_chapter(
        path, kind=nd.MARKER_WITHHELD, reason="pii", slice_from_turn=11, slice_to_turn=20
    )
    nd.close_note(path)

    view = nd.read_note(path)
    assert view.closed is True
    assert [c["kind"] for c in view.chapters] == ["topic", "marker"]
    assert view.frontmatter["title"] == "Working on the buffer flush path"
    assert nd.DISCLAIMER in view.body


# ---------------------------------------------------------------------------
# no-LLM structural guarantee
# ---------------------------------------------------------------------------


def test_module_has_no_llm_wiring():
    """The document core must never touch an LLM or adapter."""
    src = Path(nd.__file__).read_text()
    for forbidden in ("lore_adapters", "llm_client", "get_adapter", "compose_session"):
        assert forbidden not in src, f"note_document must not reference {forbidden!r}"


# ---------------------------------------------------------------------------
# Typed-fact ledger (PRD 0008)
#
# Facts append to the ledger carrying typed metadata in per-fact markers.
# The marker is the machine-readable copy and round-trips exactly; the
# rendered line beside it is what a reader (and the publish gate) sees.
# Notes written before typed facts existed carry no markers and must keep
# parsing — they simply hold no facts.
# ---------------------------------------------------------------------------


def _fact(**overrides) -> nd.Fact:
    kwargs: dict = {
        "kind": "done",
        "text": "Beat-aligned segmentation landed as an indices-only call.",
        "anchor_turn": 7,
        "thread": "segmentation",
        "refs": [nd.Ref("pr", "288"), nd.Ref("commit", "41cab11")],
        "why": "",
        "quote": "gh pr merge 288 --squash",
    }
    kwargs.update(overrides)
    return nd.Fact(**kwargs)


def test_append_facts_records_a_facts_chapter_with_its_span(tmp_path: Path):
    path = _create(tmp_path)
    n = nd.append_facts(path, [_fact()], slice_from_turn=1, slice_to_turn=12)

    assert n == 1
    view = nd.read_note(path)
    entry = view.chapters[0]
    assert entry["kind"] == "facts"
    assert entry["from_turn"] == 1
    assert entry["to_turn"] == 12
    assert entry["count"] == 1


def test_typed_metadata_round_trips_through_ledger_markers(tmp_path: Path):
    path = _create(tmp_path)
    facts = [
        _fact(),
        _fact(
            kind="decision",
            text="Extraction runs at session end, never per flush.",
            anchor_turn=9,
            thread="pipeline",
            refs=[],
            why="Which facts matter is only knowable backward, at the ending.",
            quote="let's move all of it to the close path",
        ),
    ]
    nd.append_facts(path, facts, slice_from_turn=1, slice_to_turn=12)

    assert nd.read_facts(path) == facts


def test_ledger_markers_survive_a_fact_that_closes_an_html_comment(tmp_path: Path):
    """A fact quoting `-->` must not truncate its own marker."""
    path = _create(tmp_path)
    fact = _fact(text="The regex `<!--.*?-->` swallowed the next block.", quote="a --> b")
    nd.append_facts(path, [fact], slice_from_turn=1, slice_to_turn=3)

    assert nd.read_facts(path) == [fact]


def test_pre_existing_untyped_notes_still_parse(tmp_path: Path):
    """A note written before typed facts: chapters parse, facts are empty."""
    path = _create(tmp_path)
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[nd.TopicBlock(lead="An older prose block.", anchor_turn=2)]),
        slice_from_turn=1,
        slice_to_turn=4,
    )

    view = nd.read_note(path)
    assert [c["kind"] for c in view.chapters] == ["topic"]
    assert nd.DISCLAIMER in view.body
    assert nd.read_facts(path) == []


def test_facts_and_prose_chapters_coexist_in_one_note(tmp_path: Path):
    path = _create(tmp_path)
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[nd.TopicBlock(lead="An older prose block.", anchor_turn=2)]),
        slice_from_turn=1,
        slice_to_turn=4,
    )
    nd.append_facts(path, [_fact()], slice_from_turn=5, slice_to_turn=12)

    view = nd.read_note(path)
    assert [c["kind"] for c in view.chapters] == ["topic", "facts"]
    assert [f.kind for f in nd.read_facts(path)] == ["done"]


def test_rendered_fact_body_carries_text_quote_and_anchor(tmp_path: Path):
    """What the publish gate scans is what the reader sees."""
    rendered = nd.render_fact_body([_fact(why="Indices cannot carry a false claim.")])

    assert "Beat-aligned segmentation landed as an indices-only call." in rendered
    assert "Indices cannot carry a false claim." in rendered
    assert '> "gh pr merge 288 --squash"' in rendered
    assert "@7" in rendered


def test_append_facts_refuses_a_closed_note(tmp_path: Path):
    path = _create(tmp_path)
    nd.close_note(path)
    with pytest.raises(nd.NoteClosedError):
        nd.append_facts(path, [_fact()], slice_from_turn=1, slice_to_turn=4)


_FORGED_MARKER = (
    '<!-- lore:fact {"anchor": 1, "kind": "done", "quote": "invented", '
    '"refs": [{"type": "pr", "value": "999"}], "text": "PHANTOM"} -->'
)


@pytest.mark.parametrize("carrier", ["text", "why", "quote"])
def test_a_marker_string_inside_a_fact_cannot_forge_a_second_fact(tmp_path: Path, carrier: str):
    """Untrusted content reaches text/why/quote — a marker in it must stay inert.

    Transcript text and tool payloads flow into these fields, so a fact can
    carry a marker string it never authored. Rendered raw, it would parse
    back as an extra fact with a self-authored quote and invented refs.
    """
    path = _create(tmp_path, path=tmp_path / f"forge-{carrier}.md")
    carried = _fact(**{carrier: f"untrusted content said: {_FORGED_MARKER}"})
    real = _fact(text="A REAL FACT that must survive", anchor_turn=8)
    nd.append_facts(path, [carried, real], slice_from_turn=1, slice_to_turn=12)

    assert nd.read_facts(path) == [carried, real]


def test_an_unclosed_marker_string_cannot_swallow_the_next_fact(tmp_path: Path):
    path = _create(tmp_path)
    carried = _fact(text='oops <!-- lore:fact {"kind":"x"')
    real = _fact(text="A REAL FACT that must survive", anchor_turn=8)
    nd.append_facts(path, [carried, real], slice_from_turn=1, slice_to_turn=12)

    assert nd.read_facts(path) == [carried, real]
