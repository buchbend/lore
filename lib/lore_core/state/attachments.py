"""Attachments file: host-local truth for which cwd paths route to which wiki/scope.

Sidecar JSON at ``$LORE_ROOT/.lore/attachments.json``. Paths are absolute
and resolved. Not portable between hosts. Not regenerable from any other
artifact — this is the record of what the user actually consented to.

Resolution is by longest-prefix match on attachment paths — no filesystem
walk-up.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lore_core.io import atomic_write_text


@dataclass
class Attachment:
    path: Path
    wiki: str
    scope: str
    attached_at: datetime
    source: str = "manual"
    offer_fingerprint: str | None = None


@dataclass
class Declined:
    path: Path
    offer_fingerprint: str


class AttachmentsFile:
    """Sidecar at ``<lore_root>/.lore/attachments.json``.

    In-memory model: two lists (attachments + declined). Load once,
    mutate, save atomically. Not thread-safe — callers serialise via the
    curator lockfile when needed.
    """

    def __init__(self, lore_root: Path) -> None:
        self._lore_root = lore_root
        self._path = lore_root / ".lore" / "attachments.json"
        self._attachments: list[Attachment] = []
        self._declined: list[Declined] = []
        self._loaded = False

    # ---- load/save ----

    def load(self) -> None:
        """Idempotent: safe to call repeatedly."""
        self._attachments = []
        self._declined = []
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for entry in raw.get("attachments", []):
            self._attachments.append(_attachment_from_raw(entry))
        for entry in raw.get("declined", []):
            self._declined.append(_declined_from_raw(entry))

    def save(self) -> None:
        raw = {
            "attachments": [_attachment_to_raw(a) for a in self._attachments],
            "declined": [_declined_to_raw(d) for d in self._declined],
        }
        atomic_write_text(self._path, json.dumps(raw, indent=2))

    # ---- read ----

    def all(self) -> list[Attachment]:
        self._ensure_loaded()
        return list(self._attachments)

    def get(self, path: Path) -> Attachment | None:
        """Exact-match lookup; returns None if path isn't registered."""
        self._ensure_loaded()
        normalised = _normalise_path(path)
        for a in self._attachments:
            if a.path == normalised:
                return a
        return None

    def longest_prefix_match(self, cwd: Path) -> Attachment | None:
        """Return the most-specific attachment whose path is an ancestor
        of (or equal to) ``cwd``. None if no attachment covers ``cwd``.
        """
        self._ensure_loaded()
        cwd = _normalise_path(cwd)
        matches = [
            a for a in self._attachments
            if cwd == a.path or _is_subpath(cwd, a.path)
        ]
        if not matches:
            return None
        return max(matches, key=lambda a: len(a.path.parts))

    # ---- write ----

    def add(self, attachment: Attachment) -> None:
        """Upsert by path (exact match)."""
        self._ensure_loaded()
        normalised = Attachment(
            path=_normalise_path(attachment.path),
            wiki=attachment.wiki,
            scope=attachment.scope,
            attached_at=attachment.attached_at,
            source=attachment.source,
            offer_fingerprint=attachment.offer_fingerprint,
        )
        self._attachments = [a for a in self._attachments if a.path != normalised.path]
        self._attachments.append(normalised)

    def remove(self, path: Path) -> bool:
        """Remove by path. Returns True if an entry was removed."""
        self._ensure_loaded()
        normalised = _normalise_path(path)
        before = len(self._attachments)
        self._attachments = [a for a in self._attachments if a.path != normalised]
        return len(self._attachments) != before

    def decline(self, path: Path, offer_fingerprint: str) -> None:
        """Record a declined offer. Path + fingerprint pair is the key."""
        self._ensure_loaded()
        normalised = _normalise_path(path)
        self._declined = [
            d for d in self._declined
            if not (d.path == normalised and d.offer_fingerprint == offer_fingerprint)
        ]
        self._declined.append(Declined(path=normalised, offer_fingerprint=offer_fingerprint))

    def is_declined(self, path: Path, offer_fingerprint: str) -> bool:
        self._ensure_loaded()
        normalised = _normalise_path(path)
        return any(
            d.path == normalised and d.offer_fingerprint == offer_fingerprint
            for d in self._declined
        )

    def rewrite_scopes(self, mapping: dict[str, str]) -> int:
        """Apply a scope-rename mapping to attachment rows.

        For each attachment whose ``scope`` is a key in ``mapping``,
        rewrite it to the mapped value. Returns the number of rows
        changed. Caller is responsible for saving.
        """
        self._ensure_loaded()
        changed = 0
        for i, a in enumerate(self._attachments):
            if a.scope in mapping:
                self._attachments[i] = Attachment(
                    path=a.path,
                    wiki=a.wiki,
                    scope=mapping[a.scope],
                    attached_at=a.attached_at,
                    source=a.source,
                    offer_fingerprint=a.offer_fingerprint,
                )
                changed += 1
        return changed

    # ---- internals ----

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()


# ---- fingerprinting ----

def fingerprint_of(offer_fields: dict[str, Any]) -> str:
    """Stable SHA256 over routing-relevant fields of an offer.

    Uses ``json.dumps(..., sort_keys=True)`` so key ordering and
    whitespace in the source YAML don't affect the fingerprint.
    """
    canonical = json.dumps(offer_fields, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest}"


# ---- helpers ----

def _normalise_path(p: Path) -> Path:
    """Resolve to absolute. Symlinks are resolved when the path exists;
    non-existent paths are absolute-joined against cwd but not probed
    (matches walk-up resolver's behaviour on tmp paths)."""
    if p.exists():
        return p.resolve()
    return Path(p).absolute()


def _is_subpath(child: Path, parent: Path) -> bool:
    """True if ``child`` is a proper descendant of ``parent``."""
    return child.is_relative_to(parent) and child != parent


def _attachment_to_raw(a: Attachment) -> dict[str, Any]:
    return {
        "path": str(a.path),
        "wiki": a.wiki,
        "scope": a.scope,
        "attached_at": a.attached_at.isoformat(),
        "source": a.source,
        "offer_fingerprint": a.offer_fingerprint,
    }


def _attachment_from_raw(raw: dict[str, Any]) -> Attachment:
    return Attachment(
        path=Path(raw["path"]),
        wiki=raw["wiki"],
        scope=raw["scope"],
        attached_at=_parse_dt(raw["attached_at"]),
        source=raw.get("source", "manual"),
        offer_fingerprint=raw.get("offer_fingerprint"),
    )


def _declined_to_raw(d: Declined) -> dict[str, Any]:
    return {"path": str(d.path), "offer_fingerprint": d.offer_fingerprint}


def _declined_from_raw(raw: dict[str, Any]) -> Declined:
    return Declined(path=Path(raw["path"]), offer_fingerprint=raw["offer_fingerprint"])


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
