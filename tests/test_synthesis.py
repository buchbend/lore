"""Tests for lore_curator.synthesis — two-phase flush worker."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lore_core.schema import parse_frontmatter
from lore_core.types import Scope, TranscriptHandle, Turn
from lore_core.wiki_config import WikiConfig
from lore_curator import stub_note
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer
from lore_curator.stub_note import write_or_update
from lore_curator.synthesis import (
    BULLET_CAPS,
    BULLET_LINE_MAX,
    FlushOutcome,
    flush_buffer,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    def __init__(self, type_: str, input_: dict | None = None):
        self.type = type_
        self.input = input_ or {}


class _FakeResponse:
    def __init__(self, content: list):
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, responder):
        self._responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responder(kwargs)


class _FakeLlmClient:
    def __init__(self, responder):
        self.messages = _FakeMessagesAPI(responder)


def _ok_responder(composed: dict[str, Any]):
    def _r(_kwargs):
        return _FakeResponse([_FakeContentBlock("tool_use", composed)])
    return _r


def _err_responder():
    def _r(_kwargs):
        raise RuntimeError("LLM down")
    return _r


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scope() -> Scope:
    return Scope(
        wiki="private",
        scope="proj:feature",
        backend="none",
        claude_md_path=Path("/tmp/CLAUDE.md"),
    )


def _make_handle() -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id="transcript-X",
        path=Path("/tmp/t.jsonl"),
        cwd=Path("/tmp"),
        mtime=datetime.now(UTC),
    )


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"msg-{i}")
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_plans_advanced", lambda **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _seed_stub(lore_root: Path, monkeypatch, *, files=None, transcript_id: str = "abc") -> tuple:
    """Run one heartbeat + write_or_update; return (buffer, stub_path, sidecar_path)."""
    files = files if files is not None else ["/repo/auth.py"]
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: list(files),
    )
    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id=transcript_id, integration="claude-code", wiki="private", scope="proj:feature",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time, integration="claude-code",
        chunk_from_hash="h0", chunk_to_hash="h1",
    )
    return outcome.buffer, Path(outcome.buffer.read_sidecar().stub_path), outcome.buffer.sidecar_path


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def test_phase1_drops_stub_marker_and_closes_buffer(lore_root, patch_collectors, monkeypatch):
    buffer, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=None,
        model=None,
    )
    assert outcome.phase1_completed is True
    assert outcome.phase2_completed is False
    # The on-disk file is the deterministic Phase 1 note.
    fm = parse_frontmatter(stub_path.read_text())
    assert "state" not in fm
    # The buffer's live sidecar is gone — moved to _done/.
    assert not sidecar_path.exists()
    moved = lore_root / ".lore" / "buffers" / "_done" / sidecar_path.name
    assert moved.exists()


def test_phase1_idempotent_on_already_closed(lore_root, patch_collectors, monkeypatch):
    _, _, sidecar_path = _seed_stub(lore_root, monkeypatch)

    flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    # Second invocation -- sidecar moved to _done already; passing the
    # original path should short-circuit cleanly.
    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert outcome.skipped_reason in ("no-sidecar", "already-closed")


def test_phase1_handles_missing_stub_gracefully(lore_root, patch_collectors, monkeypatch):
    buffer, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)
    stub_path.unlink()

    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert outcome.phase1_completed is False
    # Buffer still gets closed -- handover gates on state, not stub presence.
    moved = lore_root / ".lore" / "buffers" / "_done" / sidecar_path.name
    assert moved.exists()


def test_phase1_strips_dangling_plan_refs(lore_root, patch_collectors, monkeypatch):
    monkeypatch.setattr(
        "lore_curator.buffer_append._files_touched_from_turns",
        lambda turns: ["/repo/x.py"],
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_plans_advanced",
        lambda **kw: ["real-plan", "ghost-plan"],
    )
    (lore_root / "wiki" / "private" / "plans").mkdir(parents=True)
    (lore_root / "wiki" / "private" / "plans" / "real-plan.md").write_text("---\ntype: plan\n---\n")

    work_time = datetime(2026, 5, 1, 14, 32, tzinfo=UTC)
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:feature",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    write_or_update(
        outcome=outcome, scope=_make_scope(), transcript=_make_handle(),
        wiki_root=lore_root / "wiki" / "private",
        work_time=work_time, now=work_time, integration="claude-code",
        chunk_from_hash="h0", chunk_to_hash="h1",
    )
    flush_outcome = flush_buffer(
        outcome.buffer.sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
    )
    assert flush_outcome.dangling_plans == ["ghost-plan"]
    fm = parse_frontmatter(Path(outcome.buffer.read_sidecar() and "ignored").parent.absolute()) if False else parse_frontmatter(
        # Read the (now Phase-1-finalised) stub.
        next((lore_root / "wiki" / "private" / "sessions").rglob("*.md")).read_text()
    )
    assert fm.get("plans") == ["real-plan"]


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------


def test_phase2_rewrites_title_and_summary(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed = {
        "title": "auth handler refactor",
        "description": "Rebuilt auth.py to align with the new policy decorator.",
        "summary": "We pulled the legacy callbacks out and slotted a tidy decorator chain in their place.",
        "decisions": ["**Decorator chain** — sticks with the new shape"],
        "worked_on": ["**auth.py** — pulled callbacks", "**tests** — green"],
        "loose_ends": ["**docs** — the migration note remained unwritten"],
    }
    llm = _FakeLlmClient(_ok_responder(composed))

    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.phase2_attempts == 1
    fm = parse_frontmatter(stub_path.read_text())
    assert fm["title"] == "auth handler refactor"
    text = stub_path.read_text()
    assert "## Summary" in text
    assert "## Decisions made" in text
    assert "## What we worked on" in text
    assert "## Loose ends" in text


def test_phase2_retry_then_succeed(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed_ok = {
        "title": "title", "description": "desc", "summary": "sum",
    }

    state = {"calls": 0}

    def _flaky(_kw):
        state["calls"] += 1
        if state["calls"] < 3:
            raise RuntimeError("transient")
        return _FakeResponse([_FakeContentBlock("tool_use", composed_ok)])

    llm = _FakeLlmClient(_flaky)
    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    assert outcome.phase2_attempts == 3


def test_phase2_exhausts_and_degrades(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    llm = _FakeLlmClient(_err_responder())
    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.degraded is True
    assert outcome.phase2_completed is False
    assert outcome.phase2_attempts == 3
    # Stub is still the deterministic Activity-only note.
    fm = parse_frontmatter(stub_path.read_text())
    assert "state" not in fm  # state:stub dropped by Phase 1


def test_phase2_truncates_overlong_bullets(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    composed = {
        "title": "t", "description": "d", "summary": "s",
        "decisions": [f"d-{i}" for i in range(BULLET_CAPS["decisions"] + 5)],
        "worked_on": ["X" * (BULLET_LINE_MAX + 50)],
        "loose_ends": [],
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = stub_path.read_text()
    # Only the cap'd count of decision lines.
    decision_count = text.count("- d-")
    assert decision_count == BULLET_CAPS["decisions"]
    # Worked-on line truncated.
    for line in text.splitlines():
        if line.startswith("- "):
            assert len(line) <= BULLET_LINE_MAX + 2  # "- " prefix + capped content


def test_phase2_skipped_without_llm_client(lore_root, patch_collectors, monkeypatch):
    _, stub_path, sidecar_path = _seed_stub(lore_root, monkeypatch)

    outcome = flush_buffer(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=None,
        model=None,
    )
    assert outcome.phase1_completed is True
    assert outcome.phase2_completed is False
    assert outcome.degraded is False
