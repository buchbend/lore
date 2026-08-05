"""A session boundary registers transcripts and stops there.

The capture hook used to force-spawn Curator A at session-end and pre-compact
so no pending work was stranded across the boundary. Curator A composed a
session note through an LLM call. With the compose pipeline retired, the
boundary keeps the parts that carry information forward — transcript
registration, the archive, ledger updates — and spawns nothing.

Covers the teardown's first acceptance criterion: when a session ends, lore
writes no session note and spawns no LLM call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


class _FakeAdapter:
    integration = "fake"

    def __init__(self, handles, turns=()):
        self._handles = handles
        self._turns = list(turns)

    def list_transcripts(self, directory):
        return self._handles

    def read_slice(self, handle, from_index=0):
        yield from self._turns

    def read_slice_after_hash(self, *a, **kw):
        yield from ()

    def is_complete(self, handle):
        return True


def _handle(repo: Path):
    from lore_core.types import TranscriptHandle

    return TranscriptHandle(
        integration="fake",
        id="t1",
        path=repo / "t1.jsonl",
        cwd=repo,
        mtime=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )


def _route(lore_root: Path, repo: Path, *, event: str):
    from lore_core.types import Scope
    from lore_curator.capture_routing import route_capture

    scope = Scope(
        wiki="demo",
        scope="demo:proj",
        backend="none",
        claude_md_path=repo / "CLAUDE.md",
    )
    (lore_root / "wiki" / "demo").mkdir(parents=True, exist_ok=True)

    return route_capture(
        lore_root,
        repo,
        scope,
        event=event,
        adapter=_FakeAdapter([_handle(repo)]),
        transcript=None,
    )


def test_route_capture_takes_no_spawn_callable() -> None:
    """The injected spawn seam is gone, so no caller can reintroduce a spawn."""
    import inspect

    from lore_curator.capture_routing import route_capture

    params = inspect.signature(route_capture).parameters
    assert "spawn_curator_a" not in params, (
        f"route_capture still accepts a spawn callable: {list(params)}"
    )


@pytest.mark.parametrize("event", ["session-end", "pre-compact"])
def test_boundary_registers_the_transcript_and_reports_no_spawn(
    tmp_path: Path, event: str
) -> None:
    """The pending entry is still recorded — only the compose spawn is gone."""
    from lore_core.ledger import TranscriptLedger

    lore_root = tmp_path / "vault"
    repo = tmp_path / "proj"
    repo.mkdir(parents=True, exist_ok=True)

    routing = _route(lore_root, repo, event=event)

    assert "spawn" not in routing.outcome, (
        f"{event} reported a spawn outcome: {routing.outcome!r}"
    )

    entry = TranscriptLedger(lore_root).get("fake", "t1")
    assert entry is not None, "capture must still register the transcript"


def test_no_session_note_is_written_at_a_boundary(tmp_path: Path) -> None:
    """Nothing lands under sessions/ as a result of the boundary event."""
    lore_root = tmp_path / "vault"
    repo = tmp_path / "proj"
    repo.mkdir(parents=True, exist_ok=True)

    _route(lore_root, repo, event="session-end")

    sessions = lore_root / "wiki" / "demo" / "sessions"
    written = list(sessions.rglob("*.md")) if sessions.exists() else []
    assert written == [], f"session-end wrote notes: {written}"


def test_the_compose_entry_point_is_gone() -> None:
    """`run_curator_a` was the compose driver; nothing may import it."""
    with pytest.raises(ImportError):
        from lore_curator.session_curator import run_curator_a  # noqa: F401
