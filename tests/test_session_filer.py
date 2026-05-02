"""Tests for lore_curator.session_filer — session-note writer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
        integration="claude-code",
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


class _FakeContentBlock:
    """Minimal anthropic.types.ToolUseBlock shape for summary-merge tests."""

    def __init__(self, type_: str, input_: dict | None = None) -> None:
        self.type = type_
        self.input = input_ or {}


class _FakeResponse:
    def __init__(self, content: list[_FakeContentBlock]) -> None:
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._response


class _FakeLlmClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessagesAPI(response)


def _make_summary_merge_client(merged: str) -> _FakeLlmClient:
    """A fake LlmClient whose ``messages.create`` returns a tool_use
    block with ``{"merged": <merged>}``. Mirrors the shape
    ``summary_merge.merge_descriptions`` walks."""
    block = _FakeContentBlock(type_="tool_use", input_={"merged": merged})
    return _FakeLlmClient(_FakeResponse([block]))


def _make_noteworthy(
    title: str = "Add Ledger Feature",
    description: str = "Added an append-only ledger module for tracking curator runs.",
) -> NoteworthyResult:
    return NoteworthyResult(
        noteworthy=True,
        reason="substantive refactor",
        title=title,
        description=description,
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
    description: str | None = "Some existing session",
    body: str = "",
    year: int = 2026,
    month: int = 4,
) -> Path:
    """Helper to plant a fake session note in the YYYY/MM/ hierarchy.

    ``description=None`` simulates a legacy note that pre-dates the
    description frontmatter field — used by tests covering the
    backfill-on-append path.
    """
    if created is None:
        created = datetime.now(UTC).date().isoformat()
    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "session",
        "created": created,
        "last_reviewed": created,
        "scope": scope_str,
        "draft": True,
        "curator_a_run": datetime.now(UTC).isoformat(),
        "source_transcripts": [
            {"integration": "claude-code", "id": "old-id", "from_hash": "sha256:aaa", "to_hash": "sha256:bbb"}
        ],
        "tags": [],
    }
    if description is not None:
        fm["description"] = description
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
    # ``draft`` was vestigial on session notes (never flipped) — the revision
    # drops it. Sessions are immutable historical records, not living docs.
    assert "draft" not in fm
    assert isinstance(fm["source_transcripts"], list)
    assert len(fm["source_transcripts"]) == 1
    assert fm["created"] == "2026-04-19"
    assert fm["last_reviewed"] == "2026-04-19"


def test_file_new_session_note_has_title_and_description(tmp_path):
    """New shape: frontmatter carries `title` (slug source) AND `description`
    (1-2 sentence status-line preview). The old paragraph ``summary`` field
    is gone — its content moved into ``description``.
    """
    result = _file_note(tmp_path)
    fm = parse_frontmatter(result.path.read_text())
    assert fm["title"] == "Add Ledger Feature"
    assert fm["description"].startswith("Added an append-only ledger")
    assert "summary" not in fm


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
    assert src["integration"] == "claude-code"
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


def test_slug_word_boundary_truncates_at_dash():
    """Long titles truncate at a hyphen boundary, never mid-word.

    The old hard ``[:60]`` produced filenames like
    ``"...rebase-onto-pha"`` (cut mid-word in "Phase"). The fix walks back
    to the last hyphen that fits.
    """
    title = (
        "Ship v0.13.1 — fix #29 mid-stream curator notes "
        "(rebase onto Phase 12)"
    )
    s = _slug(title)
    assert len(s) <= 60
    assert not s.endswith("-")
    # Result must end on a complete word (next char in the source slug
    # must be a hyphen — i.e. we cut at a boundary).
    full = "ship-v0-13-1-fix-29-mid-stream-curator-notes-rebase-onto-phase-12"
    assert full.startswith(s + "-"), (
        f"slug {s!r} should be a prefix of {full!r} stopping at a hyphen"
    )


def test_slug_short_title_unchanged():
    """A short, already-clean title passes through untouched."""
    assert _slug("Add Ledger Feature") == "add-ledger-feature"


def test_slug_hard_cut_when_no_word_boundary():
    """One giant unbroken token has no boundary to walk back to — fall
    back to the hard ``[:60]`` cut so we always produce *some* slug."""
    s = _slug("a" * 80)
    assert len(s) == 60
    assert s == "a" * 60


# ---------------------------------------------------------------------------
# Work-date propagation
# ---------------------------------------------------------------------------


def _make_handle_with_mtime(mtime: datetime) -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id="transcript-abc123",
        path=Path("/tmp/transcript.jsonl"),
        cwd=Path("/tmp"),
        mtime=mtime,
    )


def test_work_time_drives_directory_and_filename(tmp_path):
    """Directory uses YYYY/MM from work_time; filename uses DD-HHMM- prefix."""
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
    # Filename now carries day + HHMM prefix so intra-day order is sortable.
    assert result.path.name.startswith("18-2230-"), (
        f"filename must use work day + HHMM, got {result.path.name}"
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
    """Second note with same day + HHMM + slug gets a -2 suffix.

    The HHMM prefix makes natural collisions much rarer (would need a
    second new-note open in the same minute with the same slug for the
    same scope — and same-scope same-day would normally merge instead).
    Here we force the collision by pre-seeding the *closed* timed file.
    """
    sessions_dir = tmp_path / "sessions" / "2026" / "04"
    sessions_dir.mkdir(parents=True)
    closed_first = sessions_dir / "19-1200-add-ledger-feature.md"
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
    """Existing today + same-scope open note -> append, no LLM call.

    Phase 2: append merges bullets into the locked sections rather than
    wrapping each chunk in its own ``## <chunk title>`` H2 — see the
    section-merge tests below for the canonical behaviour. This test
    just confirms the merge-instead-of-create decision still holds.
    """
    sessions_dir = tmp_path / "sessions"
    existing = _write_session_note(
        sessions_dir, "19-morning-work.md",
        scope_str="proj:feature", created="2026-04-19",
        body="## What we worked on\n\n- morning slice\n",
    )

    result = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Afternoon Work"),
        scope=_make_scope("proj:feature"),
    )

    assert result.was_merge is True
    assert result.path == existing
    text = existing.read_text()
    # New chunk's bullets merge into the existing What-we-worked-on section.
    assert "- morning slice" in text
    assert "- Added ledger module" in text
    # The old ``## <chunk title>`` per-append wrapper is gone.
    assert "## Afternoon Work" not in text


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
# Phase 3 — mechanical sections + auto-populated frontmatter
# ---------------------------------------------------------------------------


def _patch_collectors(monkeypatch, *, commits=None, issues=None, repo=None):
    """Stub the Phase-3 collectors at the names ``_collect_activity`` calls
    them by.

    ``_collect_activity`` lives in ``lore_curator.session_activity`` (it
    used to live in ``session_filer``; the buffer-and-flush curator
    needed it to be reachable from the heartbeat path without dragging
    the LLM merger surface). It calls ``collect_commits_by_sha`` /
    ``collect_issues_in_window`` from its own module namespace, so we
    patch them there.

    The new SHA-bound resolver takes ``shas`` instead of ``since``/``until``.
    Tests that want to assert "these specific commits show up in the note"
    pass them via ``commits=`` and the stub returns them verbatim, ignoring
    whatever SHAs the extractor emitted (which is fine — these tests
    don't construct turn fixtures, they construct note shapes).
    """
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_commits_by_sha",
        lambda *a, **kw: list(commits or []),
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window",
        lambda *a, **kw: ((issues or {}).get("opened", []), (issues or {}).get("closed", [])),
    )
    if repo is not None:
        # `current_repo` is imported lazily inside `_collect_activity`
        # from lore_core.git — patching the source module catches it.
        monkeypatch.setattr(
            "lore_core.git.current_repo",
            lambda cwd: repo,
        )


def test_phase_3_plans_frontmatter_populated_from_body_wikilinks(tmp_path, monkeypatch):
    """Body wikilinks ``[[plan/<slug>(#sN)?]]`` validated against
    wiki/<wiki>/plans/ feed the frontmatter ``plans:`` list.
    Hallucinated plan slugs (no matching plan note) are dropped."""
    wiki_root = tmp_path / "wiki" / "private"
    (wiki_root / "plans").mkdir(parents=True)
    (wiki_root / "plans" / "real-plan.md").write_text("---\ntype: plan\n---\n")

    nw = NoteworthyResult(
        noteworthy=True, reason="r",
        title="Plan work",
        description="Worked on [[plan/real-plan#s2]] and touched [[plan/ghost#s1]] which is hallucinated.",
        bullets=["did the thing"],
    )
    _patch_collectors(monkeypatch)  # no commits, no issues

    result = _file_note(
        wiki_root, noteworthy=nw, scope=_make_scope("private:lore"),
    )
    fm = parse_frontmatter(result.path.read_text())
    assert "real-plan#s2" in (fm.get("plans") or [])
    assert all("ghost" not in p for p in (fm.get("plans") or []))


def test_phase_3_projects_frontmatter_populated_from_cwd_repo(tmp_path, monkeypatch):
    """Cwd repo's project note (when present) lands in ``projects:``."""
    wiki_root = tmp_path / "wiki" / "private"
    (wiki_root / "projects").mkdir(parents=True)
    (wiki_root / "projects" / "lore.md").write_text("---\ntype: project\n---\n")

    monkeypatch.setattr(
        "lore_curator.session_activity.current_repo",
        lambda cwd: "buchbend/lore",
    )
    _patch_collectors(monkeypatch, repo="buchbend/lore")

    result = _file_note(wiki_root, scope=_make_scope("private:lore"))
    fm = parse_frontmatter(result.path.read_text())
    assert "lore" in (fm.get("projects") or [])


def test_phase_3_activity_section_renders_commits(tmp_path, monkeypatch):
    """Commits collected in the work window render under
    ``## Activity / ### Commits``."""
    from lore_curator.session_activity import CommitRef

    fake_commits = [
        CommitRef(short_hash="abc1234", subject="add ledger",
                  branch="main", repo="org/lore"),
    ]
    _patch_collectors(monkeypatch, commits=fake_commits)

    result = _file_note(tmp_path, scope=_make_scope("private:lore"))
    body = _body(result.path)
    assert "## Activity" in body
    assert "### Commits" in body
    assert "`abc1234` add ledger" in body
    # No issues → no ### Issues opened/closed subheadings.
    assert "### Issues opened" not in body
    assert "### Issues closed" not in body


def test_phase_3_activity_section_omitted_when_no_commits_or_issues(tmp_path, monkeypatch):
    """No git/gh data → no Activity parent rendered (omit-when-empty)."""
    _patch_collectors(monkeypatch)
    result = _file_note(tmp_path)
    body = _body(result.path)
    assert "## Activity" not in body


def test_phase_3_issues_closed_section_renders_referenced_only(tmp_path, monkeypatch):
    """Only issues actually mentioned in commits or turns appear under
    ``### Issues closed``. The collector path: a commit subject with
    ``closes #29`` produces a closed-reference set, gh returns the
    issue, the renderer surfaces it."""
    from lore_curator.session_activity import CommitRef

    fake_commits = [
        CommitRef(short_hash="def5678", subject="fix: closes #29",
                  branch="main", repo="org/lore"),
    ]
    fake_issues = {"closed": [{"number": 29, "title": "the bug", "state": "CLOSED"}]}
    _patch_collectors(monkeypatch, commits=fake_commits, issues=fake_issues, repo="org/lore")

    result = _file_note(tmp_path, scope=_make_scope("private:lore"))
    body = _body(result.path)
    assert "## Activity" in body
    assert "### Issues closed" in body
    assert "#29 the bug" in body


# ---------------------------------------------------------------------------
# Phase 2 — body shape (rationale-first sections, no Session: H1 prefix)
# ---------------------------------------------------------------------------


def _body(path: Path) -> str:
    """Return just the markdown body of a session note (frontmatter stripped)."""
    text = path.read_text()
    parts = text.split("---\n", 2)
    return parts[2] if len(parts) >= 3 else text


def test_body_h1_is_bare_title_no_session_prefix(tmp_path):
    """The H1 is the title verbatim — no 'Session:' prefix.

    The old form prefixed every body with ``# Session: <title>``; the
    revision drops it because ``type: session`` and the path already
    establish the kind.
    """
    result = _file_note(tmp_path, noteworthy=_make_noteworthy("Add Ledger Feature"))
    body = _body(result.path)
    assert "# Add Ledger Feature\n" in body
    assert "# Session:" not in body


def test_body_section_order_is_rationale_first(tmp_path):
    """Locked section order: Summary → Decisions made → What we worked on
    → Activity → Loose ends. Rationale-first puts Decisions ahead of
    activity bullets so a 6-month-future reader (and Curator B's prefix
    window) lands on the durable layer first.
    """
    result = _file_note(tmp_path, noteworthy=_make_noteworthy())
    body = _body(result.path)
    # Each section's offset must strictly increase down the locked order.
    summary_at = body.find("## Summary")
    decisions_at = body.find("## Decisions made")
    worked_on_at = body.find("## What we worked on")
    loose_at = body.find("## Loose ends")
    assert summary_at != -1
    assert decisions_at != -1
    assert worked_on_at != -1
    # Decisions must appear before "What we worked on".
    assert decisions_at < worked_on_at
    # Summary must come first.
    assert summary_at < decisions_at
    # Loose ends comes last (when present); _make_noteworthy doesn't set
    # any loose_ends so it's omitted — guard the assertion.
    if loose_at != -1:
        assert worked_on_at < loose_at


def test_body_summary_section_carries_description_paragraph(tmp_path):
    """## Summary is the body home of the 1-2-sentence description.

    The frontmatter carries the same string for SessionStart's cheap read;
    the body section is what humans / Curator B see when they actually
    open the note.
    """
    nw = _make_noteworthy(
        "Add Ledger Feature",
        description=(
            "Added an append-only ledger module for tracking curator runs. "
            "Decided on JSONL over SQLite for grep-ability."
        ),
    )
    result = _file_note(tmp_path, noteworthy=nw)
    body = _body(result.path)
    assert "## Summary\n" in body
    assert "append-only ledger module" in body
    assert "JSONL over SQLite" in body


def test_body_omits_loose_ends_section_when_empty(tmp_path):
    """No loose-ends bullets → no ``## Loose ends`` heading. Empty
    sections fragment scan and read as 'something is missing'."""
    nw = _make_noteworthy()  # default: loose_ends=[]
    result = _file_note(tmp_path, noteworthy=nw)
    body = _body(result.path)
    assert "## Loose ends" not in body


def test_body_renders_loose_ends_when_present(tmp_path):
    nw = NoteworthyResult(
        noteworthy=True,
        reason="r",
        title="Test",
        description="A test session.",
        bullets=["did stuff"],
        decisions=[],
        loose_ends=[
            "Sphinx-with-MyST build was untested as of this session.",
            "Auth-gating remains undecided.",
        ],
    )
    result = _file_note(tmp_path, noteworthy=nw)
    body = _body(result.path)
    assert "## Loose ends" in body
    assert "MyST build was untested" in body
    assert "Auth-gating remains undecided" in body


def test_body_omits_activity_when_empty(tmp_path):
    """Phase 2 emits empty Activity (Phase 3 populates from git/gh).
    With no commits / issues, the parent and all subheadings stay out
    of the body — no orphan ``## Activity\\n`` blocks."""
    result = _file_note(tmp_path, noteworthy=_make_noteworthy())
    body = _body(result.path)
    assert "## Activity" not in body
    assert "### Commits" not in body
    assert "### Issues opened" not in body
    assert "### Issues closed" not in body


def test_body_drops_legacy_files_touched_section(tmp_path):
    """``### Files touched`` was a duplicate of frontmatter ``files_touched``.
    Revision drops the body section."""
    result = _file_note(
        tmp_path,
        turns=_make_turns_with_files("auth.py", "auth_test.py"),
    )
    body = _body(result.path)
    assert "### Files touched" not in body
    assert "## Files touched" not in body


def test_body_drops_legacy_entities_line(tmp_path):
    """The freeform ``Entities: [[a]], [[b]]`` line is gone — the contract
    was inconsistent (mix of file basenames, branches, versions, concepts)."""
    nw = _make_noteworthy()
    # _make_noteworthy sets entities=["ledger"]; ensure it's not surfaced.
    assert nw.entities == ["ledger"]
    result = _file_note(tmp_path, noteworthy=nw)
    body = _body(result.path)
    assert "Entities:" not in body


def test_append_merges_bullets_into_existing_sections(tmp_path):
    """The canonical Phase 2 append rule: a second chunk's bullets merge
    into the existing note's ``## What we worked on`` and ``## Decisions
    made`` sections — no new ``## <chunk title>`` H2 wrapper, no nested
    duplicate ``## What we worked on`` block."""
    sessions_dir = tmp_path / "sessions"
    # Plant an open note already in the new shape.
    body_md = (
        "# Morning\n\n"
        "## Summary\nMorning narrative.\n\n"
        "## Decisions made\n- **A** chose path A\n\n"
        "## What we worked on\n- morning slice\n"
    )
    existing = _write_session_note(
        sessions_dir, "19-morning.md",
        scope_str="proj:feature", created="2026-04-19",
        body=body_md,
    )

    result = _file_note(
        tmp_path,
        noteworthy=NoteworthyResult(
            noteworthy=True, reason="r",
            title="Afternoon", description="Afternoon narrative.",
            bullets=["afternoon slice"],
            decisions=["**B** chose path B"],
        ),
        scope=_make_scope("proj:feature"),
    )

    text = existing.read_text()
    body = _body(result.path)
    assert result.path == existing
    # No nested-H2-per-chunk wrapper.
    assert "## Afternoon\n" not in body
    # Both chunks' bullets coexist under one ``## What we worked on``.
    assert body.count("## What we worked on") == 1
    assert "- morning slice" in body
    assert "- afternoon slice" in body
    # Same for Decisions made.
    assert body.count("## Decisions made") == 1
    assert "**A**" in body
    assert "**B**" in body


def test_append_without_llm_client_keeps_summary_sticky(tmp_path):
    """No-LLM fallback: when ``file_session_note`` is called without an
    ``llm_client`` (tests, dry-runs, the explicit /lore:session path),
    the writer's ``summary_merger`` is None and summary/description are
    sticky-existing. This guarantees that an automated pipeline missing
    its LLM never silently overwrites a real summary with a later
    chunk's framing.

    Bullet sections still union — that's additive, not destructive."""
    sessions_dir = tmp_path / "sessions"
    body_md = (
        "# Morning\n\n"
        "## Summary\nThe morning framing.\n\n"
        "## What we worked on\n- morning slice\n"
    )
    existing = _write_session_note(
        sessions_dir, "19-morning.md",
        scope_str="proj:feature", created="2026-04-19",
        description="The morning framing.",
        body=body_md,
    )

    _file_note(
        tmp_path,
        noteworthy=NoteworthyResult(
            noteworthy=True, reason="r",
            title="Afternoon", description="The afternoon framing.",
            bullets=["afternoon slice"],
        ),
        scope=_make_scope("proj:feature"),
    )

    body = _body(existing)
    assert "The morning framing." in body
    assert "The afternoon framing." not in body
    # Bullets still union — append is destructive only on replace-style
    # fields, not additive ones.
    assert "- afternoon slice" in body
    fm = parse_frontmatter(existing.read_text())
    assert fm["description"] == "The morning framing."


def test_append_with_llm_client_merges_summary_and_description(tmp_path):
    """Curator A path: with an ``llm_client`` + ``summary_merge_model``
    available, the writer asks the LLM to compose a merged 1-2-sentence
    summary that anchors on the existing framing and weaves in the new
    chunk's context. Both the body ``## Summary`` AND the frontmatter
    ``description`` receive the merged value.

    This is the production path — the Frankenstein-merge fix was the
    Jaccard threshold raise (single-shared-file no longer bridges); when
    a merge legitimately happens, the summary should reflect the
    combined arc rather than being clobbered by the later chunk OR
    frozen on the earlier one."""
    sessions_dir = tmp_path / "sessions"
    body_md = (
        "# Morning\n\n"
        "## Summary\nMorning: started OAuth refactor in auth.py.\n\n"
        "## What we worked on\n- morning slice\n"
    )
    existing = _write_session_note(
        sessions_dir, "19-morning.md",
        scope_str="proj:feature", created="2026-04-19",
        description="Morning: started OAuth refactor in auth.py.",
        body=body_md,
    )

    merged_text = (
        "Started the OAuth refactor in auth.py and continued through the "
        "afternoon refining the token-exchange path."
    )
    fake_client = _make_summary_merge_client(merged_text)

    file_session_note(
        scope=_make_scope("proj:feature"),
        handle=_make_handle(),
        noteworthy=NoteworthyResult(
            noteworthy=True, reason="r",
            title="Afternoon", description="Afternoon: refined token-exchange path.",
            bullets=["refined token-exchange"],
        ),
        turns=_make_turns(),
        wiki_root=tmp_path,
        now=_NOW,
        llm_client=fake_client,
        summary_merge_model="claude-sonnet-4-6",
    )

    body = _body(existing)
    assert merged_text in body, "merged summary must land in body ## Summary"
    # Original morning framing is fully replaced by the merged version
    # (which still anchors on it, per the merger's contract).
    assert body.count("Morning: started OAuth refactor in auth.py.") == 0

    fm = parse_frontmatter(existing.read_text())
    assert fm["description"] == merged_text, (
        "merged summary must also drive frontmatter description"
    )

    # Merger was actually called once.
    assert len(fake_client.messages.calls) == 1
    sent = fake_client.messages.calls[0]
    prompt = sent["messages"][0]["content"]
    assert "Morning: started OAuth refactor" in prompt, (
        "prompt must include the existing summary as anchor"
    )
    assert "Afternoon: refined token-exchange" in prompt, (
        "prompt must include the new chunk's summary"
    )


def test_append_with_llm_client_does_not_merge_when_existing_empty(tmp_path):
    """Short-circuit: when the existing note has no body Summary (legacy
    pre-Phase-2 shape) AND no frontmatter description, the merger
    short-circuits without an LLM call and the new chunk's framing
    becomes the note's summary/description. This preserves the
    description-backfill behaviour without spending an LLM call."""
    sessions_dir = tmp_path / "sessions"
    body_md = (
        "# Morning\n\n"
        "## What we worked on\n- morning slice\n"
    )
    existing = _write_session_note(
        sessions_dir, "19-morning.md",
        scope_str="proj:feature", created="2026-04-19",
        description=None,
        body=body_md,
    )

    fake_client = _make_summary_merge_client("never-called-marker")

    file_session_note(
        scope=_make_scope("proj:feature"),
        handle=_make_handle(),
        noteworthy=NoteworthyResult(
            noteworthy=True, reason="r",
            title="Afternoon", description="The afternoon framing.",
            bullets=["afternoon slice"],
        ),
        turns=_make_turns(),
        wiki_root=tmp_path,
        now=_NOW,
        llm_client=fake_client,
        summary_merge_model="claude-sonnet-4-6",
    )

    fm = parse_frontmatter(existing.read_text())
    assert fm["description"] == "The afternoon framing."
    body = _body(existing)
    assert "The afternoon framing." in body
    # No LLM call should have fired — there was nothing to merge against.
    assert len(fake_client.messages.calls) == 0


def test_append_backfills_missing_description_on_legacy_note(tmp_path):
    """A legacy note pre-dating the ``description`` field gets one on
    first append — backfill is additive, not a clobber. Exercises the
    no-LLM path (sticky fallback's backfill rule)."""
    sessions_dir = tmp_path / "sessions"
    body_md = (
        "# Morning\n\n"
        "## What we worked on\n- morning slice\n"
    )
    existing = _write_session_note(
        sessions_dir, "19-morning.md",
        scope_str="proj:feature", created="2026-04-19",
        description=None,
        body=body_md,
    )
    fm_before = parse_frontmatter(existing.read_text())
    assert "description" not in fm_before or not fm_before.get("description")

    _file_note(
        tmp_path,
        noteworthy=NoteworthyResult(
            noteworthy=True, reason="r",
            title="Afternoon", description="The afternoon framing.",
            bullets=["afternoon slice"],
        ),
        scope=_make_scope("proj:feature"),
    )

    fm_after = parse_frontmatter(existing.read_text())
    assert fm_after["description"] == "The afternoon framing."


def test_merge_body_sections_empty_new_summary_keeps_existing():
    """Defensive: an empty ``new.summary`` must not blank out the
    existing one. (At the file_session_note level si.description
    falls back to title so this can only happen when a body parses
    to a Summary-less BodySections — e.g., a legacy note shape — but
    the merge primitive must still hold the invariant.)"""
    from lore_core.session_writer import BodySections, merge_body_sections

    existing = BodySections(
        title="t", summary="kept", decisions=[], worked_on=["a"],
        loose_ends=[], commits=[], issues_opened=[], issues_closed=[],
    )
    new = BodySections(
        title="t", summary="", decisions=[], worked_on=["b"],
        loose_ends=[], commits=[], issues_opened=[], issues_closed=[],
    )
    merged = merge_body_sections(existing, new)
    assert merged.summary == "kept"


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
    # Title precedes description (it's the human's first scan target).
    assert keys_in_order.index("title") < keys_in_order.index("description")


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
        integration="claude-code",
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
        integration="claude-code", id="uuid-a",
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
        integration="claude-code", id="u-fresh",
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

    Morning: started auth refactor on auth.py + auth_test.py + helpers.py.
    Afternoon: continued auth.py + auth_test.py + utils.py.
    Two shared files of three on each side → Jaccard 2/4 = 0.5,
    clearing the 0.5 threshold. Same topic continuing."""
    morning = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor"),
        turns=_make_turns_with_files("auth.py", "auth_test.py", "helpers.py"),
    )

    afternoon = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor — More"),
        turns=_make_turns_with_files("auth.py", "auth_test.py", "utils.py"),
    )

    assert morning.path == afternoon.path
    assert afternoon.was_merge is True


def test_phase_c_single_shared_file_does_not_merge(tmp_path):
    """One incidentally-shared file is not enough to merge two
    semantically-distinct sessions on the same day.

    Real-world failure mode (2026-04-29): a GitHub-issue-curation
    session and a step_files plan session both touched
    ``lib/lore_cli/hooks.py`` and merged at Jaccard 1/3 ≈ 0.33,
    fusing two unrelated topics in one note. Threshold raised to 0.5
    so single-shared-file overlap can no longer bridge."""
    morning = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("GitHub issue curation"),
        turns=_make_turns_with_files("hooks.py", "issue_state.py"),
    )

    afternoon = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Plan: step_files automation"),
        turns=_make_turns_with_files("hooks.py", "plan_files.py"),
    )

    assert morning.path != afternoon.path, (
        "Single shared file should not bridge unrelated topics"
    )
    assert afternoon.was_merge is False


@pytest.mark.parametrize("boilerplate_file", [
    "poetry.lock", "uv.lock", "Pipfile.lock", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "package-lock.json",
    "Makefile", "Dockerfile", ".dockerignore", "tsconfig.json",
    ".python-version",
])
def test_phase_c_extended_boilerplate_does_not_force_merge(tmp_path, boilerplate_file):
    """M2: lockfiles, build configs, and CI-adjacent files are touched
    by almost every session in projects that use them. Their overlap
    must not bridge unrelated topics."""
    morning = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Auth Refactor"),
        turns=_make_turns_with_files("auth.py", boilerplate_file),
    )

    afternoon = _file_note(
        tmp_path,
        noteworthy=_make_noteworthy("Schema Migration"),
        turns=_make_turns_with_files("schema.sql", boilerplate_file),
    )

    assert morning.path != afternoon.path, (
        f"Overlap on {boilerplate_file!r} alone should not bridge "
        "unrelated topics"
    )


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


# ---------------------------------------------------------------------------
# _commit_shas_from_bash_results — per-session SHA capture (Step 1)
# ---------------------------------------------------------------------------


def _bash_pair(
    *,
    call_index: int,
    result_index: int,
    command: str,
    output: str,
    tc_id: str | None = "tc-1",
    is_error: bool = False,
) -> list[Turn]:
    """Build an [assistant tool_call, tool_result] Turn pair for a Bash call."""
    from lore_core.types import ToolCall, ToolResult

    return [
        Turn(
            index=call_index, timestamp=None, role="assistant",
            tool_call=ToolCall(
                name="Bash", input={"command": command},
                id=tc_id, category="shell_exec",
            ),
        ),
        Turn(
            index=result_index, timestamp=None, role="tool_result",
            tool_result=ToolResult(
                tool_call_id=tc_id, output=output, is_error=is_error,
            ),
        ),
    ]


def test_extractor_t_single_commit_basic():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m 'x'",
        output="[main abc1234] x\n 1 file changed, 1 insertion(+)\n",
    )
    assert _commit_shas_from_bash_results(turns) == ["abc1234"]


def test_extractor_t_root_commit():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m init",
        output="[main (root-commit) f00ba12] init\n",
    )
    assert _commit_shas_from_bash_results(turns) == ["f00ba12"]


def test_extractor_t_detached_during_rebase():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m fix",
        output="[(detached from origin/foo) abc1234] fix\n",
    )
    assert _commit_shas_from_bash_results(turns) == ["abc1234"]


def test_extractor_t_amend_after_hook():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    # Hook-amend produces two anchored SHA lines; we want the LAST.
    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m fix",
        output=(
            "[main 1111111] fix\n"
            "ruff did some autofix; amending\n"
            "[main 2222222] fix\n"
        ),
    )
    assert _commit_shas_from_bash_results(turns) == ["2222222"]


def test_extractor_t_parallel_calls_reordered_results():
    """Three Bash tool_use ids; results returned in shuffled order
    (post-Claude parallel-tool feature). SHAs must come back in
    tool_call order, not tool_result order."""
    from lore_core.types import ToolCall, ToolResult
    from lore_curator.session_filer import _commit_shas_from_bash_results

    # Calls in order id1 → id2 → id3
    calls = [
        Turn(index=0, timestamp=None, role="assistant",
             tool_call=ToolCall(name="Bash", input={"command": "git commit -m a"},
                                id="id1", category="shell_exec")),
        Turn(index=1, timestamp=None, role="assistant",
             tool_call=ToolCall(name="Bash", input={"command": "git commit -m b"},
                                id="id2", category="shell_exec")),
        Turn(index=2, timestamp=None, role="assistant",
             tool_call=ToolCall(name="Bash", input={"command": "git commit -m c"},
                                id="id3", category="shell_exec")),
    ]
    # Results returned id3 → id1 → id2.
    results = [
        Turn(index=3, timestamp=None, role="tool_result",
             tool_result=ToolResult(tool_call_id="id3", output="[main ccccccc] c")),
        Turn(index=4, timestamp=None, role="tool_result",
             tool_result=ToolResult(tool_call_id="id1", output="[main aaaaaaa] a")),
        Turn(index=5, timestamp=None, role="tool_result",
             tool_result=ToolResult(tool_call_id="id2", output="[main bbbbbbb] b")),
    ]
    assert _commit_shas_from_bash_results(calls + results) == [
        "aaaaaaa", "bbbbbbb", "ccccccc",
    ]


def test_extractor_t_failed_commit_no_sha_in_output():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m x",
        output="nothing to commit, working tree clean\n",
        is_error=True,
    )
    assert _commit_shas_from_bash_results(turns) == []


def test_extractor_t_pre_commit_hook_noise_then_real_sha():
    """Pre-commit hook prints `[ruff fixed abc1234]` (not anchored — has
    'ruff' inside). Git's real `[main def5678] msg` line at column 0 wins.
    """
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m foo",
        output=(
            "  [ruff fixed abc1234]\n"   # indented — not anchored
            "[main def5678] foo\n"        # anchored — real
            " 1 file changed\n"
        ),
    )
    assert _commit_shas_from_bash_results(turns) == ["def5678"]


def test_extractor_t_chained_commits_one_call():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m a && git commit -m b",
        output="[main aaaaaaa] a\n[main bbbbbbb] b\n",
    )
    # Spec note: extractor takes the LAST anchored match per result. A
    # `&&` chain inside one Bash call appears as one tool_result; we
    # only get the final commit SHA. Multi-commit recovery from a
    # chained call is out of scope (would require parsing per-line).
    assert _commit_shas_from_bash_results(turns) == ["bbbbbbb"]


def test_extractor_t_substring_false_positive_log_grep():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git log --grep='git commit' --oneline -5",
        output="abcdef0 some commit\n",
    )
    assert _commit_shas_from_bash_results(turns) == []


def test_extractor_t_commit_tree_plumbing():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git commit-tree -m x deadbeef",
        output="0123456789abcdef0123456789abcdef01234567\n",
    )
    assert _commit_shas_from_bash_results(turns) == []


def test_extractor_t_unpaired_call_truncated():
    """tool_call with no matching tool_result — extractor skips silently."""
    from lore_core.types import ToolCall
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = [
        Turn(index=0, timestamp=None, role="assistant",
             tool_call=ToolCall(name="Bash", input={"command": "git commit -m x"},
                                id="orphan", category="shell_exec")),
    ]
    assert _commit_shas_from_bash_results(turns) == []


def test_extractor_t_missing_tool_call_id():
    """tool_call.id is None → cannot pair with a result; documented as []."""
    from lore_core.types import ToolCall, ToolResult
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = [
        Turn(index=0, timestamp=None, role="assistant",
             tool_call=ToolCall(name="Bash", input={"command": "git commit -m x"},
                                id=None, category="shell_exec")),
        Turn(index=1, timestamp=None, role="tool_result",
             tool_result=ToolResult(tool_call_id=None,
                                    output="[main abc1234] x")),
    ]
    assert _commit_shas_from_bash_results(turns) == []


def test_extractor_t_dedup_preserves_order():
    """Two Bash calls produce the same SHA (e.g. shown twice somehow);
    only the first occurrence survives."""
    from lore_curator.session_filer import _commit_shas_from_bash_results

    pair1 = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m x",
        output="[main abc1234] x\n",
        tc_id="t1",
    )
    pair2 = _bash_pair(
        call_index=2, result_index=3,
        command="git commit -m y",
        output="[main abc1234] x\n",  # same SHA repeated
        tc_id="t2",
    )
    assert _commit_shas_from_bash_results(pair1 + pair2) == ["abc1234"]


def test_extractor_t_short_and_long_sha():
    from lore_curator.session_filer import _commit_shas_from_bash_results

    pair_short = _bash_pair(
        call_index=0, result_index=1,
        command="git commit -m a", output="[main abc1234] a\n",
        tc_id="s",
    )
    pair_long = _bash_pair(
        call_index=2, result_index=3,
        command="git commit -m b",
        output="[main 0123456789abcdef0123456789abcdef01234567] b\n",
        tc_id="l",
    )
    assert _commit_shas_from_bash_results(pair_short + pair_long) == [
        "abc1234",
        "0123456789abcdef0123456789abcdef01234567",
    ]


def test_extractor_t_pipe_or_redirect_before_git():
    """`echo x | git commit -F -` — pipeline / redirect ahead of git
    means we can't trust the next-token-is-commit heuristic; reject."""
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="echo x | git commit -F -",
        output="[main abc1234] x\n",
    )
    assert _commit_shas_from_bash_results(turns) == []


def test_extractor_t_combined_form_global_flags():
    """`git --git-dir=/repo/.git commit -m x` — shlex preserves the
    `=`-suffixed global flag as a single token; the membership check
    must treat the prefix form, not the bare-arg form, or the commit is
    silently skipped (regression for the original Step 1 implementation)."""
    from lore_curator.session_filer import _commit_shas_from_bash_results

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git --git-dir=/repo/.git commit -m x",
        output="[main 1234abc] x\n",
    )
    assert _commit_shas_from_bash_results(turns) == ["1234abc"]

    turns = _bash_pair(
        call_index=0, result_index=1,
        command="git --work-tree=/repo --git-dir=/repo/.git commit -m y",
        output="[main 5678def] y\n",
    )
    assert _commit_shas_from_bash_results(turns) == ["5678def"]


# ---------------------------------------------------------------------------
# _collect_activity — integration regression tests for bleed + gap (Step 3)
# ---------------------------------------------------------------------------


def _init_repo_for_filer(repo_root: Path) -> None:
    import subprocess as _sp
    repo_root.mkdir(parents=True, exist_ok=True)
    _sp.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    _sp.run(["git", "config", "user.email", "test@example.com"],
            cwd=repo_root, check=True)
    _sp.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    _sp.run(["git", "config", "commit.gpgsign", "false"],
            cwd=repo_root, check=True)


def _make_real_commit(repo_root: Path, *, subject: str, when: datetime,
                      filename: str = "f.txt", body: str | None = None) -> str:
    import subprocess as _sp
    (repo_root / filename).write_text(filename + "-content")
    _sp.run(["git", "add", "-A"], cwd=repo_root, check=True)
    iso = when.isoformat()
    env = {**__import__("os").environ,
           "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso}
    msg = subject if body is None else f"{subject}\n\n{body}\n"
    _sp.run(
        ["git", "commit", "-q", "-F", "-", "--no-verify"],
        cwd=repo_root, check=True, input=msg, text=True, env=env,
    )
    out = _sp.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                  capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_step3_no_bleed_across_parallel_sessions(tmp_path):
    """Two chunks A and B, same repo, overlapping turn windows.
    Only chunk A's Bash tool_call invokes ``git commit``; chunk B has
    no commit tool calls. Under time-window attribution, B's session
    note would also list X (because B's [min_ts, max_ts] covers the
    commit's committer-date). Under SHA-bound attribution, B sees
    nothing — that's the whole point of the rewrite.
    """
    from lore_core.types import ToolCall, ToolResult
    from lore_curator.session_filer import _collect_activity

    repo = tmp_path / "repo"
    _init_repo_for_filer(repo)
    commit_time = datetime(2026, 4, 29, 11, 0, tzinfo=UTC)
    full_sha = _make_real_commit(repo, subject="A's work", when=commit_time)
    short_sha = full_sha[:7]

    # Both chunks span the commit time.
    t_pre = datetime(2026, 4, 29, 10, 30, tzinfo=UTC)
    t_post = datetime(2026, 4, 29, 11, 30, tzinfo=UTC)

    # Chunk A: turns include a Bash tool_call for `git commit` whose
    # tool_result reports the SHA.
    chunk_a: list[Turn] = [
        Turn(index=0, timestamp=t_pre, role="user", text="please commit"),
        Turn(index=1, timestamp=commit_time, role="assistant",
             tool_call=ToolCall(name="Bash",
                                input={"command": "git commit -m \"A's work\""},
                                id="ta", category="shell_exec")),
        Turn(index=2, timestamp=commit_time, role="tool_result",
             tool_result=ToolResult(tool_call_id="ta",
                                    output=f"[main {short_sha}] A's work\n")),
        Turn(index=3, timestamp=t_post, role="assistant", text="done"),
    ]

    # Chunk B: turns span the same window but have NO commit tool calls.
    chunk_b: list[Turn] = [
        Turn(index=0, timestamp=t_pre, role="user", text="parallel work"),
        Turn(index=1, timestamp=t_post, role="assistant", text="ok"),
    ]

    wiki_root = tmp_path / "wiki" / "private"
    (wiki_root / "plans").mkdir(parents=True)

    a = _collect_activity(
        cwd=repo, wiki_root=wiki_root, turns=chunk_a,
        files_touched=[], body_text_for_plan_scan="",
    )
    b = _collect_activity(
        cwd=repo, wiki_root=wiki_root, turns=chunk_b,
        files_touched=[], body_text_for_plan_scan="",
    )

    a_blob = "\n".join(a["commits"])
    b_blob = "\n".join(b["commits"])
    assert short_sha in a_blob, f"A should attribute its own commit: {a_blob}"
    assert short_sha not in b_blob, (
        f"B did not run git commit but inherited A's SHA — bleed regression: {b_blob}"
    )


def test_step3_inter_chunk_gap_captured(tmp_path):
    """A commit whose committer-date falls OUTSIDE the chunk's
    (min_turn_ts, max_turn_ts) window must still be attributed when its
    SHA appears in a Bash tool_result inside the chunk. This proves
    we no longer rely on time-window filtering and protects against
    silent reintroduction of `git log --since/--until` as a fallback.
    """
    from lore_core.types import ToolCall, ToolResult
    from lore_curator.session_filer import _collect_activity

    repo = tmp_path / "repo"
    _init_repo_for_filer(repo)
    # Commit committer-date is HOURS before the chunk's earliest turn.
    commit_time = datetime(2026, 4, 29, 5, 0, tzinfo=UTC)
    full_sha = _make_real_commit(repo, subject="early ghost", when=commit_time)
    short_sha = full_sha[:7]

    chunk_window_start = datetime(2026, 4, 29, 10, 0, tzinfo=UTC)
    chunk_window_end = datetime(2026, 4, 29, 10, 30, tzinfo=UTC)

    turns: list[Turn] = [
        Turn(index=0, timestamp=chunk_window_start, role="user", text="..."),
        # Bash tool_call timestamp is INSIDE the chunk window even though
        # the actual git commit's committer-date is far outside.
        Turn(index=1, timestamp=chunk_window_start, role="assistant",
             tool_call=ToolCall(name="Bash",
                                input={"command": "git commit -m x"},
                                id="tg", category="shell_exec")),
        Turn(index=2, timestamp=chunk_window_start, role="tool_result",
             tool_result=ToolResult(tool_call_id="tg",
                                    output=f"[main {short_sha}] early ghost\n")),
        Turn(index=3, timestamp=chunk_window_end, role="assistant", text="ok"),
    ]

    wiki_root = tmp_path / "wiki" / "private"
    (wiki_root / "plans").mkdir(parents=True)
    activity = _collect_activity(
        cwd=repo, wiki_root=wiki_root, turns=turns,
        files_touched=[], body_text_for_plan_scan="",
    )
    blob = "\n".join(activity["commits"])
    assert short_sha in blob, (
        "commit SHA from tool_result lost despite being inside the chunk's "
        f"turns; resolver may still depend on time window: {blob}"
    )
