"""Tests for the SessionEnd flush_requested wiring.

Session end is the session boundary: it force-drains the buffer through the
close path (segment -> extract -> render) and the note appears once, complete.
Mid-session triggers (pre-compact, cap-trip) only bookkeep — they leave the
buffer accumulating so the whole session is still unflushed when the close path
reads it backward.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from lore_core import note_document as nd
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator import chapter_flush
from lore_curator.buffer_append import append_chunk
from lore_curator.capture_routing import (
    request_flush_for_my_buffers as _request_flush_for_my_buffers,
)
from lore_curator.session_curator import _dispatch_flush_requested


def _make_turns(n: int = 2) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"line {i}")
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private" / "sessions").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def patch_collectors(monkeypatch):
    sa = "lore_curator.session_activity"
    monkeypatch.setattr(f"{sa}.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr(f"{sa}.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr(f"{sa}.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _seed(lore_root: Path, *, transcript_id: str = "abc", turns: list[Turn] | None = None) -> Any:
    outcome = append_chunk(
        lore_root=lore_root, chunk_turns=turns or _make_turns(2), local_date="2026-05-01",
        transcript_id=transcript_id, integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    return outcome.buffer


# ---------------------------------------------------------------------------
# Fakes for the end-to-end dispatch (no LLM, no adapter registry)
# ---------------------------------------------------------------------------


class _Adapter:
    integration = "claude-code"

    def __init__(self, turns: list[Turn]) -> None:
        self._turns = turns

    def transcript_path_for_id(self, transcript_id: str, cwd: Path) -> Path:
        return cwd / f"{transcript_id}.jsonl"

    def read_slice(self, handle, from_index: int = 0):
        yield from (t for t in self._turns if t.index >= from_index)


class _Block:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = [_Block(payload)]
        self.model = "m"


class _Messages:
    def __init__(self, payloads: list[dict | None]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        payload = self._payloads.pop(0) if self._payloads else {}
        if payload is None:
            raise RuntimeError("simulated LLM failure")
        return _Resp(payload)


class _Client:
    def __init__(self, payloads: list[dict | None]) -> None:
        self.messages = _Messages(payloads)


class _Logger:
    trace_id = "trace-1"

    def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Trigger routing
# ---------------------------------------------------------------------------


def test_request_flush_routes_session_end_to_the_close_path(lore_root):
    """Session end is the session boundary: the buffer is handed to the close
    path (``ready``), which segments, extracts and renders the whole session."""
    buf = _seed(lore_root)
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 1
    sidecar = buf.read_sidecar()
    assert sidecar.state == "ready"
    assert sidecar.flush_requested is not None
    assert sidecar.flush_requested.trigger == "session-end"
    assert sidecar.flush_requested.by_pid == os.getpid()


def test_pre_compact_only_bookkeeps(lore_root):
    """Pre-compact is not a session boundary. Flushing there would compose the
    session's first turns forward, before its ending can say which of them
    mattered — so it leaves the buffer alone and the close path sees it whole."""
    buf = _seed(lore_root)
    stamped = _request_flush_for_my_buffers(lore_root, trigger="pre-compact")
    assert stamped == 0
    sidecar = buf.read_sidecar()
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested is None


def test_request_flush_skips_other_pid(lore_root):
    buf = _seed(lore_root)
    # Forge a different owner.pid so iter_for_pid doesn't match.
    with buf.with_lock():
        from lore_curator.buffer_store import OwnerInfo
        buf.patch(owner=OwnerInfo(
            pid=42, host="other", start_ts=0.0, run_id="", claude_session_id="",
        ))
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 0
    sidecar = buf.read_sidecar()
    assert sidecar.state == "accumulating"
    assert sidecar.flush_requested is None


def test_request_flush_skips_closed_buffers(lore_root):
    buf = _seed(lore_root)
    with buf.with_lock():
        buf.transition("ready")
        buf.transition("flushing")
        buf.transition("closed")
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 0


def test_request_flush_idempotent_when_request_already_stamped(lore_root):
    buf = _seed(lore_root)
    _request_flush_for_my_buffers(lore_root, trigger="session-end")
    # Second call -> short-circuits without touching state.
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end")
    assert stamped == 0
    sidecar = buf.read_sidecar()
    assert sidecar.flush_requested is not None
    assert sidecar.flush_requested.trigger == "session-end"


def test_request_flush_max_scan_bound(lore_root):
    for tid in ("a", "b", "c", "d"):
        _seed(lore_root, transcript_id=tid)
    stamped = _request_flush_for_my_buffers(lore_root, trigger="session-end", max_scan=2)
    # Bound is on scan count, not stamp count.
    assert stamped <= 2


# ---------------------------------------------------------------------------
# End to end: the trigger the hook fires, through the dispatch the curator runs
# ---------------------------------------------------------------------------


def test_session_end_drains_the_whole_session_into_a_rendered_facts_note(lore_root, monkeypatch):
    """The one test that exercises the wiring instead of the pieces.

    Nothing calls ``synth_and_close`` here: the hook's trigger and the
    curator's dispatch decide what runs. What lands is the end-mode note —
    typed facts in the ledger, a rendered body — and not a prose chapter.
    """
    turns = _make_turns(13)  # above the trivial-session gate
    buf = _seed(lore_root, turns=turns)
    monkeypatch.setattr(chapter_flush, "get_adapter", lambda integration: _Adapter(turns))
    client = _Client(
        [
            {"boundaries": []},  # segmentation: one beat
            {
                "facts": [
                    {
                        "kind": "done",
                        "text": "The close path landed.",
                        "anchor": 4,
                        "thread": "wiring",
                    }
                ]
            },
            {"headline": "The close path landed."},
        ]
    )

    assert _request_flush_for_my_buffers(lore_root, trigger="session-end") == 1
    assert _dispatch_flush_requested(lore_root, llm_client=client, logger=_Logger()) == 1

    notes = list((lore_root / "wiki" / "private" / "sessions").rglob("*.md"))
    assert len(notes) == 1
    view = nd.read_note(notes[0])
    assert [c["kind"] for c in view.chapters] == ["facts"]
    assert "**The close path landed.**" in view.body
    assert "## Done" in view.body
    # Session end drains unconditionally: the note is complete and closed, and
    # the buffer is archived — nothing is left for the reaper to pick up.
    assert view.closed is True
    assert not buf.sidecar_path.exists()
