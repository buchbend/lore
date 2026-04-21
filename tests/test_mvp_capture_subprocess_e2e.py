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


def _make_turns(n: int = 3) -> list[Turn]:
    turns = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        turns.append(Turn(index=i, timestamp=None, role=role, text=f"msg {i}"))
    return turns


_NOW = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)


def _setup_lore_root(tmp_path: Path, wiki_name: str = "private") -> tuple[Path, Path]:
    lore_root = tmp_path / "vault"
    wiki_dir = lore_root / "wiki" / wiki_name
    (wiki_dir / "sessions").mkdir(parents=True)
    # P2: per-wiki threshold gate. Tests seed one pending transcript,
    # so lower the threshold to 1.
    (wiki_dir / ".lore-wiki.yml").write_text("curator:\n  threshold_pending: 1\n")

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


def test_e2e_subprocess_backend_produces_session_note(
    lore_root_with_attached_wiki, register_fake_claude_code
):
    """Full Curator A run using SubprocessClient + fake runner creates a session note."""
    lore_root, work = lore_root_with_attached_wiki
    turns = _make_turns(3)
    handle = _make_handle(work)

    register_fake_claude_code(
        handles_by_dir={str(work): [handle]},
        turns_by_id={handle.id: turns},
    )

    # Capture the transcript into the ledger.
    from lore_cli.hooks import hook_app
    from lore_core.ledger import TranscriptLedger
    from typer.testing import CliRunner as TyperCliRunner

    cli_runner = TyperCliRunner()
    result = cli_runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(work), "--host", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(work)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"capture failed: {result.output}"

    # Pre-populate a recent session note so that _merge_judgment makes an LLM call
    # (rather than short-circuiting on empty recent_notes). This forces both
    # classify and merge_judgment to go through the fake_runner.
    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    recent_note = sessions_dir / "2026-04-17-prior-work.md"
    recent_note.write_text(
        "---\n"
        "schema_version: 2\n"
        "type: session\n"
        "created: '2026-04-17'\n"
        "scope: projectA\n"
        "draft: true\n"
        "---\n\n"
        "Prior work session note.\n"
    )

    seen_schemas: list[set] = []

    def fake_runner(cmd, **kwargs):
        # Identify which tool is being invoked by the --json-schema payload.
        schema_idx = cmd.index("--json-schema")
        schema = json.loads(cmd[schema_idx + 1])
        keys = set(schema.get("properties", {}).keys())
        seen_schemas.append(keys)
        if "noteworthy" in keys:
            payload = _CLASSIFY_PAYLOAD
        elif "merge" in keys or "new" in keys:
            payload = _MERGE_PAYLOAD
        else:
            raise AssertionError(f"unexpected schema keys: {keys}")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=json.dumps(payload), stderr=""
        )

    from lore_curator.llm_client import SubprocessClient
    fake_client = SubprocessClient(runner=fake_runner)

    from lore_curator.curator_a import run_curator_a

    curator_result = run_curator_a(
        lore_root=lore_root,
        anthropic_client=fake_client,
        dry_run=False,
        now=_NOW,
    )

    # (a) Session note was created.
    notes = list(sessions_dir.glob("*.md"))
    # Filter out the pre-populated note; look for the new curator-created note.
    new_notes = [n for n in notes if n.name != "2026-04-17-prior-work.md"]
    assert len(new_notes) == 1, f"Expected 1 new session note, found: {new_notes}"

    note_text = new_notes[0].read_text()
    fm = _parse_frontmatter(note_text)

    # (b) Frontmatter has draft: true and the classify-supplied title.
    assert fm.get("draft") is True, f"Expected draft:true, got {fm.get('draft')}"
    assert fm.get("description") == "Refactor the thing", (
        f"Expected title from classify, got {fm.get('description')!r}"
    )
    assert fm.get("type") == "session"
    assert fm.get("scope") == "projectA"

    # (c) Ledger watermark advanced.
    tledger = TranscriptLedger(lore_root)
    entry = tledger.get("claude-code", "uuid-1")
    assert entry is not None
    assert entry.digested_hash is not None, "Expected ledger to advance after curator run"
    assert entry.digested_hash == turns[-1].content_hash()

    # (d) fake_runner was called at least twice — once for classify, once for merge_judgment.
    assert len(seen_schemas) >= 2, (
        f"Expected >= 2 fake_runner calls (classify + merge_judgment), got {len(seen_schemas)}: {seen_schemas}"
    )
    schema_key_sets = [frozenset(k) for k in seen_schemas]
    assert any("noteworthy" in s for s in schema_key_sets), (
        "Expected one call with 'noteworthy' key (classify)"
    )
    assert any(("merge" in s or "new" in s) for s in schema_key_sets), (
        "Expected one call with 'merge'/'new' keys (merge_judgment)"
    )


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
        ["capture", "--event", "session-end", "--cwd", str(work), "--host", "claude-code"],
        env={"LORE_ROOT": str(lore_root), "CLAUDE_PROJECT_DIR": str(work)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"capture failed: {result.output}"

    # Run curator with no LLM client (mirrors the no-backend situation).
    from lore_curator.curator_a import run_curator_a

    curator_result = run_curator_a(
        lore_root=lore_root,
        anthropic_client=None,   # no backend
        dry_run=False,
        now=_NOW,
    )

    # No session note should have been created.
    sessions_dir = lore_root / "wiki" / "private" / "sessions"
    notes = list(sessions_dir.glob("*.md"))
    assert len(notes) == 0, f"Expected no session notes when no backend, found: {notes}"

    # The transcript was considered but skipped due to missing client.
    assert curator_result.transcripts_considered >= 1
    assert "no_anthropic_client" in curator_result.skipped_reasons, (
        f"Expected 'no_anthropic_client' skip reason, got {curator_result.skipped_reasons}"
    )
