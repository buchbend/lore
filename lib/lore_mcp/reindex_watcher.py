"""Filesystem watcher that invalidates the MCP search reindex throttle.

Runs as a daemon thread inside the long-lived MCP server process (one
per Claude session). When a wiki ``.md`` file is created, modified, or
deleted, the watcher marks that wiki as dirty so the next
``lore_search`` call reindexes from disk instead of returning cached
hits.

Without this:
  - Host B pulls Host A's new commits (auto_pull at SessionStart) but
    Host B's MCP server doesn't know — its 5s throttle keeps serving
    stale results until the throttle naturally expires.

With this:
  - The fetch+ff updates `<lore_root>/wiki/<x>/...` mtimes; the watcher
    tags wiki ``x`` dirty within ~50ms; the next search reindexes.

``watchdog`` is an optional dependency (in the ``[search]`` extras). If
it's not installed, ``start_watcher()`` returns ``None`` and the rest
of the system falls back to throttle-only invalidation — same behaviour
as 0.10.x.

This is also Lore's first daemon thread inside the MCP server. The
pattern (MCP-daemon for fast-path, hooks for correctness) is documented
in ``docs/architecture/sync.md`` — keep that property when extending
this module.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class ReindexDirtyState:
    """Process-local dirty bookkeeping. One instance per MCP server.

    Thread-safe: the watcher daemon writes; the search-handler thread
    reads + clears. Both go through the lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dirty: set[str] = set()

    def mark_dirty(self, wiki: str) -> None:
        with self._lock:
            self._dirty.add(wiki)

    def is_dirty(self, wiki: str) -> bool:
        with self._lock:
            return wiki in self._dirty

    def take(self, wiki: str) -> bool:
        """Return True iff ``wiki`` was dirty; clear in either case.

        The "test-and-clear" idiom — caller is about to reindex.
        """
        with self._lock:
            if wiki in self._dirty:
                self._dirty.discard(wiki)
                return True
            return False

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._dirty)


def _wiki_for_path(path: Path, wiki_root: Path) -> str | None:
    """Return the wiki name a path lives under, or None if outside."""
    try:
        rel = path.resolve().relative_to(wiki_root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    return parts[0] if parts else None


def _make_handler(state: ReindexDirtyState, wiki_root: Path):
    """Build a watchdog event handler bound to ``state`` + ``wiki_root``.

    Lazy import so this module is import-clean even when ``watchdog`` is
    not installed; only ``start_watcher`` ever asks for it.
    """
    from watchdog.events import FileSystemEventHandler  # local import

    class _Handler(FileSystemEventHandler):
        def _maybe_mark(self, src_path: str) -> None:
            p = Path(src_path)
            if p.suffix != ".md":
                return
            wiki = _wiki_for_path(p, wiki_root)
            if wiki is not None:
                state.mark_dirty(wiki)

        def on_created(self, event):
            if not event.is_directory:
                self._maybe_mark(event.src_path)

        def on_modified(self, event):
            if not event.is_directory:
                self._maybe_mark(event.src_path)

        def on_deleted(self, event):
            if not event.is_directory:
                self._maybe_mark(event.src_path)

        def on_moved(self, event):
            if not event.is_directory:
                # A move that lands a .md inside a wiki should also dirty.
                self._maybe_mark(getattr(event, "dest_path", event.src_path))

    return _Handler()


def start_watcher(lore_root: Path, state: ReindexDirtyState):
    """Start a daemon Observer watching ``<lore_root>/wiki/`` recursively.

    Returns the started ``Observer`` (callers can ``.stop()`` it; daemon
    threads also exit with the process). Returns ``None`` if
    ``watchdog`` is not installed or the wiki dir doesn't exist —
    callers must handle that gracefully (the throttle is the fallback).
    """
    wiki_root = lore_root / "wiki"
    if not wiki_root.is_dir():
        return None

    try:
        from watchdog.observers import Observer  # local import — optional dep
    except ImportError:
        return None

    handler = _make_handler(state, wiki_root)
    observer = Observer()
    observer.daemon = True
    observer.schedule(handler, str(wiki_root), recursive=True)
    observer.start()
    return observer
