"""Regression net for the formerly-silent flush failure paths (issue #189).

Before this slice a failed flush "deferred silently (no marker)", a spawn
failure returned False into the void, an unreadable sidecar skipped without a
trace, and a chapter-append I/O error propagated with no flush-state record.
Each now produces a spine event or a dead-letter — a stuck flush is a
queryable row, never an absence of evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core import note_document as nd
from lore_core.flush_store import FlushState, FlushStore
from lore_core.spine import ErrorCode, read_spine
from lore_curator.chapter_flush import (
    flush_chapter,
    spawn_detached_flush,
    synth_in_place,
)

# Reuse the compose-pipeline fakes from the sibling suite.
from test_chapter_flush import (  # noqa: E402
    _Adapter,
    _append,
    _chapter_payload,
    _Client,
    _lookup,
    _lore_root,
    _turns,
)


@pytest.fixture(autouse=True)
def _patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **k: [])
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window", lambda *a, **k: ([], [])
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_projects_for_session", lambda **k: []
    )
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _curator_events(lore_root: Path) -> list[dict]:
    return read_spine(lore_root, source="curator")


# ---------------------------------------------------------------------------
# The silent defer is gone: a failed mid-session flush now leaves evidence.
# ---------------------------------------------------------------------------


def test_failed_midsession_flush_leaves_a_queryable_queued_record(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 2)
    buf = _append(lore_root, all_turns)

    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([None, None]),  # both compose attempts fail
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "deferred"
    # No marker while a retry chance remains — but no longer SILENT:
    assert nd.read_note(outcome.note_path).chapters == []

    store = FlushStore(lore_root)
    queued = store.list(state=FlushState.QUEUED)
    assert [r.buffer_stem for r in queued] == [buf.stem]
    assert queued[0].attempts == 1
    assert queued[0].next_retry_at is not None  # backoff scheduled

    events = [e["event"] for e in _curator_events(lore_root)]
    assert "flush-queued" in events  # the defer emitted a spine event


def test_repeated_failures_dead_letter_with_structured_reason(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 2)
    buf = _append(lore_root, all_turns)
    adapter = _Adapter(all_turns)

    from lore_core.flush_store import MAX_ATTEMPTS

    outcome = None
    for _ in range(MAX_ATTEMPTS):
        outcome = synth_in_place(
            buf.sidecar_path,
            lore_root=lore_root,
            wiki_root=wiki_root,
            llm_client=_Client([None, None]),
            model="m",
            adapter_lookup=_lookup(adapter),
            auto_commit=False,
        )

    assert outcome.status == "gave-up"
    # Terminal dead-letter is listable and carries a structured reason.
    dead = FlushStore(lore_root).list(state=FlushState.DEAD_LETTERED)
    assert [r.buffer_stem for r in dead] == [buf.stem]
    assert dead[0].reason == ErrorCode.COMPOSE_FAILED.value
    # Failed marker written; buffer reset (one session stays one note).
    markers = [c for c in nd.read_note(outcome.note_path).chapters if c.get("kind") == "marker"]
    assert len(markers) == 1 and markers[0]["marker"] == nd.MARKER_FAILED
    assert buf.read_sidecar().state == "accumulating"
    # Error-level spine event with the closed error code.
    dl = [e for e in _curator_events(lore_root) if e["event"] == "flush-dead-lettered"]
    assert dl and dl[-1]["level"] == "error"
    assert dl[-1]["error_code"] == ErrorCode.COMPOSE_FAILED.value


# ---------------------------------------------------------------------------
# Buffer sidecar read error — corrupt sidecar no longer skips in silence.
# ---------------------------------------------------------------------------


def test_unreadable_sidecar_emits_error_event(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    buf = _append(lore_root, _turns(0, 2))
    # Corrupt the sidecar so read_sidecar() returns None despite the file
    # existing — the "read error" case, distinct from a missing buffer.
    buf.sidecar_path.write_text("{ this is not json")

    outcome = flush_chapter(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        close=False,
    )
    assert outcome.status == "skipped"  # still short-circuits, but now loudly
    errs = [
        e
        for e in _curator_events(lore_root)
        if e.get("error_code") == ErrorCode.SIDECAR_READ_FAILED.value
    ]
    assert errs, "an unreadable sidecar must emit an error-level spine event"
    assert errs[-1]["level"] == "error"


# ---------------------------------------------------------------------------
# Subprocess spawn failure — Popen OSError no longer vanishes.
# ---------------------------------------------------------------------------


def test_spawn_failure_emits_event(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    buf = _append(lore_root, _turns(0, 2))

    def boom(*a, **k):
        raise OSError("no fork")

    monkeypatch.setattr("subprocess.Popen", boom)
    ok = spawn_detached_flush(buf.sidecar_path, lore_root=lore_root)
    assert ok is None
    errs = [
        e for e in _curator_events(lore_root) if e.get("error_code") == ErrorCode.SPAWN_FAILED.value
    ]
    assert errs, "a spawn failure must emit a spine event"
    assert errs[-1]["trace_id"], "spawn-failed event must carry the minted trace_id"


def test_spawn_lock_failure_emits_event_with_trace_id(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    buf = _append(lore_root, _turns(0, 2))

    def boom(*a, **k):
        raise OSError("flock denied")

    monkeypatch.setattr("lore_core.lockfile.flocked", boom)
    ok = spawn_detached_flush(buf.sidecar_path, lore_root=lore_root)
    assert ok is None
    errs = [
        e for e in _curator_events(lore_root) if e.get("error_code") == ErrorCode.SPAWN_FAILED.value
    ]
    assert errs, "a spawn-lock failure must emit a spine event"
    assert errs[-1]["trace_id"], "lock-failure event must carry a trace_id too"


# ---------------------------------------------------------------------------
# Chapter-append I/O error — a write failure dead-letters instead of crashing.
# ---------------------------------------------------------------------------


def test_chapter_append_ioerror_dead_letters(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 2)
    buf = _append(lore_root, all_turns)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(nd, "append_chapter", boom)

    # A good compose that would normally append a chapter.
    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([_chapter_payload("Lead", "prose.", 0)]),
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    # No crash; the flush is a dead-letter with the append error code.
    assert outcome.status == "failed"
    dead = FlushStore(lore_root).list(state=FlushState.DEAD_LETTERED)
    assert [r.reason for r in dead] == [ErrorCode.CHAPTER_APPEND_FAILED.value]
    dl = [e for e in _curator_events(lore_root) if e["event"] == "flush-dead-lettered"]
    assert dl and dl[-1]["error_code"] == ErrorCode.CHAPTER_APPEND_FAILED.value
