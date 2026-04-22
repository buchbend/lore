"""Scopes file: flat ID-as-path scope tree under ``$LORE_ROOT/.lore/scopes.json``.

Scope IDs are colon-separated and encode the parent chain
(``ccat:data-center:computers`` has parent ``ccat:data-center``, root
``ccat``). The file stores a flat dict ``{scope_id: ScopeEntry}``.
Parent pointers are derived from IDs, not stored.

Wiki assignment at root, inherited by descendants via the nearest
ancestor with a ``wiki`` field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lore_core.io import atomic_write_text


@dataclass
class ScopeEntry:
    label: str | None = None
    wiki: str | None = None
    description: str | None = None


def parent_of(scope_id: str) -> str | None:
    """Return the parent scope ID, or None if ``scope_id`` is a root."""
    if ":" not in scope_id:
        return None
    return scope_id.rsplit(":", 1)[0]


def ancestors_of(scope_id: str) -> list[str]:
    """Root-to-self chain: ``ccat:x:y`` → ``[ccat, ccat:x, ccat:x:y]``."""
    parts = scope_id.split(":")
    return [":".join(parts[: i + 1]) for i in range(len(parts))]


class ScopesFile:
    """Sidecar at ``<lore_root>/.lore/scopes.json``.

    Regenerable: delete the file and it rebuilds as offers are
    re-accepted (Phase 5 rebuild pass). Represents the union of
    accepted offers' scope chains.
    """

    def __init__(self, lore_root: Path) -> None:
        self._lore_root = lore_root
        self._path = lore_root / ".lore" / "scopes.json"
        self._scopes: dict[str, ScopeEntry] = {}
        self._loaded = False

    # ---- load/save ----

    def load(self) -> None:
        self._scopes = {}
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for sid, entry in raw.get("scopes", {}).items():
            self._scopes[sid] = _entry_from_raw(entry)

    def save(self) -> None:
        raw = {"scopes": {sid: _entry_to_raw(e) for sid, e in self._scopes.items()}}
        atomic_write_text(self._path, json.dumps(raw, indent=2))

    # ---- read ----

    def get(self, scope_id: str) -> ScopeEntry | None:
        self._ensure_loaded()
        return self._scopes.get(scope_id)

    def all_ids(self) -> list[str]:
        self._ensure_loaded()
        return list(self._scopes.keys())

    def resolve_wiki(self, scope_id: str) -> str | None:
        """Walk ancestors (leaf → root) and return the nearest ``wiki``."""
        self._ensure_loaded()
        for sid in reversed(ancestors_of(scope_id)):
            entry = self._scopes.get(sid)
            if entry and entry.wiki:
                return entry.wiki
        return None

    def descendants(self, scope_id: str) -> list[str]:
        """IDs of strict descendants (not ``scope_id`` itself)."""
        self._ensure_loaded()
        prefix = scope_id + ":"
        return sorted(sid for sid in self._scopes if sid.startswith(prefix))

    # ---- write ----

    def ingest_chain(self, scope_id: str, wiki: str) -> list[str]:
        """Backfill the ancestor chain for ``scope_id``.

        Creates any missing ancestors. The root (first segment) gets the
        ``wiki`` pointer if the ancestor doesn't already have one;
        descendants inherit via :meth:`resolve_wiki` and carry no ``wiki``
        field of their own.

        Returns the list of newly-created scope IDs (root → leaf order).
        Raises :class:`ScopeConflict` if the root already has a different
        wiki than ``wiki``.
        """
        self._ensure_loaded()
        created: list[str] = []
        chain = ancestors_of(scope_id)
        root = chain[0]
        existing_root = self._scopes.get(root)
        if existing_root and existing_root.wiki and existing_root.wiki != wiki:
            raise ScopeConflict(
                scope_root=root,
                existing_wiki=existing_root.wiki,
                incoming_wiki=wiki,
            )
        for sid in chain:
            if sid not in self._scopes:
                entry = ScopeEntry()
                if sid == root:
                    entry.wiki = wiki
                self._scopes[sid] = entry
                created.append(sid)
            elif sid == root and not self._scopes[sid].wiki:
                # Root existed without a wiki (e.g. created as a middle
                # node of another chain that was later reparented).
                # Adopt the incoming wiki.
                self._scopes[sid].wiki = wiki
        return created

    def set_entry(self, scope_id: str, entry: ScopeEntry) -> None:
        """Overwrite or insert a full entry. Caller is responsible for
        consistency (use :meth:`ingest_chain` for normal creation)."""
        self._ensure_loaded()
        self._scopes[scope_id] = entry

    def rename(self, old_id: str, new_id: str) -> list[tuple[str, str]]:
        """Rename ``old_id`` and all its descendants, preserving their
        relative structure. Returns ``[(old, new), ...]`` for every
        renamed entry.

        Raises :class:`KeyError` if ``old_id`` isn't present.
        """
        self._ensure_loaded()
        if old_id not in self._scopes:
            raise KeyError(f"No scope entry for {old_id!r}")
        rewrites: list[tuple[str, str]] = []
        affected = [old_id, *self.descendants(old_id)]
        for sid in affected:
            suffix = sid[len(old_id):]  # empty for old_id itself; ":x" etc. otherwise
            new_sid = new_id + suffix
            rewrites.append((sid, new_sid))
        # Apply in a new dict to avoid in-place collisions
        new_scopes: dict[str, ScopeEntry] = {}
        for sid, entry in self._scopes.items():
            if sid in affected:
                suffix = sid[len(old_id):]
                new_scopes[new_id + suffix] = entry
            else:
                new_scopes[sid] = entry
        self._scopes = new_scopes
        return rewrites

    def reparent(self, scope_id: str, new_parent: str) -> list[tuple[str, str]]:
        """Move ``scope_id`` (and descendants) under ``new_parent``.

        The leaf segment is preserved: ``ccat:data-center`` reparented
        under ``infra`` becomes ``infra:data-center``.

        Raises :class:`KeyError` if ``scope_id`` is absent.
        """
        if ":" in scope_id:
            leaf = scope_id.rsplit(":", 1)[1]
        else:
            leaf = scope_id
        new_id = f"{new_parent}:{leaf}" if new_parent else leaf
        return self.rename(scope_id, new_id)

    def remove(self, scope_id: str) -> None:
        """Remove a leaf. Raises :class:`ValueError` if descendants exist."""
        self._ensure_loaded()
        if scope_id not in self._scopes:
            raise KeyError(f"No scope entry for {scope_id!r}")
        if self.descendants(scope_id):
            raise ValueError(
                f"Cannot remove {scope_id!r}: has descendants "
                f"(use rename/reparent or remove children first)"
            )
        del self._scopes[scope_id]

    # ---- internals ----

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()


class ScopeConflict(Exception):
    """Raised when ingesting an offer whose root scope already belongs
    to a different wiki. Caller must prompt for resolution."""

    def __init__(self, *, scope_root: str, existing_wiki: str, incoming_wiki: str) -> None:
        super().__init__(
            f"scope root {scope_root!r} is already assigned to wiki "
            f"{existing_wiki!r}; incoming offer wants {incoming_wiki!r}"
        )
        self.scope_root = scope_root
        self.existing_wiki = existing_wiki
        self.incoming_wiki = incoming_wiki


def _entry_to_raw(e: ScopeEntry) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if e.label is not None:
        raw["label"] = e.label
    if e.wiki is not None:
        raw["wiki"] = e.wiki
    if e.description is not None:
        raw["description"] = e.description
    return raw


def _entry_from_raw(raw: dict[str, Any]) -> ScopeEntry:
    return ScopeEntry(
        label=raw.get("label"),
        wiki=raw.get("wiki"),
        description=raw.get("description"),
    )
