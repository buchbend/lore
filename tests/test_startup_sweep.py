"""Startup sweep — singleton close of dead sessions' notes.

At start lore acts as a singleton (global lock) and closes the note of
every dead session, composing one final chapter through the normal gate
or a failed marker when it can't. Concurrent starts race safely on the
lock: the loser touches nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lore_core import note_document as nd
from lore_core.lockfile import curator_lock
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer, OwnerInfo
from lore_curator.chapter_flush import startup_sweep, sweep_dead_sessions


class _Adapter:
    integration = "fake"

    def __init__(self, turns: list[Turn]) -> None:
        self._turns = turns

    def transcript_path_for_id(self, transcript_id: str, cwd: Path) -> Path:
        return cwd / f"{transcript_id}.jsonl"

    def read_slice(self, handle, from_index: int = 0):
        yield from (t for t in self._turns if t.index >= from_index)


def _lookup(adapter: _Adapter):
    def _l(integration: str):
        return adapter

    return _l


class _Block:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _Resp:
    def __init__(self, payload):
        self.content = [_Block(payload)]
        self.model = "m"


class _Messages:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        p = self._payloads.pop(0) if self._payloads else {}
        if p is None:
            raise RuntimeError("simulated LLM failure")
        return _Resp(p)


class _Client:
    def __init__(self, payloads):
        self.messages = _Messages(payloads)


@pytest.fixture(autouse=True)
def _patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **k: [])
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_issues_in_window",
        lambda *a, **k: ([], []),
    )
    monkeypatch.setattr(
        "lore_curator.session_activity.collect_projects_for_session",
        lambda **k: [],
    )
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _turns(lo: int, hi: int) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"line {i}")
        for i in range(lo, hi + 1)
    ]


def _lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private" / "sessions").mkdir(parents=True)
    return tmp_path


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _seed(
    lore_root: Path,
    turns: list[Turn],
    *,
    tid: str,
    owner: OwnerInfo,
    local_date: str | None = None,
) -> Buffer:
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date=local_date or _today(),
        transcript_id=tid,
        integration="fake",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )
    buf = outcome.buffer
    with buf.with_lock():
        buf.patch(owner=owner)
    return buf


def _dead_owner() -> OwnerInfo:
    # A different host is an unambiguous "not this process" -> dead.
    return OwnerInfo(pid=99999, host="a-host-that-is-not-ours", start_ts=1.0)


def _uncertain_owner() -> OwnerInfo:
    # No pid -> liveness can't be judged; the sweep must leave it alone.
    return OwnerInfo(pid=0, host="", start_ts=0.0)


def test_sweep_composes_and_closes_dead_session(tmp_path):
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 12)  # above the trivial-session gate
    buf = _seed(lore_root, all_turns, tid="dead1", owner=_dead_owner())
    client = _Client([{"blocks": [{"lead": "Recorded the crash", "body": "prose.", "anchor": 2}]}])

    report = sweep_dead_sessions(
        lore_root,
        llm_client=client,
        adapter_lookup=_lookup(_Adapter(all_turns)),
    )
    assert report.swept == 1
    # The dead session's note is composed AND closed; the buffer archived.
    note_path = next((tmp_path / "wiki" / "private" / "sessions").rglob("*.md"))
    view = nd.read_note(note_path)
    assert view.closed is True
    assert len([c for c in view.chapters if c.get("kind") == "topic"]) == 1
    assert not buf.sidecar_path.exists()  # moved to _done/
    assert (tmp_path / ".lore" / "buffers" / "_done" / f"{buf.stem}.state.json").exists()


def test_sweep_marks_and_closes_when_compose_fails(tmp_path):
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 12)
    buf = _seed(lore_root, all_turns, tid="dead2", owner=_dead_owner())

    report = sweep_dead_sessions(
        lore_root,
        llm_client=_Client([None, None]),
        adapter_lookup=_lookup(_Adapter(all_turns)),
    )
    assert report.swept == 1
    note_path = next((tmp_path / "wiki" / "private" / "sessions").rglob("*.md"))
    view = nd.read_note(note_path)
    assert view.closed is True
    markers = [c for c in view.chapters if c.get("kind") == "marker"]
    assert len(markers) == 1 and markers[0]["marker"] == nd.MARKER_FAILED
    assert not buf.sidecar_path.exists()


def test_sweep_stale_closes_old_dead_session_without_composing(tmp_path):
    # A dead buffer older than the stale window is closed with a marker and
    # NO LLM call, so a deep backlog can't stall startup. The client would
    # compose visible content if consulted — it must not appear.
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 12)
    old_date = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    buf = _seed(lore_root, all_turns, tid="stale1", owner=_dead_owner(), local_date=old_date)
    client = _Client([{"blocks": [{"lead": "should not appear", "body": "x", "anchor": 1}]}])

    report = sweep_dead_sessions(
        lore_root,
        llm_client=client,
        adapter_lookup=_lookup(_Adapter(all_turns)),
    )
    assert report.stale_closed == 1
    assert report.swept == 0
    note_path = next((tmp_path / "wiki" / "private" / "sessions").rglob("*.md"))
    view = nd.read_note(note_path)
    assert view.closed is True
    # No composed topic chapter — a marker instead (the LLM was never called).
    assert not any(c.get("kind") == "topic" for c in view.chapters)
    assert not buf.sidecar_path.exists()  # archived to _done/


def test_sweep_budget_defers_excess_recent_dead_sessions(tmp_path):
    # More recent dead buffers than the compose budget: the sweep handles
    # max_compose of them and leaves the rest for the next sweep.
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 12)
    for i in range(3):
        _seed(lore_root, all_turns, tid=f"recent{i}", owner=_dead_owner())
    client = _Client([{"blocks": [{"lead": "Recorded it", "body": "prose.", "anchor": 1}]}] * 3)

    report = sweep_dead_sessions(
        lore_root,
        llm_client=client,
        adapter_lookup=_lookup(_Adapter(all_turns)),
        max_compose=2,
    )
    assert report.swept == 2
    assert report.deferred == 1


def test_sweep_discards_trivial_dead_session_without_composing(tmp_path):
    # A tiny dead session (no files, no commits) leaves no note at all:
    # the sweep discards its stub deterministically, spending no LLM call
    # and no compose budget.
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 4)
    buf = _seed(lore_root, all_turns, tid="tiny", owner=_dead_owner())
    client = _Client([{"blocks": [{"lead": "must not appear", "body": "x", "anchor": 1}]}])

    report = sweep_dead_sessions(
        lore_root,
        llm_client=client,
        adapter_lookup=_lookup(_Adapter(all_turns)),
    )
    assert report.discarded == 1
    assert report.swept == 0
    assert client.messages.calls == []
    assert not list((tmp_path / "wiki" / "private" / "sessions").rglob("*.md"))
    assert not buf.sidecar_path.exists()  # archived to _done/


def test_sweep_skips_uncertain_owner(tmp_path):
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 2)
    buf = _seed(lore_root, all_turns, tid="maybe", owner=_uncertain_owner())

    report = sweep_dead_sessions(
        lore_root,
        llm_client=_Client([]),
        adapter_lookup=_lookup(_Adapter(all_turns)),
    )
    assert report.swept == 0
    assert report.uncertain_skipped == 1
    # Untouched: still accumulating, not archived.
    assert buf.read_sidecar().state == "accumulating"
    assert buf.sidecar_path.exists()


def test_startup_sweep_is_contended_when_global_lock_held(tmp_path):
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 2)
    buf = _seed(lore_root, all_turns, tid="dead3", owner=_dead_owner())

    # A concurrent start already holds the singleton lock.
    with curator_lock(lore_root, timeout=0.0):
        report = startup_sweep(
            lore_root,
            llm_client=_Client([None]),
            adapter_lookup=_lookup(_Adapter(all_turns)),
        )
    assert report.contended is True
    assert report.swept == 0
    # The loser touched nothing — the buffer is still live.
    assert buf.read_sidecar().state == "accumulating"
    assert buf.sidecar_path.exists()


def test_startup_sweep_closes_dead_session_under_lock(tmp_path):
    lore_root = _lore_root(tmp_path)
    all_turns = _turns(0, 12)
    buf = _seed(lore_root, all_turns, tid="dead4", owner=_dead_owner())
    client = _Client([{"blocks": [{"lead": "Recorded it", "body": "prose.", "anchor": 1}]}])

    report = startup_sweep(
        lore_root,
        llm_client=client,
        adapter_lookup=_lookup(_Adapter(all_turns)),
    )
    assert report.contended is False
    assert report.swept == 1
    note_path = next((tmp_path / "wiki" / "private" / "sessions").rglob("*.md"))
    assert nd.read_note(note_path).closed is True
    assert not buf.sidecar_path.exists()
