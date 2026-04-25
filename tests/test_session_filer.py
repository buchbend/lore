"""Tests for lore_curator.session_filer — session-note writer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
import pytest

from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_curator.noteworthy import NoteworthyResult
from lore_curator.session_filer import FiledNote, _slug, file_session_note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(scope_str: str = "proj:feature") -> Scope:
    return Scope(
        wiki="mywiki",
        scope=scope_str,
        backend="none",
        claude_md_path=Path("/tmp/CLAUDE.md"),
    )


def _make_handle() -> TranscriptHandle:
    return TranscriptHandle(
        host="claude-code",
        id="transcript-abc123",
        path=Path("/tmp/transcript.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )


def _make_turns() -> list[Turn]:
    return [
        Turn(index=0, timestamp=None, role="user", text="start"),
        Turn(index=1, timestamp=None, role="assistant", text="end"),
    ]


def _make_noteworthy(title: str = "Add Ledger Feature") -> NoteworthyResult:
    return NoteworthyResult(
        noteworthy=True,
        reason="substantive refactor",
        title=title,
        bullets=["Added ledger module", "Tests passing"],
        files_touched=["ledger.py"],
        entities=["ledger"],
        decisions=["Use append-only log"],
    )


_NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _file_note(
    wiki_root: Path,
    *,
    scope: Scope | None = None,
    noteworthy: NoteworthyResult | None = None,
    turns: list[Turn] | None = None,
    handle: TranscriptHandle | None = None,
    now: datetime = _NOW,
) -> FiledNote:
    return file_session_note(
        scope=scope or _make_scope(),
        handle=handle or _make_handle(),
        noteworthy=noteworthy or _make_noteworthy(),
        turns=turns or _make_turns(),
        wiki_root=wiki_root,
        now=now,
    )


def _write_session_note(
    sessions_dir: Path,
    filename: str,
    *,
    scope_str: str = "proj:feature",
    created: str | None = None,
    description: str = "Some existing session",
    body: str = "",
    year: int = 2026,
    month: int = 4,
) -> Path:
    """Helper to plant a fake session note in the YYYY/MM/ hierarchy."""
    if created is None:
        created = datetime.now(UTC).date().isoformat()
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": created,
        "last_reviewed": created,
        "description": description,
        "scope": scope_str,
        "draft": True,
        "curator_a_run": datetime.now(UTC).isoformat(),
        "source_transcripts": [
            {"host": "claude-code", "id": "old-id", "from_hash": "sha256:aaa", "to_hash": "sha256:bbb"}
        ],
        "tags": [],
    }
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{dumped}\n---\n\n{body}\n"
    month_dir = sessions_dir / str(year) / f"{month:02d}"
    month_dir.mkdir(parents=True, exist_ok=True)
    p = month_dir / filename
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# Tests — directory hierarchy (YYYY/MM/DD-slug.md)
# ---------------------------------------------------------------------------


def test_file_new_session_note_creates_in_year_month_dir(tmp_path):
    """New note lands at sessions/YYYY/MM/DD-slug.md."""
    result = _file_note(tmp_path)
    assert result.path.exists()
    assert result.path.parent.name == "04"
    assert result.path.parent.parent.name == "2026"
    assert result.path.parent.parent.parent.name == "sessions"
    assert result.path.name.startswith("19-")


def test_file_new_session_note_frontmatter(tmp_path):
    """New note has correct frontmatter."""
    result = _file_note(tmp_path)
    fm = parse_frontmatter(result.path.read_text())
    assert fm["type"] == "session"
    assert fm["scope"] == "proj:feature"
    assert fm["draft"] is True
    assert isinstance(fm["source_transcripts"], list)
    assert len(fm["source_transcripts"]) == 1
    assert fm["created"] == "2026-04-19"
    assert fm["last_reviewed"] == "2026-04-19"


def test_file_draft_true_on_new_note(tmp_path):
    """New session notes always have draft: true."""
    result = _file_note(tmp_path)
    fm = parse_frontmatter(result.path.read_text())
    assert fm["draft"] is True


def test_no_llm_merge_call(tmp_path):
    """No LLM merge judgment call — 1 transcript = 1 note."""
    result = _file_note(tmp_path)
    assert result.was_merge is False
    assert result.path.exists()


def test_source_transcripts_hashes_recorded(tmp_path):
    """New note frontmatter has source_transcripts[0] with host, id, from_hash, to_hash."""
    turns = _make_turns()
    result = _file_note(tmp_path, turns=turns)
    fm = parse_frontmatter(result.path.read_text())
    src = fm["source_transcripts"][0]
    assert src["host"] == "claude-code"
    assert src["id"] == "transcript-abc123"
    assert src["from_hash"] == turns[0].content_hash()
    assert src["to_hash"] == turns[-1].content_hash()


def test_filed_note_wikilink_uses_stem_only(tmp_path):
    """FiledNote.wikilink is [[DD-slug]] — bare stem, no path."""
    result = _file_note(tmp_path)
    expected = f"[[{result.path.stem}]]"
    assert result.wikilink == expected
    assert "2026" not in result.wikilink


def test_slug_sanitises_title():
    """Title with special chars produces clean hyphen-separated slug."""
    s = _slug("Add: Ledger! Now?")
    assert s == "add-ledger-now"
    assert "--" not in s
    assert all(c.isalnum() or c == "-" for c in s)


# ---------------------------------------------------------------------------
# Work-date propagation
# ---------------------------------------------------------------------------


def _make_handle_with_mtime(mtime: datetime) -> TranscriptHandle:
    return TranscriptHandle(
        host="claude-code",
        id="transcript-abc123",
        path=Path("/tmp/transcript.jsonl"),
        cwd=Path("/tmp"),
        mtime=mtime,
    )


def test_work_time_drives_directory_and_filename(tmp_path):
    """Directory uses YYYY/MM from work_time; filename uses DD."""
    work_time = datetime(2026, 4, 18, 22, 30, tzinfo=UTC)
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy(),
        turns=_make_turns(),
        wiki_root=tmp_path,
        now=curation_time,
        work_time=work_time,
    )
    assert result.path.parent.name == "04"
    assert result.path.parent.parent.name == "2026"
    assert result.path.name.startswith("18-"), (
        f"filename must use work day, got {result.path.name}"
    )


def test_work_time_drives_frontmatter_created_and_last_reviewed(tmp_path):
    """Frontmatter `created` and `last_reviewed` use work_time, not `now`."""
    work_time = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy(),
        turns=_make_turns(),
        wiki_root=tmp_path,
        now=curation_time,
        work_time=work_time,
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["created"] == "2026-04-15"
    assert fm["last_reviewed"] == "2026-04-15"


def test_curator_a_run_stays_curation_time_even_when_work_time_older(tmp_path):
    """`curator_a_run` records when we LOOKED, not when the work happened."""
    work_time = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy(),
        turns=_make_turns(),
        wiki_root=tmp_path,
        now=curation_time,
        work_time=work_time,
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["curator_a_run"].startswith("2026-04-19")


def test_work_time_defaults_to_now_when_not_supplied(tmp_path):
    """Backward compat: callers that don't pass work_time get today's date."""
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    result = _file_note(tmp_path, now=now)
    assert result.path.name.startswith("19-")
    fm = parse_frontmatter(result.path.read_text())
    assert fm["created"] == "2026-04-19"


def test_collision_appends_counter(tmp_path):
    """Second note with same day + slug gets a -2 suffix."""
    sessions_dir = tmp_path / "sessions" / "2026" / "04"
    sessions_dir.mkdir(parents=True)
    closed_first = sessions_dir / "19-add-ledger-feature.md"
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": "2026-04-19",
        "last_reviewed": "2026-04-19",
        "description": "first slice",
        "scope": "proj:feature",
        "closed": True,
        "draft": False,
        "source_transcripts": [],
        "tags": [],
    }
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    closed_first.write_text(f"---\n{dumped}\n---\n\nfirst\n")

    result = _file_note(tmp_path)
    assert result.path != closed_first
    assert result.path.name.endswith("-2.md")
    assert result.was_merge is False


# ---------------------------------------------------------------------------
# P3' — append-to-today's-open-note rule
# ---------------------------------------------------------------------------


def test_filer_appends_to_todays_open_note_for_same_scope(tmp_path):
    """Existing today + same-scope open note -> append, no LLM call."""
    sessions_dir = tmp_path / "sessions"
    existing = _write_session_note(
        sessions_dir, "19-morning-work.md",
        scope_str="proj:feature", created="2026-04-19",
        body="### Summary\n- morning slice",
    )

    result = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Afternoon Work"),
        scope=_make_scope("proj:feature"),
    )

    assert result.was_merge is True
    assert result.path == existing
    text = existing.read_text()
    assert "## Afternoon Work" in text
    assert "- morning slice" in text


def test_filer_creates_new_note_when_todays_note_is_closed(tmp_path):
    """closed: in frontmatter opts a note out of P3' append."""
    sessions_dir = tmp_path / "sessions" / "2026" / "04"
    sessions_dir.mkdir(parents=True)
    closed_note = sessions_dir / "19-finished.md"
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": "2026-04-19",
        "last_reviewed": "2026-04-19",
        "description": "Finished session",
        "scope": "proj:feature",
        "closed": True,
        "draft": False,
        "source_transcripts": [],
        "tags": [],
    }
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    closed_note.write_text(f"---\n{dumped}\n---\n\nbody\n")
    closed_before = closed_note.read_text()

    result = _file_note(
        tmp_path, scope=_make_scope("proj:feature")
    )
    assert result.path != closed_note
    assert result.was_merge is False
    assert closed_note.read_text() == closed_before


def test_filer_creates_new_note_when_no_todays_note_exists(tmp_path):
    """Empty sessions dir -> new note."""
    result = _file_note(tmp_path)
    assert result.was_merge is False
    assert result.path.exists()


def test_filer_creates_new_note_for_different_scope_same_day(tmp_path):
    """Same-day note for a DIFFERENT scope must not trigger append."""
    sessions_dir = tmp_path / "sessions"
    other_scope = _write_session_note(
        sessions_dir, "19-other.md",
        scope_str="other:scope", created="2026-04-19",
    )
    other_before = other_scope.read_text()

    result = _file_note(
        tmp_path, scope=_make_scope("proj:feature")
    )
    assert result.was_merge is False
    assert result.path != other_scope
    assert other_scope.read_text() == other_before


def test_find_todays_open_note_ignores_notes_from_other_dates(tmp_path):
    """Yesterday's same-scope note should not be appended to by P3'."""
    sessions_dir = tmp_path / "sessions"
    yesterday = _write_session_note(
        sessions_dir, "18-yesterday.md",
        scope_str="proj:feature", created="2026-04-18",
    )
    yesterday_before = yesterday.read_text()

    result = _file_note(
        tmp_path, scope=_make_scope("proj:feature")
    )
    assert result.path != yesterday
    assert result.was_merge is False
    assert yesterday.read_text() == yesterday_before


def test_find_todays_open_note_respects_work_time_not_now(tmp_path):
    """Work-date-backdated slice appends to that date's open note."""
    sessions_dir = tmp_path / "sessions"
    existing = _write_session_note(
        sessions_dir, "17-prior.md",
        scope_str="proj:feature", created="2026-04-17",
    )

    work_time = datetime(2026, 4, 17, 14, 0, tzinfo=UTC)
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope("proj:feature"),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy("Back-dated Slice"),
        turns=_make_turns(),
        wiki_root=tmp_path,
        now=curation_time,
        work_time=work_time,
    )

    assert result.path == existing
    assert result.was_merge is True


# ---------------------------------------------------------------------------
# P4b — transcripts: frontmatter list
# ---------------------------------------------------------------------------


def _transcripts_list(text: str) -> list[str]:
    return parse_frontmatter(text).get("transcripts") or []


def test_new_note_has_transcripts_frontmatter_with_handle_uuid(tmp_path):
    """A new note's frontmatter carries the originating UUID in `transcripts:`."""
    result = _file_note(tmp_path)
    fm = parse_frontmatter(result.path.read_text())
    assert fm.get("transcripts") == ["transcript-abc123"]


def test_new_note_places_transcripts_last_in_frontmatter(tmp_path):
    """UI ordering: human-facing fields above the machine-facing UUID list."""
    result = _file_note(tmp_path)
    text = result.path.read_text()
    fm_text = text.split("---\n", 2)[1]
    keys_in_order = [line.split(":", 1)[0] for line in fm_text.splitlines() if line and not line.startswith(" ") and not line.startswith("-")]
    assert "transcripts" in keys_in_order
    assert keys_in_order.index("transcripts") > keys_in_order.index("description")
    assert keys_in_order.index("transcripts") > keys_in_order.index("scope")


def test_append_extends_transcripts_list(tmp_path):
    """Appending a slice from a different session adds its UUID to the list."""
    sessions_dir = tmp_path / "sessions"
    existing = _write_session_note(
        sessions_dir, "19-open.md",
        scope_str="proj:feature", created="2026-04-19",
    )
    text = existing.read_text()
    fm = parse_frontmatter(text)
    fm["transcripts"] = ["uuid-prior"]
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = text.split("---\n", 2)[2] if text.count("---\n") >= 2 else ""
    existing.write_text(f"---\n{dumped}\n---\n{body}")

    new_handle = TranscriptHandle(
        host="claude-code",
        id="uuid-new",
        path=Path("/tmp/x.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )
    _file_note(tmp_path, handle=new_handle, scope=_make_scope("proj:feature"))

    assert _transcripts_list(existing.read_text()) == ["uuid-prior", "uuid-new"]


def test_append_dedupes_repeated_uuid_moving_it_to_tail(tmp_path):
    """A repeated UUID moves to the list's tail — no duplicate entries."""
    sessions_dir = tmp_path / "sessions"
    existing = _write_session_note(
        sessions_dir, "19-open.md",
        scope_str="proj:feature", created="2026-04-19",
    )
    text = existing.read_text()
    fm = parse_frontmatter(text)
    fm["transcripts"] = ["uuid-a", "uuid-b", "uuid-c"]
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = text.split("---\n", 2)[2] if text.count("---\n") >= 2 else ""
    existing.write_text(f"---\n{dumped}\n---\n{body}")

    repeat_handle = TranscriptHandle(
        host="claude-code", id="uuid-a",
        path=Path("/tmp/x.jsonl"), cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )
    _file_note(tmp_path, handle=repeat_handle, scope=_make_scope("proj:feature"))

    assert _transcripts_list(existing.read_text()) == ["uuid-b", "uuid-c", "uuid-a"]


def test_append_caps_transcripts_list_at_20_most_recent(tmp_path):
    """25 unique UUIDs -> list stays at 20 (oldest 5 dropped)."""
    sessions_dir = tmp_path / "sessions"
    existing = _write_session_note(
        sessions_dir, "19-open.md",
        scope_str="proj:feature", created="2026-04-19",
    )
    text = existing.read_text()
    fm = parse_frontmatter(text)
    fm["transcripts"] = [f"u{i:02d}" for i in range(20)]
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = text.split("---\n", 2)[2] if text.count("---\n") >= 2 else ""
    existing.write_text(f"---\n{dumped}\n---\n{body}")

    new_handle = TranscriptHandle(
        host="claude-code", id="u-fresh",
        path=Path("/tmp/x.jsonl"), cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )
    _file_note(tmp_path, handle=new_handle, scope=_make_scope("proj:feature"))

    got = _transcripts_list(existing.read_text())
    assert len(got) == 20
    assert got[-1] == "u-fresh"
    assert "u00" not in got
    assert "u01" in got


# ---------------------------------------------------------------------------
# Phase C — topic-aware merge in session_writer
# ---------------------------------------------------------------------------


def _make_turns_with_files(*paths: str) -> list[Turn]:
    """Build a Turn slice whose tool_calls touch the given file paths."""
    from lore_core.types import ToolCall

    turns: list[Turn] = [Turn(index=0, timestamp=None, role="user", text="do work")]
    for i, path in enumerate(paths):
        turns.append(Turn(
            index=1 + i, timestamp=None, role="assistant",
            tool_call=ToolCall(
                name="Edit", input={"file_path": path, "new_string": "x"},
                id=f"tc-{i}", category="file_edit",
            ),
        ))
    turns.append(Turn(index=1 + len(paths), timestamp=None,
                      role="assistant", text="done"))
    return turns


def test_phase_c_disjoint_files_create_new_note_same_day(tmp_path):
    """Same-day, same-scope, but DIFFERENT files → new note, not merge.

    Morning: auth refactor. Afternoon: schema migration. Should be two
    notes, not one Frankenstein note covering both topics."""
    morning = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor"),
        turns=_make_turns_with_files("auth.py", "auth_test.py"),
    )

    afternoon = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Schema Migration"),
        turns=_make_turns_with_files("schema.sql", "models.py"),
    )

    assert morning.path != afternoon.path, \
        "Disjoint file sets should not merge into the same note"
    assert afternoon.was_merge is False


def test_phase_c_overlapping_files_merge_same_day(tmp_path):
    """Same-day, same-scope, OVERLAPPING files → merge (continuation of work).

    Morning: started auth refactor on auth.py + auth_test.py.
    Afternoon: continued auth.py + helpers.py. Overlap on auth.py
    triggers merge — same topic continuing."""
    morning = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor"),
        turns=_make_turns_with_files("auth.py", "auth_test.py"),
    )

    afternoon = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor — More"),
        turns=_make_turns_with_files("auth.py", "helpers.py"),
    )

    assert morning.path == afternoon.path
    assert afternoon.was_merge is True


def test_phase_c_boilerplate_only_overlap_does_not_force_merge(tmp_path):
    """Boilerplate files like CLAUDE.md, pyproject.toml, README.md are
    touched by almost every session. Their overlap alone must not be
    enough to merge — otherwise everything links to everything."""
    morning = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor"),
        turns=_make_turns_with_files("auth.py", "CLAUDE.md", "pyproject.toml"),
    )

    afternoon = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Schema Migration"),
        turns=_make_turns_with_files("schema.sql", "CLAUDE.md", "pyproject.toml"),
    )

    assert morning.path != afternoon.path, \
        "Boilerplate-only overlap should not bridge unrelated topics"


def test_phase_c_files_touched_persisted_in_frontmatter(tmp_path):
    """Future merge decisions need to know what files an open note covers,
    so each note records its files_touched in the frontmatter."""
    result = _file_note(
        tmp_path,
        turns=_make_turns_with_files("auth.py", "auth_test.py"),
    )
    fm = parse_frontmatter(result.path.read_text())
    assert "files_touched" in fm
    assert set(fm["files_touched"]) == {"auth.py", "auth_test.py"}


def test_phase_c_merge_unions_files_touched(tmp_path):
    """When chunks merge into one note, the note's files_touched grows
    to be the union — so subsequent chunks compare against the full
    history, not just the latest append."""
    _file_note(
        tmp_path,
        turns=_make_turns_with_files("auth.py"),
    )
    second = _file_note(
        tmp_path,
        turns=_make_turns_with_files("auth.py", "helpers.py"),
    )
    fm = parse_frontmatter(second.path.read_text())
    assert set(fm["files_touched"]) == {"auth.py", "helpers.py"}


def test_phase_c_extracts_file_path_from_cursor_argument_shape(tmp_path):
    """H1 regression: Cursor's edit_file uses ``target_file`` for the
    path argument, not Claude Code's ``file_path``. Without checking
    multiple key names the cross-host promise breaks — Cursor users get
    files_touched=[] silently and degrade to legacy fallthrough."""
    from lore_curator.session_filer import _files_touched_from_turns
    from lore_core.types import ToolCall

    turns = [
        Turn(index=0, timestamp=None, role="user", text="edit"),
        Turn(index=1, timestamp=None, role="assistant", tool_call=ToolCall(
            name="edit_file",
            input={"target_file": "auth.py", "code_edit": "x"},
            id="tc",
            category="file_edit",
        )),
    ]
    assert _files_touched_from_turns(turns) == ["auth.py"]


def test_phase_c_extracts_file_path_from_uri_argument_shape(tmp_path):
    """Copilot-style ``uri`` argument also surfaces."""
    from lore_curator.session_filer import _files_touched_from_turns
    from lore_core.types import ToolCall

    turns = [
        Turn(index=0, timestamp=None, role="assistant", tool_call=ToolCall(
            name="applyEdit",
            input={"uri": "file:///work/a.py", "newText": "x"},
            id="tc",
            category="file_edit",
        )),
    ]
    paths = _files_touched_from_turns(turns)
    assert paths == ["file:///work/a.py"]


def test_phase_c_file_bearing_chunk_does_not_merge_into_legacy_note(tmp_path):
    """H2 regression: a Phase-C-aware chunk (with files_touched) must
    NOT merge into a legacy note (no files_touched). Otherwise on the
    upgrade day, every new chunk gets attracted to the most recent
    legacy note for that day, producing the cross-topic Frankenstein
    notes Phase C was designed to prevent.

    Talk-only chunks (no files_touched) can still merge into legacy —
    see the next test."""
    sessions_dir = tmp_path / "wiki" / "mywiki" / "sessions"
    sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    legacy = _write_session_note(
        sessions_dir, "19-legacy.md",
        scope_str="proj:feature", created="2026-04-19",
    )
    legacy_before = legacy.read_text()
    assert "files_touched" not in legacy_before  # sanity

    result = _file_note(
        tmp_path / "wiki" / "mywiki",
        turns=_make_turns_with_files("anything.py"),
    )
    assert result.path != legacy, \
        "File-bearing chunk should open a new note rather than merge into ambiguous legacy"
    assert result.was_merge is False
    assert legacy.read_text() == legacy_before


def test_phase_c_talk_only_chunk_merges_into_legacy_note(tmp_path):
    """A chunk with no tool calls has no signal to differentiate topics
    — fall through to the pre-Phase-C "most recent same-day same-scope"
    rule and merge into the legacy note."""
    sessions_dir = tmp_path / "wiki" / "mywiki" / "sessions"
    sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    legacy = _write_session_note(
        sessions_dir, "19-legacy.md",
        scope_str="proj:feature", created="2026-04-19",
    )

    talk_only_turns = [
        Turn(index=0, timestamp=None, role="user", text="just talking"),
        Turn(index=1, timestamp=None, role="assistant", text="ok"),
    ]
    result = _file_note(
        tmp_path / "wiki" / "mywiki",
        turns=talk_only_turns,
    )
    assert result.path == legacy
    assert result.was_merge is True


def test_phase_c_disjoint_legacy_notes_do_not_attract_new_file_chunks(tmp_path):
    """H2 regression: two legacy notes from earlier the same day, both
    without files_touched, must not become attractors for a new
    file-bearing chunk. The fix is that file-bearing chunks open a new
    note rather than merging into ambiguous legacy candidates (proven
    by the previous test); this verifies it holds with multiple
    candidates."""
    sessions_dir = tmp_path / "wiki" / "mywiki" / "sessions"
    sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    a = _write_session_note(
        sessions_dir, "19-topic-a.md",
        scope_str="proj:feature", created="2026-04-19",
    )
    b = _write_session_note(
        sessions_dir, "19-topic-b.md",
        scope_str="proj:feature", created="2026-04-19",
    )
    a_before = a.read_text()
    b_before = b.read_text()

    result = _file_note(
        tmp_path / "wiki" / "mywiki",
        turns=_make_turns_with_files("new_topic.py"),
    )
    assert result.path != a
    assert result.path != b
    assert result.was_merge is False
    assert a.read_text() == a_before
    assert b.read_text() == b_before
