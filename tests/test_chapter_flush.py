"""Chapter flush lifecycle — segment -> extract -> gate -> render at close.

There is one flush and it runs at the close, so every test here closes a
session. Every LLM interaction is faked; no test asserts prose quality and no
LLM-as-judge appears. The stub client records each ``messages.create`` kwargs
and replays queued tool_use payloads (the saved-buffer replay harness shape).
The publish gate is the real deterministic gate.
"""

from __future__ import annotations

import secrets as _secrets
import subprocess
from pathlib import Path
from typing import Any

import pytest
from lore_core import note_document as nd
from lore_core import quarantine
from lore_core.schema import parse_frontmatter
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import Buffer
from lore_curator.chapter_flush import synth_and_close

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


def _fact_item(
    kind: str,
    text: str,
    anchor: int,
    thread: str = "",
    why: str = "",
    refs: list[dict] | None = None,
) -> dict:
    item: dict[str, Any] = {"kind": kind, "text": text, "anchor": anchor}
    if thread:
        item["thread"] = thread
    if why:
        item["why"] = why
    if refs:
        item["refs"] = refs
    return item


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


# -------------------------------------------------------------------------
# Failure semantics
# -------------------------------------------------------------------------


def test_session_end_failure_writes_marker_and_closes(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 12)  # above the trivial-session gate
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


# -------------------------------------------------------------------------
# No note is better than a noise note: trivial gate + empty extraction
# -------------------------------------------------------------------------


def test_session_end_trivial_session_leaves_no_note(tmp_path):
    # A tiny session with no file/commit activity is discarded
    # deterministically: no LLM call, the stub note is removed, the
    # buffer is archived.
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 4)
    buf = _append(lore_root, all_turns)
    client = _Client([{"boundaries": []}, {"facts": [_fact_item("done", "never read", 1)]}])

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )
    assert outcome.status == "trivial"
    assert outcome.discarded is True
    assert outcome.closed is True
    assert client.messages.calls == []
    assert not list((wiki_root / "sessions").rglob("*.md"))
    assert not buf.sidecar_path.exists()  # archived to _done/


def test_empty_extraction_after_a_real_chapter_closes_the_note(tmp_path):
    # A session that recorded facts, then reopened and produced nothing worth
    # recording: the note closes with the facts it already had.
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    adapter = _Adapter(_turns(0, 20))

    _append(lore_root, _turns(0, 12))
    buf = Buffer.open(lore_root, transcript_id="abc", local_date="2026-05-01")
    first = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client(
            [
                {"boundaries": []},
                {"facts": [_fact_item("done", "The lock landed.", 4, thread="lock")]},
                {"headline": "The lock landed."},
            ]
        ),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )

    _append(lore_root, _turns(13, 20))  # the session resumes: note reopened
    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([{"boundaries": []}, {"facts": []}]),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )

    assert outcome.status == "empty"
    assert outcome.discarded is False  # the earlier facts are not thrown away
    assert outcome.closed is True
    view = nd.read_note(first.note_path)
    assert [c["kind"] for c in view.chapters] == ["facts"]
    assert view.closed is True
    assert not buf.sidecar_path.exists()


# -------------------------------------------------------------------------
# Planted-secret end-to-end: withheld marker + quarantine
# -------------------------------------------------------------------------


def test_planted_secret_withholds_marker_and_quarantines(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 12)
    buf = _append(lore_root, all_turns)
    token = _secrets.token_urlsafe(40)
    # Every attempt leaks the secret -> the real gate withholds them all.
    leak = {"facts": [_fact_item("done", f"Pasted key sk-{token} into the config.", 2)]}
    client = _Client([{"boundaries": []}, leak, dict(leak), {"headline": "Reviewed the incident."}])

    outcome = synth_and_close(
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
    # A private quarantine entry holds the full withheld text.
    entries = quarantine.list_entries(lore_root=lore_root)
    assert len(entries) == 1
    assert token in entries[0].composed_text


# -------------------------------------------------------------------------
# Headline-derived slug — the note is named once its facts exist
# -------------------------------------------------------------------------


def test_session_end_names_the_note_from_the_headline(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 12)
    buf = _append(lore_root, all_turns)
    client = _Client(
        [
            {"boundaries": []},
            {"facts": [_fact_item("done", "The publish gate landed.", 4, thread="gate")]},
            {"headline": "The publish gate landed."},
        ]
    )

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )

    assert outcome.note_path.stem.endswith("the-publish-gate-landed")
    fm = parse_frontmatter(outcome.note_path.read_text())
    assert fm["title"] == "proj:x: The publish gate landed"
    assert fm["headline"] == "The publish gate landed."


def test_same_minute_rename_collision_gets_numeric_suffix(tmp_path, monkeypatch):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    # Force both buffers into the same minute so their rename targets collide.
    monkeypatch.setattr(
        "lore_curator.buffer_store._now_iso",
        lambda: "2026-05-01T14:32:00+00:00",
    )

    def _close(tid: str) -> Path:
        turns = _turns(0, 12)
        buf = _append(lore_root, turns, tid=tid)
        return synth_and_close(
            buf.sidecar_path,
            lore_root=lore_root,
            wiki_root=wiki_root,
            llm_client=_Client(
                [
                    {"boundaries": []},
                    {"facts": [_fact_item("done", "Shared topic.", 2)]},
                    {"headline": "Shared Topic"},
                ]
            ),
            model="m",
            adapter_lookup=_lookup(_Adapter(turns)),
            auto_commit=False,
        ).note_path

    note_a = _close("abc")
    note_b = _close("def")

    assert note_a != note_b
    assert note_a.name.endswith("-shared-topic.md")
    assert note_b.name.endswith("-shared-topic-2.md")
    assert note_a.exists()
    assert note_b.exists()


# -------------------------------------------------------------------------
# End-mode close: segment -> extract -> render (PRD 0008)
#
# At close no chapter is composed. The session is segmented, each chunk is
# extracted into typed facts, and the note body is rendered from the ledger
# by code — the model never writes the note.
# -------------------------------------------------------------------------


def test_session_end_extracts_typed_facts_and_renders_the_note(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 12)
    buf = _append(lore_root, all_turns)
    client = _Client(
        [
            {"boundaries": []},  # segmentation: one beat
            {
                "facts": [
                    _fact_item("progress", "Patched the buffer lock.", 2, thread="flush"),
                    _fact_item("done", "The flush race is fixed.", 4, thread="flush"),
                    _fact_item("finding", "The reaper races the cap-trip.", 6, thread="flush"),
                ]
            },
            {"headline": "The flush race is fixed."},
        ]
    )

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
    # The ledger holds one facts chapter spanning the chunk — no prose chapter.
    assert [c["kind"] for c in view.chapters] == ["facts"]
    assert view.chapters[0]["from_turn"] == 0 and view.chapters[0]["to_turn"] == 12
    # The body is the render: headline, sections, ledger below.
    assert "**The flush race is fixed.**" in view.body
    # No refs on these facts, so code stamps them as session talk (never as fact).
    assert "- Reported done in session, recorded nowhere: The flush race is fixed. @4" in view.body
    assert "- Observed in session: The reaper races the cap-trip. @6" in view.body
    assert view.body.index("## Done") < view.body.index("## Ledger")
    # Suppression is render-time only: absent from the note, whole in the ledger.
    assert "Reported in session: Patched the buffer lock." not in view.body
    assert [f.text for f in nd.read_facts(outcome.note_path)] == [
        "Patched the buffer lock.",
        "The flush race is fixed.",
        "The reaper races the cap-trip.",
    ]
    assert view.closed is True
    assert not buf.sidecar_path.exists()


def test_a_chunk_that_cannot_be_extracted_becomes_a_coverage_gap(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 12)
    buf = _append(lore_root, all_turns)
    client = _Client(
        [
            {"boundaries": [7]},  # two chunks: 0-6 and 7-12
            {"facts": [_fact_item("done", "The lock landed.", 2, thread="lock")]},
            None,  # chunk 2: the model fails ...
            None,  # ... and fails its one corrective retry
            {"headline": "The lock landed."},
        ]
    )

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )

    assert outcome.status == "composed"  # one chunk failing never loses the rest
    view = nd.read_note(outcome.note_path)
    assert [c["kind"] for c in view.chapters] == ["facts", "marker"]
    marker = view.chapters[1]
    assert marker["marker"] == nd.MARKER_FAILED
    assert marker["from_turn"] == 7 and marker["to_turn"] == 12
    gaps = [ln for ln in view.body.splitlines() if ln.startswith("- Coverage gap:")]
    assert len(gaps) == 1
    assert "turns 7–12" in gaps[0]
    assert view.closed is True


def test_session_end_with_no_facts_leaves_no_note(tmp_path):
    # Every chunk answers "nothing worth recording": the stub is removed
    # rather than closed around an empty render.
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    all_turns = _turns(0, 12)
    buf = _append(lore_root, all_turns)
    client = _Client([{"boundaries": []}, {"facts": []}])

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )

    assert outcome.status == "empty"
    assert outcome.discarded is True
    assert outcome.closed is True
    assert not list((wiki_root / "sessions").rglob("*.md"))
    assert len(client.messages.calls) == 2  # no headline call without facts


def test_reopened_session_re_renders_the_note_over_the_grown_ledger(tmp_path):
    # ADR 0001 reopen, end-mode: the second close appends to the same ledger
    # and rewrites the body from all of it — one note, one rendered reading.
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    adapter = _Adapter(_turns(0, 20))

    _append(lore_root, _turns(0, 12))
    buf = Buffer.open(lore_root, transcript_id="abc", local_date="2026-05-01")
    out1 = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client(
            [
                {"boundaries": []},
                {"facts": [_fact_item("done", "The lock landed.", 4, thread="lock")]},
                {"headline": "The lock landed."},
            ]
        ),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    note_path = out1.note_path
    assert nd.is_closed(note_path) is True

    _append(lore_root, _turns(13, 20))  # the session resumes: note reopened
    assert nd.is_closed(note_path) is False
    synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client(
            [
                {"boundaries": []},
                {"facts": [_fact_item("done", "The renderer landed.", 15, thread="render")]},
                {"headline": "The lock and the renderer landed."},
            ]
        ),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )

    assert list((wiki_root / "sessions").rglob("*.md")) == [note_path]
    view = nd.read_note(note_path)
    assert [c["kind"] for c in view.chapters] == ["facts", "facts"]
    assert view.body.count("## Done") == 1  # one rendered reading, not two
    assert "recorded nowhere: The lock landed. @4" in view.body
    assert "recorded nowhere: The renderer landed. @15" in view.body
    assert "**The lock and the renderer landed.**" in view.body
    assert view.closed is True


def test_the_close_path_verifies_refs_against_the_repo_the_session_ran_in(tmp_path):
    """A real commit earns its check mark; an invented one demotes its line."""
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    subprocess.run(["git", "init", "-q"], cwd=lore_root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=lore_root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=lore_root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"], cwd=lore_root, check=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=lore_root, capture_output=True, text=True, check=True
    ).stdout.strip()[:7]

    all_turns = _turns(0, 12)
    buf = _append(lore_root, all_turns)  # the session's cwd is this repo
    client = _Client(
        [
            {"boundaries": []},
            {
                "facts": [
                    _fact_item(
                        "done",
                        "The seed landed.",
                        2,
                        refs=[{"type": "commit", "value": head}],
                    ),
                    _fact_item(
                        "done",
                        "The phantom landed.",
                        4,
                        refs=[{"type": "commit", "value": "deadbeefdeadbeef"}],
                    ),
                ]
            },
            {"headline": "The seed landed."},
        ]
    )

    outcome = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=client,
        model="m",
        adapter_lookup=_lookup(_Adapter(all_turns)),
        auto_commit=False,
    )

    body = nd.read_note(outcome.note_path).body
    assert f"- The seed landed. — commit {head} ✓ @2" in body
    assert (
        "- Claimed in session, ref not found: The phantom landed."
        " — commit deadbeefdeadbeef (not found) @4" in body
    )
