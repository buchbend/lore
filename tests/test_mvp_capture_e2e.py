"""
End-to-end integration tests for the passive-capture MVP.

Exercises the full pipeline with mocked externals:
  - Fake claude-code adapter (no SDK needed at test time)
  - Fake Anthropic client (no network)
  - Real ledger, scope resolver, redaction, curator, session filer

These tests are the canonical proof that the plumbing connects correctly.
"""

from __future__ import annotations

import json
import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lore_core.types import Turn, TranscriptHandle
from lore_adapters.registry import _REGISTRY


# ---------------------------------------------------------------------------
# Fake Adapter
# ---------------------------------------------------------------------------


class FakeClaudeCodeAdapter:
    host = "claude-code"

    def __init__(self, handles_by_dir=None, turns_by_id=None):
        self._handles = handles_by_dir or {}
        self._turns = turns_by_id or {}

    def list_transcripts(self, directory):
        return self._handles.get(str(directory), [])

    def read_slice(self, handle, from_index=0):
        for t in self._turns.get(handle.id, []):
            if t.index >= from_index:
                yield t

    def read_slice_after_hash(self, handle, after_hash, index_hint=None):
        turns = self._turns.get(handle.id, [])
        if after_hash is None:
            yield from turns
            return
        for i, t in enumerate(turns):
            if t.content_hash() == after_hash:
                yield from turns[i + 1:]
                return
        yield from []

    def is_complete(self, handle):
        return True


# ---------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, type_, input_=None, text=None):
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessagesAPI:
    """Returns canned responses based on tool_choice name."""

    def __init__(self, responses_by_tool):
        self._responses = responses_by_tool
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool_name = kwargs.get("tool_choice", {}).get("name")
        if tool_name in self._responses:
            return self._responses[tool_name]
        # Default: classify noteworthy=True
        return _FakeResponse([
            _FakeBlock("tool_use", input_={
                "noteworthy": True, "reason": "default",
                "title": "Test Slice", "bullets": ["b1"],
                "files_touched": [], "entities": [], "decisions": [],
            })
        ])


class FakeAnthropic:
    def __init__(self, responses_by_tool=None):
        self.messages = _FakeMessagesAPI(responses_by_tool or {})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turns(n: int = 3) -> list[Turn]:
    turns = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        turns.append(Turn(index=i, timestamp=None, role=role, text=f"msg {i}"))
    return turns


_NOW = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)


def _setup_lore_root(tmp_path: Path, wiki_name: str = "private") -> tuple[Path, Path]:
    """Create lore_root with wiki/<wiki_name>/sessions/ and an attached work dir."""
    lore_root = tmp_path / "vault"
    (lore_root / "wiki" / wiki_name / "sessions").mkdir(parents=True)

    work = tmp_path / "work" / "project-a"
    work.mkdir(parents=True)
    (work / "CLAUDE.md").write_text(
        f"# Project\n\n## Lore\n\n- wiki: {wiki_name}\n- scope: projectA\n- backend: none\n"
    )
    return lore_root, work


def _make_handle(work: Path, transcript_id: str = "uuid-1") -> TranscriptHandle:
    return TranscriptHandle(
        host="claude-code",
        id=transcript_id,
        path=work / f"{transcript_id}.jsonl",
        cwd=work,
        mtime=_NOW,
    )


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from a Markdown file."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[4:end]
    return yaml.safe_load(fm_text) or {}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lore_root_with_attached_wiki(tmp_path, monkeypatch):
    """Set up tmp_path as a lore_root with wiki/private/, attached CLAUDE.md."""
    lore_root, work = _setup_lore_root(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))
    return lore_root, work


@pytest.fixture
def register_fake_claude_code(monkeypatch):
    """Swap the real claude-code adapter for a fake and clean up afterwards."""
    def _register(handles_by_dir=None, turns_by_id=None):
        fake = FakeClaudeCodeAdapter(handles_by_dir, turns_by_id)
        monkeypatch.setitem(_REGISTRY, "claude-code", fake)
        return fake
    return _register


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mvp_e2e_session_end_produces_note(
    lore_root_with_attached_wiki, register_fake_claude_code, monkeypatch
):
    """Full capture → curator pipeline creates a session note for a noteworthy transcript."""
    lore_root, work = lore_root_with_attached_wiki
    turns = _make_turns(3)
    handle = _make_handle(work)

    register_fake_claude_code(
        handles_by_dir={str(work): [handle]},
        turns_by_id={handle.id: turns},
    )

    # Step 1: Call capture (the Typer command function directly)
    from lore_cli.hooks import hook_app
    from lore_core.ledger import TranscriptLedger

    runner = CliRunner()
    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(work), "--host", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(work)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"capture failed: {result.output}"

    # Assert ledger gained an entry
    tledger = TranscriptLedger(lore_root)
    entry = tledger.get("claude-code", "uuid-1")
    assert entry is not None, "Expected ledger entry after capture"
    assert entry.host == "claude-code"
    assert entry.digested_hash is None  # not yet processed by curator

    # Step 2: Run curator
    from lore_curator.curator_a import run_curator_a

    fake_anthropic = FakeAnthropic({
        # tool_choice name used by classify_slice is "classify"
        "classify": _FakeResponse([
            _FakeBlock("tool_use", input_={
                "noteworthy": True,
                "reason": "substantial work done",
                "title": "Test Session Work",
                "bullets": ["implemented feature", "wrote tests"],
                "files_touched": ["src/main.py"],
                "entities": ["main"],
                "decisions": ["chose approach X"],
            })
        ]),
        # merge_judgment: no recent notes → short-circuits to new=True, no LLM call needed
    })

    curator_result = run_curator_a(
        lore_root=lore_root,
        anthropic_client=fake_anthropic,
        dry_run=False,
        now=_NOW,
    )

    # Assert session note was created
    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    notes = list(sessions_dir.glob("*.md"))
    assert len(notes) == 1, f"Expected 1 session note, found {len(notes)}: {notes}"

    # Parse frontmatter
    note_text = notes[0].read_text()
    fm = _parse_frontmatter(note_text)

    assert fm.get("draft") is True, f"Expected draft:true, got {fm.get('draft')}"
    assert fm.get("type") == "session", f"Expected type:session, got {fm.get('type')}"
    assert fm.get("scope") == "projectA", f"Expected scope:projectA, got {fm.get('scope')}"

    src_transcripts = fm.get("source_transcripts", [])
    assert len(src_transcripts) >= 1, "Expected at least one source_transcript"
    src = src_transcripts[0]
    assert src.get("host") == "claude-code", f"Expected host=claude-code, got {src.get('host')}"
    assert src.get("from_hash") == turns[0].content_hash(), (
        f"Expected from_hash={turns[0].content_hash()}, got {src.get('from_hash')}"
    )
    assert src.get("to_hash") == turns[-1].content_hash(), (
        f"Expected to_hash={turns[-1].content_hash()}, got {src.get('to_hash')}"
    )


def test_mvp_e2e_non_noteworthy_slice_produces_no_note(
    lore_root_with_attached_wiki, register_fake_claude_code, monkeypatch
):
    """Non-noteworthy classification: no session note, but ledger is advanced."""
    lore_root, work = lore_root_with_attached_wiki
    turns = _make_turns(3)
    handle = _make_handle(work)

    register_fake_claude_code(
        handles_by_dir={str(work): [handle]},
        turns_by_id={handle.id: turns},
    )

    from lore_cli.hooks import hook_app
    from lore_core.ledger import TranscriptLedger

    runner = CliRunner()
    runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(work), "--host", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(work)},
        catch_exceptions=False,
    )

    from lore_curator.curator_a import run_curator_a

    # Return noteworthy=False from the classify call (tool_choice name is "classify")
    fake_anthropic = FakeAnthropic({
        "classify": _FakeResponse([
            _FakeBlock("tool_use", input_={
                "noteworthy": False,
                "reason": "trivial",
                "title": "X",
                "bullets": [],
                "files_touched": [],
                "entities": [],
                "decisions": [],
            })
        ]),
    })

    curator_result = run_curator_a(
        lore_root=lore_root,
        anthropic_client=fake_anthropic,
        dry_run=False,
        now=_NOW,
    )

    # No session notes
    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    notes = list(sessions_dir.glob("*.md"))
    assert len(notes) == 0, f"Expected no session notes, found {len(notes)}: {notes}"

    # Ledger was advanced (digested_hash is now set)
    tledger = TranscriptLedger(lore_root)
    entry = tledger.get("claude-code", "uuid-1")
    assert entry is not None
    assert entry.digested_hash is not None, "Expected ledger to advance (digested_hash set)"
    assert entry.digested_hash == turns[-1].content_hash()


def test_mvp_e2e_idempotent_on_rerun(
    lore_root_with_attached_wiki, register_fake_claude_code, monkeypatch
):
    """Running curator twice on the same state produces exactly one session note (no duplicate)."""
    lore_root, work = lore_root_with_attached_wiki
    turns = _make_turns(3)
    handle = _make_handle(work)

    register_fake_claude_code(
        handles_by_dir={str(work): [handle]},
        turns_by_id={handle.id: turns},
    )

    from lore_cli.hooks import hook_app
    from lore_core.ledger import TranscriptLedger
    from lore_curator.curator_a import run_curator_a

    runner = CliRunner()
    runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(work), "--host", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(work)},
        catch_exceptions=False,
    )

    fake_anthropic = FakeAnthropic()  # default: noteworthy=True

    # First curator run — creates note
    result1 = run_curator_a(
        lore_root=lore_root,
        anthropic_client=fake_anthropic,
        dry_run=False,
        now=_NOW,
    )

    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    notes_after_first = list(sessions_dir.glob("*.md"))
    assert len(notes_after_first) == 1, f"Expected 1 note after first run, got {len(notes_after_first)}"

    # Second curator run — same ledger state; entry no longer pending because
    # digested_hash is set and curator_a_run is None (pending() condition 2 requires
    # curator_a_run is not None). So transcripts_considered == 0.
    result2 = run_curator_a(
        lore_root=lore_root,
        anthropic_client=fake_anthropic,
        dry_run=False,
        now=_NOW,
    )

    notes_after_second = list(sessions_dir.glob("*.md"))
    assert len(notes_after_second) == 1, (
        f"Expected exactly 1 note after second run, got {len(notes_after_second)}: {notes_after_second}"
    )


def test_mvp_e2e_unattached_cwd_produces_nothing(
    tmp_path, monkeypatch
):
    """Capture on an unattached cwd: no ledger file, no session notes."""
    lore_root = tmp_path / "vault"
    (lore_root / "wiki" / "private" / "sessions").mkdir(parents=True)

    # Work dir WITHOUT attached CLAUDE.md
    unattached = tmp_path / "work" / "no-lore"
    unattached.mkdir(parents=True)
    # No CLAUDE.md with ## Lore section

    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(unattached))

    from lore_cli.hooks import hook_app

    runner = CliRunner()
    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(unattached), "--host", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(unattached)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"capture unexpectedly failed: {result.output}"

    # No ledger file
    ledger_path = lore_root / ".lore" / "transcript-ledger.json"
    assert not ledger_path.exists(), "Expected no ledger file for unattached cwd"

    # No session notes
    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    notes = list(sessions_dir.glob("*.md"))
    assert len(notes) == 0, f"Expected no session notes for unattached cwd, found {notes}"


def test_mvp_e2e_manual_send_via_cli(tmp_path, monkeypatch):
    """lore ingest writes a JSONL transcript; ledger has one entry with host=manual-send."""
    lore_root, work = _setup_lore_root(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))

    # Write a minimal 2-line JSONL transcript
    jsonl_content = "\n".join([
        json.dumps({"index": 0, "role": "user", "text": "hello from cursor"}),
        json.dumps({"index": 1, "role": "assistant", "text": "hi there"}),
    ])
    transcript_file = tmp_path / "cursor_transcript.jsonl"
    transcript_file.write_text(jsonl_content)

    from lore_cli.__main__ import app
    from lore_core.ledger import TranscriptLedger

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        app,
        [
            "ingest",
            "--from", str(transcript_file),
            "--host", "cursor",
            "--directory", str(work),
        ],
        env={"LORE_ROOT": str(lore_root)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"ingest failed:\nstdout: {result.output}\nstderr: {getattr(result, 'stderr', '')}"

    # Verify ledger has one entry with host=manual-send
    tledger = TranscriptLedger(lore_root)
    all_entries = list(tledger._load().values())
    assert len(all_entries) == 1, f"Expected 1 ledger entry, got {len(all_entries)}"
    assert all_entries[0]["host"] == "manual-send", (
        f"Expected host=manual-send, got {all_entries[0]['host']}"
    )
