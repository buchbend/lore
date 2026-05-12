"""
End-to-end integration tests proving SubprocessClient composes with Curator A.

These tests use a fake subprocess runner (no real `claude` binary), confirming
that the translation layer (SubprocessClient → ToolUseBlock/LlmResponse) wires
correctly through classify_slice → session_filer → ledger advance.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from lore_core.types import Turn, TranscriptHandle
from lore_adapters.registry import _REGISTRY


# ---------------------------------------------------------------------------
# Shared fixtures / helpers (mirrors test_mvp_capture_e2e.py idioms)
# ---------------------------------------------------------------------------


class FakeClaudeCodeAdapter:
    integration = "claude-code"

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


def _make_turns(n: int = 3) -> list[Turn]:
    turns = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        turns.append(Turn(index=i, timestamp=None, role=role, text=f"msg {i}"))
    return turns


_NOW = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)


def _setup_lore_root(tmp_path: Path, wiki_name: str = "private") -> tuple[Path, Path]:
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root = tmp_path / "vault"
    wiki_dir = lore_root / "wiki" / wiki_name
    (wiki_dir / "sessions").mkdir(parents=True)
    # Per-wiki gate is turns-OR-age. Subprocess test seeds one pending
    # transcript without running sync; force-fire via the age fallback.
    (wiki_dir / ".lore-wiki.yml").write_text(
        "curator:\n  threshold_pending_turns: 1\n  max_pending_age_s: 0\n"
    )
    (lore_root / ".lore").mkdir(parents=True, exist_ok=True)

    work = tmp_path / "work" / "project-a"
    work.mkdir(parents=True)

    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=work, wiki=wiki_name, scope="projectA",
        attached_at=_NOW, source="manual",
    ))
    af.save()
    return lore_root, work


def _make_handle(work: Path, transcript_id: str = "uuid-1") -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id=transcript_id,
        path=work / f"{transcript_id}.jsonl",
        cwd=work,
        mtime=_NOW,
    )


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[4:end]
    return yaml.safe_load(fm_text) or {}


@pytest.fixture
def lore_root_with_attached_wiki(tmp_path, monkeypatch):
    lore_root, work = _setup_lore_root(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))
    return lore_root, work


@pytest.fixture
def register_fake_claude_code(monkeypatch):
    def _register(handles_by_dir=None, turns_by_id=None):
        fake = FakeClaudeCodeAdapter(handles_by_dir, turns_by_id)
        monkeypatch.setitem(_REGISTRY, "claude-code", fake)
        return fake
    return _register


# ---------------------------------------------------------------------------
# Canned payloads
# ---------------------------------------------------------------------------

_CLASSIFY_PAYLOAD = {
    "is_error": False,
    "api_error_status": None,
    "structured_output": {
        "noteworthy": True,
        "reason": "real work",
        "title": "Refactor the thing",
        "bullets": ["touched X", "shipped Y"],
        "files_touched": ["x.py"],
        "entities": [],
        "decisions": [],
    },
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
    "total_cost_usd": 0.001,
    "model": "claude-haiku-4-5-20251001",
    "stop_reason": "end_turn",
}

_MERGE_PAYLOAD = {
    "is_error": False,
    "api_error_status": None,
    "structured_output": {"new": True},
    "usage": {
        "input_tokens": 30,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
    "total_cost_usd": 0.0001,
    "model": "claude-haiku-4-5-20251001",
    "stop_reason": "end_turn",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_subprocess_backend_binary_missing_path(
    lore_root_with_attached_wiki, register_fake_claude_code
):
    """When no backend is available (binary absent + no API key), curator skips AI
    classification — no session note is created, but the run completes cleanly."""
    lore_root, work = lore_root_with_attached_wiki
    turns = _make_turns(3)
    handle = _make_handle(work)

    register_fake_claude_code(
        handles_by_dir={str(work): [handle]},
        turns_by_id={handle.id: turns},
    )

    # Capture so there is a pending ledger entry.
    from lore_cli.hooks import hook_app
    from typer.testing import CliRunner as TyperCliRunner

    cli_runner = TyperCliRunner()
    result = cli_runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(work), "--integration", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(work)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"capture failed: {result.output}"

    # Run curator with no LLM client (mirrors the no-backend situation).
    from lore_curator.session_curator import run_curator_a

    curator_result = run_curator_a(
        lore_root=lore_root,
        llm_client=None,   # no backend
        dry_run=False,
        now=_NOW,
    )

    # No session note should have been created.
    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    notes = list(sessions_dir.rglob("*.md"))
    assert len(notes) == 0, f"Expected no session notes when no backend, found: {notes}"

    # The transcript was considered but skipped due to missing client.
    assert curator_result.transcripts_considered >= 1
    assert "no_llm_client" in curator_result.skipped_reasons, (
        f"Expected 'no_llm_client' skip reason, got {curator_result.skipped_reasons}"
    )
