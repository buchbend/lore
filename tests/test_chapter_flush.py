"""Chapter flush lifecycle — compose -> gate -> append + failure semantics.

Every LLM interaction is faked; no test asserts prose quality and no
LLM-as-judge appears. The stub composer records each ``messages.create``
kwargs and replays queued tool_use payloads (the saved-buffer replay
harness shape). The publish gate is the real deterministic gate.
"""

from __future__ import annotations

import secrets as _secrets
from pathlib import Path
from typing import Any

import pytest
from lore_core import note_document as nd
from lore_core import quarantine
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer
from lore_curator.chapter_flush import synth_and_close, synth_in_place

# ---------------------------------------------------------------------------
# Fakes
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
        if payload is None:
            raise RuntimeError("simulated LLM failure")
        return _Resp(payload)


class _Client:
    def __init__(self, payloads: list[dict | None]) -> None:
        self.messages = _Messages(payloads)


def _chapter_payload(lead: str, body: str, anchor: int) -> dict[str, Any]:
    return {"blocks": [{"lead": lead, "body": body, "anchor": anchor}]}


# ---------------------------------------------------------------------------
# Fixtures / seeding
# ---------------------------------------------------------------------------


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


def _lore_root(tmp_path: Path, *, cap_turns: int | None = None) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    wiki_root = tmp_path / "wiki" / "private"
    (wiki_root / "sessions").mkdir(parents=True)
    if cap_turns is not None:
        (wiki_root / ".lore-wiki.yml").write_text(
            f"curator:\n  synthesis_buffer_cap_turns: {cap_turns}\n"
            f"  synthesis_buffer_cap_chars: 100000\n"
        )
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


# ---------------------------------------------------------------------------
# Integration: compose -> gate -> append (in-place)
# ---------------------------------------------------------------------------


def test_inplace_flush_composes_one_call_and_appends_chapter(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 4)
    buf = _append(lore_root, all_turns)
    client = _Client([_chapter_payload("Traced the flush race", "prose.", 2)])

    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "composed"
    # Exactly one compose call for the single attempt.
    assert len(client.messages.calls) == 1

    view = nd.read_note(outcome.note_path)
    topic = [c for c in view.chapters if c.get("kind") == "topic"]
    assert len(topic) == 1
    assert topic[0]["from_turn"] == 0 and topic[0]["to_turn"] == 4
    assert "Traced the flush race" in view.body
    # In-place: the buffer keeps accumulating and is not archived.
    assert buf.read_sidecar().state == "accumulating"


def test_note_so_far_carries_prior_chapter_into_next_compose(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    adapter = _Adapter(_turns(0, 5))

    # Chapter 1 over turns 0-2.
    _append(lore_root, _turns(0, 2))
    buf = Buffer.open(lore_root, transcript_id="abc", local_date="2026-05-01")
    c1 = _Client([_chapter_payload("Recorded the buffer store design", "Prose about buffers.", 1)])
    synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=c1,
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )

    # Chapter 2 over turns 3-5 — the composer must see chapter 1 in note-so-far.
    _append(lore_root, _turns(3, 5))
    c2 = _Client([_chapter_payload("Discussed the flush lifecycle", "More prose.", 4)])
    synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=c2,
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )

    prompt = c2.messages.calls[0]["messages"][0]["content"]
    assert "Recorded the buffer store design" in prompt  # note-so-far included
    view = nd.read_note(Path(buf.read_sidecar().stub_path))
    assert len([c for c in view.chapters if c.get("kind") == "topic"]) == 2


def test_gate_withhold_drives_retry_then_composes(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 4)
    buf = _append(lore_root, all_turns)
    # Attempt 1 has an imperative lead (phrasing withhold); attempt 2 is clean.
    client = _Client(
        [
            _chapter_payload("Fix the flush race", "detail.", 2),
            _chapter_payload("Traced the flush race", "The buffer accumulated turns.", 2),
        ]
    )
    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "composed"
    assert len(client.messages.calls) == 2
    # The retry prompt carried the gate's phrasing feedback.
    retry_prompt = client.messages.calls[1]["messages"][0]["content"]
    assert "past-tense" in retry_prompt.lower()


# ---------------------------------------------------------------------------
# Planted-secret end-to-end: withheld marker + quarantine
# ---------------------------------------------------------------------------


def test_planted_secret_withholds_marker_and_quarantines(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 4)
    buf = _append(lore_root, all_turns)
    token = _secrets.token_urlsafe(40)
    # Both attempts leak the secret -> the real gate withholds both times.
    leak = _chapter_payload("Reviewed the incident", f"key sk-{token} was pasted.", 2)
    client = _Client([leak, dict(leak)])

    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "withheld"

    view = nd.read_note(outcome.note_path)
    markers = [c for c in view.chapters if c.get("kind") == "marker"]
    assert len(markers) == 1 and markers[0]["marker"] == nd.MARKER_WITHHELD
    # The unsafe text never reaches the shared note.
    assert token not in outcome.note_path.read_text()
    # A private quarantine entry holds the full composed text.
    entries = quarantine.list_entries(lore_root=lore_root)
    assert len(entries) == 1
    assert token in entries[0].composed_text


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------


def test_failed_midsession_flush_defers_silently(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 2)
    buf = _append(lore_root, all_turns)
    client = _Client([None, None])  # both compose attempts raise -> FAILED

    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "deferred"

    view = nd.read_note(outcome.note_path)
    assert view.chapters == []  # NO marker while a retry chance remains
    sidecar = buf.read_sidecar()
    assert sidecar.state == "accumulating"
    assert sidecar.flush_attempts == 1  # the failed attempt is remembered
    assert sidecar.flush_requested is None  # request slot re-opened for next trigger


def test_next_trigger_retries_with_accumulated_slice(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    adapter = _Adapter(_turns(0, 5))

    _append(lore_root, _turns(0, 2))
    buf = Buffer.open(lore_root, transcript_id="abc", local_date="2026-05-01")
    # First trigger fails.
    synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([None, None]),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )

    # Session continues; more turns accumulate.
    _append(lore_root, _turns(3, 5))
    good = _Client([_chapter_payload("Traced the whole slice", "prose.", 0)])
    outcome = synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=good,
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    assert outcome.status == "composed"
    # The retry composed the full accumulated slice, turns 0..5.
    prompt = good.messages.calls[0]["messages"][0]["content"]
    assert "[user@0]" in prompt and "[assistant@5]" in prompt
    view = nd.read_note(outcome.note_path)
    topic = [c for c in view.chapters if c.get("kind") == "topic"][0]
    assert topic["from_turn"] == 0 and topic["to_turn"] == 5
    assert buf.read_sidecar().flush_attempts == 0  # progress cleared the memory


def test_give_up_at_double_cap_writes_marker_and_fresh_buffer(tmp_path):
    lore_root = _lore_root(tmp_path, cap_turns=2)  # 2x cap == 4 turns
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 3)  # 4 turns -> at 2x cap
    buf = _append(lore_root, all_turns)
    adapter = _Adapter(all_turns)

    # First flush fails -> deferred (flush_attempts -> 1).
    synth_in_place(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([None, None]),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    assert buf.read_sidecar().flush_attempts == 1

    # Second flush at 2x cap with a prior failure -> give up.
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

    view = nd.read_note(outcome.note_path)
    markers = [c for c in view.chapters if c.get("kind") == "marker"]
    assert len(markers) == 1 and markers[0]["marker"] == nd.MARKER_FAILED
    assert markers[0]["to_turn"] == 3
    # Fresh buffer: log truncated + counters reset, still accumulating, one note.
    sidecar = buf.read_sidecar()
    assert sidecar.state == "accumulating"
    assert sidecar.flush_attempts == 0
    assert buf.replay().turn_count == 0


def test_session_end_failure_writes_marker_and_closes(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 2)
    buf = _append(lore_root, all_turns)

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([None, None]),
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "failed"
    assert outcome.closed is True

    view = nd.read_note(outcome.note_path)
    markers = [c for c in view.chapters if c.get("kind") == "marker"]
    assert len(markers) == 1 and markers[0]["marker"] == nd.MARKER_FAILED
    assert view.closed is True
    assert nd.is_closed(outcome.note_path) is True
    # The buffer was archived to _done/.
    assert not buf.sidecar_path.exists()


def test_session_end_success_appends_chapter_and_closes(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 4)
    buf = _append(lore_root, all_turns)
    client = _Client([_chapter_payload("Wrapped the session", "final prose.", 3)])

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "composed"
    assert outcome.closed is True
    view = nd.read_note(outcome.note_path)
    assert len([c for c in view.chapters if c.get("kind") == "topic"]) == 1
    assert view.closed is True
    assert not buf.sidecar_path.exists()
