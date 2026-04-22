"""Tests for lore_curator.session_filer — session-note writer / merger."""
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
# Fake Anthropic client (same pattern as test_noteworthy.py)
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    def __init__(self, type_, input_=None, text=None):
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessagesAPI(response)


def _make_client(data: dict) -> _FakeAnthropicClient:
    block = _FakeContentBlock(type_="tool_use", input_=data)
    return _FakeAnthropicClient(_FakeResponse([block]))


def _make_new_client() -> _FakeAnthropicClient:
    """Client that returns {'new': True} — no merge."""
    return _make_client({"new": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolver(tier: str) -> str:
    return {"middle": "claude-sonnet-4-6", "simple": "claude-haiku-4-5"}[tier]


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
    client=None,
    now: datetime = _NOW,
) -> FiledNote:
    return file_session_note(
        scope=scope or _make_scope(),
        handle=handle or _make_handle(),
        noteworthy=noteworthy or _make_noteworthy(),
        turns=turns or _make_turns(),
        wiki_root=wiki_root,
        anthropic_client=client or _make_new_client(),
        model_resolver=_resolver,
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
) -> Path:
    """Helper to plant a fake session note for merge tests."""
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
    p = sessions_dir / filename
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_file_new_session_note_creates_file_with_frontmatter(tmp_path):
    """New note is created at sessions/YYYY-MM-DD-<slug>.md with correct frontmatter."""
    result = _file_note(tmp_path)
    assert result.path.exists()
    assert result.path.parent.name == "sessions"
    assert result.path.name.startswith("2026-04-19-")
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


def test_merge_judgment_returns_new_when_no_recent_notes(tmp_path):
    """Empty sessions dir → no LLM call; new note created."""
    client = _make_new_client()
    result = _file_note(tmp_path, client=client)
    assert client.messages.calls == []
    assert result.was_merge is False
    assert result.path.exists()


def test_merge_judgment_merges_into_recent_continuation(tmp_path):
    """Fake client returns merge decision → existing note has appended section; was_merge=True."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    existing = _write_session_note(sessions_dir, "2026-04-19-old-session.md")

    client = _make_client({"merge": str(existing)})
    new_nw = _make_noteworthy("New Feature Addition")
    result = _file_note(tmp_path, client=client, noteworthy=new_nw)

    assert result.was_merge is True
    assert result.path == existing
    text = existing.read_text()
    assert "## New Feature Addition" in text


def test_merge_appends_section_and_bumps_mtime(tmp_path):
    """Appended note has new ## section; last_reviewed updated to today."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    existing = _write_session_note(
        sessions_dir, "2026-04-18-old.md",
        created="2026-04-18",
        body="### Summary\n- old bullet",
    )

    client = _make_client({"merge": str(existing)})
    new_nw = _make_noteworthy("Merged Session Title")
    _file_note(tmp_path, client=client, noteworthy=new_nw, now=_NOW)

    text = existing.read_text()
    assert "## Merged Session Title" in text
    fm = parse_frontmatter(text)
    assert fm["last_reviewed"] == "2026-04-19"


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


def test_filed_note_wikilink(tmp_path):
    """FiledNote.wikilink is [[<stem>]] of the created path."""
    result = _file_note(tmp_path)
    expected = f"[[{result.path.stem}]]"
    assert result.wikilink == expected


def test_slug_sanitises_title():
    """Title with special chars produces clean hyphen-separated slug."""
    s = _slug("Add: Ledger! Now?")
    assert s == "add-ledger-now"
    # No repeated hyphens, no special chars
    assert "--" not in s
    assert all(c.isalnum() or c == "-" for c in s)


# ---------------------------------------------------------------------------
# Phase 1 — work-date propagation
#
# Symptom: if the ledger wasn't kept current and curator ran "catch-up",
# every backlogged transcript was filed under today's date. The user's
# session notes claimed all prior work happened today. Fix: each note
# takes its date from the transcript it came from, not from curation time.
# ---------------------------------------------------------------------------


def _make_handle_with_mtime(mtime: datetime) -> TranscriptHandle:
    return TranscriptHandle(
        host="claude-code",
        id="transcript-abc123",
        path=Path("/tmp/transcript.jsonl"),
        cwd=Path("/tmp"),
        mtime=mtime,
    )


def test_work_time_drives_filename_date(tmp_path):
    """Filename's YYYY-MM-DD comes from work_time, not curation `now`."""
    work_time = datetime(2026, 4, 18, 22, 30, tzinfo=UTC)  # prior day
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy(),
        turns=_make_turns(),
        wiki_root=tmp_path,
        anthropic_client=_make_new_client(),
        model_resolver=_resolver,
        now=curation_time,
        work_time=work_time,
    )
    assert result.path.name.startswith("2026-04-18-"), (
        f"filename must use work date, got {result.path.name}"
    )


def test_work_time_drives_frontmatter_created_and_last_reviewed(tmp_path):
    """Frontmatter `created` and `last_reviewed` use work_time, not `now`."""
    work_time = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)  # 4 days ago
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy(),
        turns=_make_turns(),
        wiki_root=tmp_path,
        anthropic_client=_make_new_client(),
        model_resolver=_resolver,
        now=curation_time,
        work_time=work_time,
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["created"] == "2026-04-15"
    assert fm["last_reviewed"] == "2026-04-15"


def test_curator_a_run_stays_curation_time_even_when_work_time_older(tmp_path):
    """`curator_a_run` is an audit field — records when we LOOKED, not
    when the work happened. Keeps curation timestamp."""
    work_time = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    result = file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy(),
        turns=_make_turns(),
        wiki_root=tmp_path,
        anthropic_client=_make_new_client(),
        model_resolver=_resolver,
        now=curation_time,
        work_time=work_time,
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["curator_a_run"].startswith("2026-04-19"), (
        f"curator_a_run must record curation time, got {fm['curator_a_run']}"
    )


def test_work_time_defaults_to_now_when_not_supplied(tmp_path):
    """Backward compat: callers that don't pass work_time get today's date
    (matches old behavior; preserves legacy tests)."""
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    result = _file_note(tmp_path, now=now)  # no work_time passed
    assert result.path.name.startswith("2026-04-19-")
    fm = parse_frontmatter(result.path.read_text())
    assert fm["created"] == "2026-04-19"


def test_merge_last_reviewed_uses_newest_work_time(tmp_path):
    """On merge, last_reviewed advances to the new slice's work_time —
    which may be earlier or later than the existing created date.
    Semantically: the note's "last touched" matches the newest work it
    contains, not the curation run."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    existing = _write_session_note(
        sessions_dir, "2026-04-15-old.md",
        created="2026-04-15",
        body="### Summary\n- old bullet",
    )

    work_time = datetime(2026, 4, 17, 14, 0, tzinfo=UTC)  # between created and now
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    client = _make_client({"merge": str(existing)})
    file_session_note(
        scope=_make_scope(),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy("Merged Work"),
        turns=_make_turns(),
        wiki_root=tmp_path,
        anthropic_client=client,
        model_resolver=_resolver,
        now=curation_time,
        work_time=work_time,
    )
    fm = parse_frontmatter(existing.read_text())
    assert fm["last_reviewed"] == "2026-04-17", fm
    # `created` is never rewritten on merge.
    assert fm["created"] == "2026-04-15"


def test_collision_appends_counter(tmp_path):
    """Second note with same day + slug gets a -2 suffix.

    Post-P3', same-day same-scope slices append instead of colliding.
    The collision-counter path now only fires when today's note is closed
    (frontmatter ``closed: true``) and a new note must land on the same
    filename.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    # Plant a closed first note that shares the expected slug.
    closed_first = sessions_dir / "2026-04-19-add-ledger-feature.md"
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

    result = _file_note(tmp_path, client=_make_new_client())
    assert result.path != closed_first
    assert result.path.name.endswith("-2.md")
    assert result.was_merge is False


def test_recent_notes_filter_excludes_wrong_scope(tmp_path):
    """Only notes with matching scope are passed to merge judgment.

    Uses older notes (not today's) so P3''s append-to-today fast path
    doesn't short-circuit the LLM merge judgment this test is about.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    # Yesterday — avoids P3' same-day append path.
    right = _write_session_note(
        sessions_dir, "2026-04-18-right.md",
        scope_str="proj:feature", created="2026-04-18",
    )
    _write_session_note(
        sessions_dir, "2026-04-18-wrong.md",
        scope_str="other:scope", created="2026-04-18",
    )

    seen_prompts = []

    class RecordingClient:
        class messages:
            calls = []

            @staticmethod
            def create(**kwargs):
                seen_prompts.append(kwargs["messages"][0]["content"])
                block = _FakeContentBlock(type_="tool_use", input_={"new": True})
                RecordingClient.messages.calls.append(kwargs)
                return _FakeResponse([block])

    _file_note(tmp_path, client=RecordingClient(), scope=_make_scope("proj:feature"))

    # One LLM call made (there was 1 recent matching note from yesterday).
    assert len(RecordingClient.messages.calls) == 1
    prompt = seen_prompts[0]
    assert str(right) in prompt
    assert "wrong.md" not in prompt


def test_recent_notes_filter_excludes_old(tmp_path):
    """Notes with created date older than 7 days are filtered out → no LLM call."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    old_date = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()
    _write_session_note(sessions_dir, "old-note.md", created=old_date)

    client = _make_new_client()
    _file_note(tmp_path, client=client)
    # Old note excluded → no recent notes → no LLM call
    assert client.messages.calls == []


def test_merge_into_existing_updates_source_transcripts_list(tmp_path):
    """Merging into a note with [A] produces [A, B], not just [B]."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    existing = _write_session_note(sessions_dir, "2026-04-19-existing.md")

    # Verify initial state has exactly 1 source transcript
    fm_before = parse_frontmatter(existing.read_text())
    assert len(fm_before["source_transcripts"]) == 1

    client = _make_client({"merge": str(existing)})
    _file_note(tmp_path, client=client)

    fm_after = parse_frontmatter(existing.read_text())
    assert len(fm_after["source_transcripts"]) == 2
    # Old entry still present
    assert fm_after["source_transcripts"][0]["id"] == "old-id"
    # New entry added
    assert fm_after["source_transcripts"][1]["id"] == "transcript-abc123"


# ---------------------------------------------------------------------------
# P3' — append-to-today's-open-note rule
#
# Symptom: arbitrary pending-count thresholds made the curator fire at
# points that rarely aligned with narrative boundaries, producing multiple
# stranded same-day notes that each captured a fragment. Fix: if there's
# already an open session note for the transcript's work date in this
# scope, append to it — no LLM merge judgment required. Cross-day
# continuations still go through the existing LLM path.
# ---------------------------------------------------------------------------


def test_filer_appends_to_todays_open_note_for_same_scope(tmp_path):
    """Existing today + same-scope open note → append, no LLM call."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    existing = _write_session_note(
        sessions_dir, "2026-04-19-morning-work.md",
        scope_str="proj:feature", created="2026-04-19",
        body="### Summary\n- morning slice",
    )

    # Client would never be called — but give it a clearly-wrong response
    # so any regression that hits the LLM path fails loudly instead of
    # silently going through.
    client = _make_client({"merge": "/nonexistent.md"})
    result = _file_note(
        tmp_path,
        client=client,
        noteworthy=_make_noteworthy("Afternoon Work"),
        scope=_make_scope("proj:feature"),
    )

    assert result.was_merge is True
    assert result.path == existing
    text = existing.read_text()
    assert "## Afternoon Work" in text
    assert "- morning slice" in text  # original body preserved
    assert client.messages.calls == [], "P3' must not call the LLM when today's note exists"


def test_filer_creates_new_note_when_todays_note_is_closed(tmp_path):
    """closed: in frontmatter opts a note out of P3' append."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    # Plant a closed today's note
    closed_note = sessions_dir / "2026-04-19-finished.md"
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
    closed_body_before = "body"
    closed_note.write_text(f"---\n{dumped}\n---\n\n{closed_body_before}\n")
    closed_before = closed_note.read_text()

    client = _make_new_client()
    result = _file_note(
        tmp_path, client=client, scope=_make_scope("proj:feature")
    )
    # New note created — original closed note untouched.
    assert result.path != closed_note
    assert result.was_merge is False
    assert closed_note.read_text() == closed_before


def test_filer_creates_new_note_when_no_todays_note_exists(tmp_path):
    """Empty sessions dir → new note, same as legacy behavior."""
    client = _make_new_client()
    result = _file_note(tmp_path, client=client)
    assert result.was_merge is False
    assert result.path.exists()


def test_filer_creates_new_note_for_different_scope_same_day(tmp_path):
    """Same-day note for a DIFFERENT scope must not trigger append."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    other_scope = _write_session_note(
        sessions_dir, "2026-04-19-other.md",
        scope_str="other:scope", created="2026-04-19",
    )
    other_before = other_scope.read_text()

    client = _make_new_client()
    result = _file_note(
        tmp_path, client=client, scope=_make_scope("proj:feature")
    )
    # New note created for proj:feature; other-scope note untouched.
    assert result.was_merge is False
    assert result.path != other_scope
    assert other_scope.read_text() == other_before


def test_find_todays_open_note_ignores_notes_from_other_dates(tmp_path):
    """Yesterday's same-scope note should not be appended to by P3'."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    yesterday = _write_session_note(
        sessions_dir, "2026-04-18-yesterday.md",
        scope_str="proj:feature", created="2026-04-18",
    )
    yesterday_before = yesterday.read_text()

    # LLM merge judgment says "new"
    client = _make_new_client()
    result = _file_note(
        tmp_path, client=client, scope=_make_scope("proj:feature")
    )
    # New note for today; yesterday's note unchanged.
    assert result.path != yesterday
    assert result.was_merge is False
    assert yesterday.read_text() == yesterday_before


def test_find_todays_open_note_respects_work_time_not_now(tmp_path):
    """Work-date-backdated slice appends to that date's open note.

    Preserves the Phase 1 work-date invariant: a backlogged transcript
    from 2026-04-17 merges into 2026-04-17's open note, even if curation
    is running on 2026-04-19.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    existing = _write_session_note(
        sessions_dir, "2026-04-17-prior.md",
        scope_str="proj:feature", created="2026-04-17",
    )

    work_time = datetime(2026, 4, 17, 14, 0, tzinfo=UTC)
    curation_time = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    client = _make_client({"merge": "/wrong.md"})  # must NOT be called
    result = file_session_note(
        scope=_make_scope("proj:feature"),
        handle=_make_handle_with_mtime(work_time),
        noteworthy=_make_noteworthy("Back-dated Slice"),
        turns=_make_turns(),
        wiki_root=tmp_path,
        anthropic_client=client,
        model_resolver=_resolver,
        now=curation_time,
        work_time=work_time,
    )

    assert result.path == existing
    assert result.was_merge is True
    assert client.messages.calls == []

