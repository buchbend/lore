"""Throttle on the per-search reindex call — Phase 7.

Backstory: ``handle_search`` calls ``backend.reindex(wiki=...)`` before
every search. Reindex is already incremental (sha-compare per file
against the cached catalog), but still walks the wiki directory tree
and stats every note on each call. Bursty agent traffic (Claude
firing 5-10 ``lore_search`` calls during a context gather) re-walks
the same N notes for each one.

Phase 7 added a per-MCP-process time-based throttle: subsequent
reindex calls within ``_REINDEX_THROTTLE_S`` seconds are skipped.
This pins the contract.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_throttle_cache() -> None:
    """Each test starts with an empty throttle cache so order doesn't matter."""
    from lore_mcp import server

    server._reindex_last_seen.clear()


def test_first_call_invokes_reindex() -> None:
    """Baseline — the throttle is opt-in: first call always reindexes."""
    from lore_mcp.server import _maybe_reindex

    backend = MagicMock()
    _maybe_reindex(backend, wiki="private")
    backend.reindex.assert_called_once_with(wiki="private")


def test_second_call_within_window_skips() -> None:
    """Two back-to-back searches → only one reindex."""
    from lore_mcp.server import _maybe_reindex

    backend = MagicMock()
    _maybe_reindex(backend, wiki="private")
    _maybe_reindex(backend, wiki="private")
    assert backend.reindex.call_count == 1


def test_call_after_window_reindexes_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the throttle window expires, the next call re-walks."""
    from lore_mcp import server

    backend = MagicMock()
    server._maybe_reindex(backend, wiki="private")
    # Force time to jump past the throttle window.
    fake_now = time.monotonic() + server._REINDEX_THROTTLE_S + 1.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)
    server._maybe_reindex(backend, wiki="private")
    assert backend.reindex.call_count == 2


def test_throttle_is_per_wiki() -> None:
    """A reindex of wiki A doesn't suppress a reindex of wiki B in the
    same window — different keys, different timestamps."""
    from lore_mcp.server import _maybe_reindex

    backend = MagicMock()
    _maybe_reindex(backend, wiki="private")
    _maybe_reindex(backend, wiki="ccat")
    assert backend.reindex.call_count == 2


def test_none_wiki_is_its_own_throttle_key() -> None:
    """``wiki=None`` (multi-wiki search) shares no throttle slot with
    any specific wiki — same call shape, but the cache key differs."""
    from lore_mcp.server import _maybe_reindex

    backend = MagicMock()
    _maybe_reindex(backend, wiki=None)
    _maybe_reindex(backend, wiki="private")
    assert backend.reindex.call_count == 2
    _maybe_reindex(backend, wiki=None)  # within window for None
    assert backend.reindex.call_count == 2


def test_handle_search_uses_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: two ``handle_search`` calls in quick succession only
    trigger one reindex."""
    from lore_mcp import server

    backend = MagicMock()
    backend.search.return_value = []
    monkeypatch.setattr(server, "FtsBackend", lambda: backend)

    server.handle_search("query a", wiki="private")
    server.handle_search("query b", wiki="private")
    assert backend.reindex.call_count == 1
    assert backend.search.call_count == 2
