"""trace_id propagation end-to-end + drain producer on the spine (#188).

One flush mints a trace_id at the spawn boundary, delivers it to the
detached curator via an env var, and stamps it on every event the flush
emits — curator run events, flush-state events, the drain event, and the
published note's linkage frontmatter — so a whole flush is correlatable,
and two concurrent flushes stay separable by trace_id.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from lore_core import note_document as nd
from lore_core.run_log import RunLogger
from lore_core.spine import read_spine
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer
from lore_curator.chapter_flush import spawn_detached_flush, synth_and_close

# ---------------------------------------------------------------------------
# Fakes (mirrors test_chapter_flush's harness — every LLM call is stubbed)
# ---------------------------------------------------------------------------


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
        return _Resp(payload)


class _Client:
    def __init__(self, payloads: list[dict | None]) -> None:
        self.messages = _Messages(payloads)


def _chapter_payload(lead: str, body: str, anchor: int) -> dict[str, Any]:
    return {"blocks": [{"lead": lead, "body": body, "anchor": anchor}]}


@pytest.fixture(autouse=True)
def _patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **k: [])
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


def _append(lore_root: Path, turns: list[Turn], *, tid: str = "abc") -> Buffer:
    outcome = append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date="2026-05-01",
        transcript_id=tid,
        integration="fake",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )
    return outcome.buffer


def _flush(lore_root: Path, buf: Buffer, turns: list[Turn], trace_id: str):
    """Run a full close flush under a RunLogger carrying ``trace_id``."""
    wiki_root = lore_root / "wiki" / "private"
    client = _Client([_chapter_payload("Traced the flush", "prose.", turns[len(turns) // 2].index)])
    with RunLogger(lore_root, trigger="flush", trace_id=trace_id) as logger:
        return synth_and_close(
            buf.sidecar_path,
            lore_root=lore_root,
            wiki_root=wiki_root,
            llm_client=client,
            model="m",
            adapter_lookup=_lookup(_Adapter(turns)),
            auto_commit=False,
            logger=logger,
        )


# ---------------------------------------------------------------------------
# AC1 — mint at the spawn boundary + deliver via env var
# ---------------------------------------------------------------------------


def test_spawn_mints_trace_id_and_delivers_via_env(tmp_path, monkeypatch):
    buffer_path = tmp_path / "abc.state.json"
    buffer_path.write_text("{}")
    captured: dict[str, Any] = {}

    class _FakePopen:
        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env")

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    trace_id = spawn_detached_flush(buffer_path, lore_root=tmp_path)
    assert isinstance(trace_id, str) and trace_id
    assert captured["env"]["LORE_TRACE_ID"] == trace_id


def test_spawn_honours_explicit_trace_id(tmp_path, monkeypatch):
    buffer_path = tmp_path / "def.state.json"
    buffer_path.write_text("{}")
    captured: dict[str, Any] = {}

    class _FakePopen:
        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env")

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    trace_id = spawn_detached_flush(buffer_path, lore_root=tmp_path, trace_id="fixed-123")
    assert trace_id == "fixed-123"
    assert captured["env"]["LORE_TRACE_ID"] == "fixed-123"


# ---------------------------------------------------------------------------
# AC2 — tracer bullet: one flush, one trace_id on every record + the note
# ---------------------------------------------------------------------------


def test_one_flush_stamps_one_trace_id_across_run_drain_and_note(tmp_path):
    lore_root = _lore_root(tmp_path)
    turns = _turns(0, 9)
    buf = _append(lore_root, turns)
    trace = "trace-tracer-bullet"

    outcome = _flush(lore_root, buf, turns, trace)
    assert outcome.status == "composed"

    recs = read_spine(lore_root)
    sources = {r["source"] for r in recs}
    events = {r["event"] for r in recs}
    assert "curator" in sources  # run + flush-state events
    assert "drain" in sources  # note-filed drain event
    assert "run-start" in events
    assert {"note-filed", "note-appended"} & events

    # Every record this flush emitted carries the SAME trace_id.
    assert recs, "no spine records emitted"
    for r in recs:
        assert r["trace_id"] == trace, (
            f"{r['source']}/{r['event']} lost trace_id: {r['trace_id']!r}"
        )

    # The published note carries the id that produced it.
    view = nd.read_note(outcome.note_path)
    assert view.frontmatter["linkage"]["trace_id"] == trace


# ---------------------------------------------------------------------------
# AC3 — drain producer emits through the spine (source="drain")
# ---------------------------------------------------------------------------


def test_drain_event_is_a_spine_record_not_a_legacy_file(tmp_path):
    lore_root = _lore_root(tmp_path)
    turns = _turns(0, 9)
    buf = _append(lore_root, turns)
    _flush(lore_root, buf, turns, "trace-drain")

    drain_recs = read_spine(lore_root, source="drain")
    assert drain_recs, "drain event never reached the spine"
    assert all(r["source"] == "drain" for r in drain_recs)
    # The legacy per-session drain jsonl writer is gone.
    assert not list((lore_root / ".lore" / "drain").glob("*.jsonl"))


# ---------------------------------------------------------------------------
# AC4 — a failed drain write degrades without raising and leaves a marker
# ---------------------------------------------------------------------------


def test_drain_write_failure_degrades_with_marker(tmp_path):
    from lore_core.drain import DrainStore

    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir(parents=True)
    # Make the spine path a directory so O_WRONLY opens fail loudly.
    (lore_dir / "spine.jsonl").mkdir()

    DrainStore(tmp_path, "s1").emit("note-filed", wiki="w", path="/x")  # must not raise
    assert (lore_dir / "spine-failed.marker").exists()


# ---------------------------------------------------------------------------
# AC5 — two interleaved flushes stay separable by trace_id
# ---------------------------------------------------------------------------


def test_two_flushes_are_separable_by_trace_id(tmp_path):
    lore_root = _lore_root(tmp_path)
    t1, t2 = _turns(0, 9), _turns(0, 9)
    buf1 = _append(lore_root, t1, tid="sess-one")
    buf2 = _append(lore_root, t2, tid="sess-two")

    _flush(lore_root, buf1, t1, "trace-ONE")
    _flush(lore_root, buf2, t2, "trace-TWO")

    recs = read_spine(lore_root)
    # No record leaks a null trace_id — both flushes fully stamped.
    assert all(r["trace_id"] in {"trace-ONE", "trace-TWO"} for r in recs)

    # run_ids partition cleanly: each run_id belongs to exactly one trace.
    run_to_traces: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        if r["run_id"] is not None:
            run_to_traces[r["run_id"]].add(r["trace_id"])
    assert run_to_traces, "no run_id-bearing records"
    assert all(len(traces) == 1 for traces in run_to_traces.values())

    # Each trace has its own drain note event.
    drain_by_trace = defaultdict(list)
    for r in read_spine(lore_root, source="drain"):
        drain_by_trace[r["trace_id"]].append(r)
    assert set(drain_by_trace) == {"trace-ONE", "trace-TWO"}
