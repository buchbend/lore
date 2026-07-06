"""Reopen + continue: a resumed session continues its existing note.

One session, one note. When a session is closed — a false liveness reap, or a
genuine close followed by an editor restart / ``/compact`` / idle-then-return —
its buffer sidecar is archived to ``_done/``. The next heartbeat on the same
stem must reattach to that archived buffer, reopen its (closed) note, and
append the new chapter to the **same** file instead of minting an unlinked,
partly-duplicated sibling.

Every LLM interaction is faked with the stub-replay harness; no test asserts
prose quality and no LLM-as-judge appears.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from lore_core import note_document as nd
from lore_core.note_document import Chapter, TopicBlock
from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.buffer_store import (
    Buffer,
    Counters,
    LastSeen,
    Sidecar,
    _stem_for,
    done_dir,
)
from lore_curator.chapter_flush import synth_and_close

# ---------------------------------------------------------------------------
# Fakes (stub-replay compose harness)
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


# The concrete pilot fixture: transcript a737ff12, 2026-07-04, split at a
# /compact into 04-0605 (turns 1095-1383) and 04-0608 (turns 1384-1430).
TID = "a737ff12"
DATE = "2026-07-04"


def _turns(lo: int, hi: int) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text=f"line {i}")
        for i in range(lo, hi + 1)
    ]


def _lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    (tmp_path / "wiki" / "private" / "sessions").mkdir(parents=True)
    return tmp_path


def _append(lore_root: Path, turns: list[Turn], *, tid: str = TID, date: str = DATE):
    return append_chunk(
        lore_root=lore_root,
        chunk_turns=turns,
        local_date=date,
        transcript_id=tid,
        integration="fake",
        wiki="private",
        scope="proj:x",
        cwd=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        cfg=WikiConfig(),
    )


def _seed_archived_closed_note(
    lore_root: Path,
    *,
    tid: str,
    date: str,
    chapter_lead: str,
    from_turn: int,
    to_turn: int,
    stub_path: Path | None = None,
    write_note: bool = True,
) -> tuple[Path, str]:
    """Seed a closed session note plus its archived (``_done/``) buffer.

    Mirrors the on-disk state left by a session that already composed one
    chapter and closed: a ``note_status: closed`` note file and a
    ``_done/<stem>.state.json`` sidecar (state ``closed``) pointing at it.
    """
    wiki_root = lore_root / "wiki" / "private"
    stem = _stem_for(tid, date)
    note_path = stub_path or (wiki_root / "sessions" / f"{tid}-first.md")
    if write_note:
        nd.create_note(
            note_path,
            title="Session note",
            description="Lab-notebook session note.",
            scope="proj:x",
            extra_frontmatter={"transcript_id": tid, "integration": "fake", "buffer_stem": stem},
        )
        nd.append_chapter(
            note_path,
            Chapter(blocks=[TopicBlock(lead=chapter_lead, body="prose.", anchor_turn=from_turn)]),
            slice_from_turn=from_turn,
            slice_to_turn=to_turn,
        )
        nd.close_note(note_path)
    sc = Sidecar(
        transcript_id=tid,
        local_date=date,
        integration="fake",
        wiki="private",
        scope="proj:x",
        cwd=str(lore_root),
        handle="",
        state="closed",
        stub_path=str(note_path),
        counters=Counters(),
        last_seen=LastSeen(content_hash="h", index_hint=to_turn),
    )
    done = done_dir(lore_root)
    (done / f"{stem}.state.json").write_text(json.dumps(sc.to_dict(), default=str))
    (done / f"{stem}.jsonl").write_text("")
    return note_path, stem


# ---------------------------------------------------------------------------
# reopen_note primitive (unit)
# ---------------------------------------------------------------------------


def test_reopen_note_flips_closed_to_open_and_allows_append(tmp_path):
    path = tmp_path / "note.md"
    nd.create_note(path, title="t", description="d", scope="proj:x")
    nd.append_chapter(
        path,
        Chapter(blocks=[TopicBlock(lead="First finding", body="a", anchor_turn=1)]),
        slice_from_turn=0,
        slice_to_turn=2,
    )
    nd.close_note(path)
    assert nd.is_closed(path) is True
    # A closed note refuses appends today.
    with pytest.raises(nd.NoteClosedError):
        nd.append_chapter(
            path,
            Chapter(blocks=[TopicBlock(lead="blocked", body="x", anchor_turn=3)]),
            slice_from_turn=3,
            slice_to_turn=4,
        )

    nd.reopen_note(path)
    assert nd.is_closed(path) is False

    n = nd.append_chapter(
        path,
        Chapter(blocks=[TopicBlock(lead="Second finding", body="b", anchor_turn=3)]),
        slice_from_turn=3,
        slice_to_turn=4,
    )
    assert n == 2
    view = nd.read_note(path)
    assert len([c for c in view.chapters if c.get("kind") == "topic"]) == 2
    assert "First finding" in view.body and "Second finding" in view.body


def test_reopen_note_idempotent_on_open_note(tmp_path):
    path = tmp_path / "note.md"
    nd.create_note(path, title="t", description="d", scope="proj:x")
    nd.append_chapter(
        path,
        Chapter(blocks=[TopicBlock(lead="Only finding", body="a", anchor_turn=1)]),
        slice_from_turn=0,
        slice_to_turn=2,
    )
    before = path.read_text()
    # Reopening an already-open note is a no-op — no raise, byte-identical file.
    nd.reopen_note(path)
    assert nd.is_closed(path) is False
    assert path.read_text() == before


# ---------------------------------------------------------------------------
# Reattach: a resumed session continues the existing note (one file)
# ---------------------------------------------------------------------------


def test_resumed_session_reattaches_and_appends_to_same_note(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    adapter = _Adapter(_turns(0, 20))

    # Session 1: accumulate, compose one chapter, close (archives the buffer).
    _append(lore_root, _turns(0, 12))
    buf = Buffer.open(lore_root, transcript_id=TID, local_date=DATE)
    stem = buf.stem
    out1 = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client([_chapter_payload("Recorded the publish gate", "gate prose.", 4)]),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    note_path = out1.note_path
    assert nd.is_closed(note_path) is True
    assert list((wiki_root / "sessions").rglob("*.md")) == [note_path]
    assert not buf.sidecar_path.exists()
    assert (done_dir(lore_root) / f"{stem}.state.json").exists()

    # Session 2 resumes on the same stem after the close.
    out2 = _append(lore_root, _turns(13, 20))
    assert out2.is_new_buffer is False  # reattached, not a fresh buffer
    assert buf.sidecar_path.exists()  # buffer restored out of _done/
    assert buf.read_sidecar().stub_path == str(note_path)  # same note pointer
    assert nd.is_closed(note_path) is False  # note reopened
    assert not (done_dir(lore_root) / f"{stem}.state.json").exists()  # archive moved back

    # Flush the continuation and close again.
    out3 = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client(
            [_chapter_payload("Recorded the essence rewrite", "essence prose.", 15)]
        ),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    assert out3.status == "composed"
    # Still exactly one note file — no sibling minted.
    assert list((wiki_root / "sessions").rglob("*.md")) == [note_path]
    view = nd.read_note(note_path)
    topic = [c for c in view.chapters if c.get("kind") == "topic"]
    assert len(topic) == 2
    assert topic[0]["from_turn"] == 0 and topic[0]["to_turn"] == 12
    assert topic[1]["from_turn"] == 13 and topic[1]["to_turn"] == 20
    assert view.closed is True


def test_continued_compose_receives_existing_body_as_note_so_far(tmp_path):
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    adapter = _Adapter(_turns(0, 20))

    _append(lore_root, _turns(0, 12))
    buf = Buffer.open(lore_root, transcript_id=TID, local_date=DATE)
    out1 = synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client(
            [_chapter_payload("Recorded the publish gate design", "unique-marker-alpha.", 4)]
        ),
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    assert out1.note_path is not None

    _append(lore_root, _turns(13, 20))
    c2 = _Client([_chapter_payload("Recorded the continuation", "beta prose.", 15)])
    synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=c2,
        model="m",
        adapter_lookup=_lookup(adapter),
        auto_commit=False,
    )
    # The continuation compose was seeded with the existing body, so the
    # first chapter is present in note-so-far and never re-narrated.
    prompt = c2.messages.calls[0]["messages"][0]["content"]
    assert "Recorded the publish gate design" in prompt
    assert "unique-marker-alpha" in prompt


def test_0605_0608_split_reproduced_as_single_note(tmp_path):
    # The concrete pilot failure: transcript a737ff12 filed 04-0605 (turns
    # 1095-1383) then, after a /compact, an unlinked 04-0608 (turns
    # 1384-1430). With reopen+continue the resume reattaches — one note.
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    note_path, stem = _seed_archived_closed_note(
        lore_root,
        tid=TID,
        date=DATE,
        chapter_lead="Recorded the publish gate",
        from_turn=1095,
        to_turn=1383,
    )
    assert list((wiki_root / "sessions").rglob("*.md")) == [note_path]

    out = _append(lore_root, _turns(1384, 1430))
    assert out.is_new_buffer is False
    assert nd.is_closed(note_path) is False  # reopened, not a new file

    buf = Buffer.open(lore_root, transcript_id=TID, local_date=DATE)
    synth_and_close(
        buf.sidecar_path,
        lore_root=lore_root,
        wiki_root=wiki_root,
        llm_client=_Client(
            [_chapter_payload("Recorded record-the-work-not-the-working", "essence prose.", 1400)]
        ),
        model="m",
        adapter_lookup=_lookup(_Adapter(_turns(1384, 1430))),
        auto_commit=False,
    )
    # The split is healed: still exactly one note file, now two chapters.
    assert list((wiki_root / "sessions").rglob("*.md")) == [note_path]
    view = nd.read_note(note_path)
    topic = [c for c in view.chapters if c.get("kind") == "topic"]
    assert len(topic) == 2
    assert topic[0]["to_turn"] == 1383
    assert topic[1]["from_turn"] == 1384 and topic[1]["to_turn"] == 1430


def test_resume_after_discarded_note_starts_fresh(tmp_path):
    # A prior session was discarded (trivial / empty): its note file was
    # removed but the buffer archived with a dangling stub_path. Resuming
    # must not crash on the missing file; it clears the pointer so a fresh
    # note is created for the continuing session.
    lore_root = _lore_root(tmp_path)
    wiki_root = lore_root / "wiki" / "private"
    missing = wiki_root / "sessions" / "gone.md"
    _seed_archived_closed_note(
        lore_root,
        tid=TID,
        date=DATE,
        chapter_lead="unused",
        from_turn=0,
        to_turn=1,
        stub_path=missing,
        write_note=False,  # note file never exists on disk
    )

    out = _append(lore_root, _turns(0, 4))
    assert out.is_new_buffer is False
    buf = Buffer.open(lore_root, transcript_id=TID, local_date=DATE)
    assert buf.sidecar_path.exists()  # buffer restored, no crash
    assert buf.read_sidecar().stub_path == ""  # dangling pointer cleared
