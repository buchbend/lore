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
    client = _make_new_client()
    result = _file_note(tmp_path, client=client)
    assert client.messages.calls == []
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
        anthropic_client=_make_new_client(),
        model_resolver=_resolver,
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
        anthropic_client=_make_new_client(),
        model_resolver=_resolver,
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
        anthropic_client=_make_new_client(),
        model_resolver=_resolver,
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

    result = _file_note(tmp_path, client=_make_new_client())
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
    assert "- morning slice" in text
    assert client.messages.calls == []


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

    client = _make_new_client()
    result = _file_note(
        tmp_path, client=client, scope=_make_scope("proj:feature")
    )
    assert result.path != closed_note
    assert result.was_merge is False
    assert closed_note.read_text() == closed_before


def test_filer_creates_new_note_when_no_todays_note_exists(tmp_path):
    """Empty sessions dir -> new note."""
    client = _make_new_client()
    result = _file_note(tmp_path, client=client)
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

    client = _make_new_client()
    result = _file_note(
        tmp_path, client=client, scope=_make_scope("proj:feature")
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

    client = _make_new_client()
    result = _file_note(
        tmp_path, client=client, scope=_make_scope("proj:feature")
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

    client = _make_client({"merge": "/wrong.md"})
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
