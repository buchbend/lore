"""`.lore.yml` — repo-level attachment offer.

An *offer* is a declarative YAML file at a repo root (or any directory)
stating "if you want to route this dir's Lore sessions, here are the
parameters." It does nothing until a host explicitly accepts via
``/lore:attach`` or a one-time SessionStart prompt.

This module is pure — parsing + fingerprinting only. Consent state
classification lives in ``lore_core.consent``; registry writes in
``lore_core.state.attachments``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILENAME = ".lore.yml"

# Routing-relevant fields that participate in the fingerprint.
# Changes to these invalidate prior accept/decline decisions and re-prompt.
# Non-routing fields (issues, prs) are stored but do NOT affect the fingerprint.
_FINGERPRINT_FIELDS: tuple[str, ...] = ("wiki", "scope", "wiki_source")


@dataclass(frozen=True)
class Offer:
    """Parsed `.lore.yml`. ``wiki`` and ``scope`` are required."""

    wiki: str
    scope: str
    backend: str = "none"
    wiki_source: str | None = None
    issues: str | None = None
    prs: str | None = None
    schema_version: int = 1


def parse_lore_yml(path: Path) -> Offer | None:
    """Parse a `.lore.yml` file. Returns ``None`` on any failure —
    offers are best-effort; a malformed file is equivalent to absence.
    """
    if not path.exists() or not path.is_file():
        return None
    try:
        import yaml  # local import; yaml is a soft dependency in tests
        raw = yaml.safe_load(path.read_text())
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    wiki = raw.get("wiki")
    scope = raw.get("scope")
    if not isinstance(wiki, str) or not wiki:
        return None
    if not isinstance(scope, str) or not scope:
        return None

    return Offer(
        wiki=wiki,
        scope=scope,
        backend=_str_or_default(raw.get("backend"), "none"),
        wiki_source=_str_or_none(raw.get("wiki_source")),
        issues=_str_or_none(raw.get("issues")),
        prs=_str_or_none(raw.get("prs")),
        schema_version=int(raw.get("schema_version", 1)),
    )


def find_lore_yml(cwd: Path, *, max_depth: int = 8) -> Path | None:
    """Walk up from ``cwd`` looking for a `.lore.yml` file.

    SessionStart is not a hot path — one walk-up per session start,
    bounded depth. Unlike scope resolution (O(log n) registry lookup),
    offer discovery can tolerate a filesystem walk.
    """
    current = cwd.resolve() if cwd.exists() else cwd.absolute()
    for _ in range(max_depth):
        candidate = current / FILENAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def offer_fingerprint(offer: Offer) -> str:
    """Stable SHA256 over routing-relevant fields of an offer.

    Key ordering and YAML formatting do not affect the fingerprint.
    Only changes to ``wiki``/``scope``/``wiki_source`` invalidate a
    prior accept/decline.
    """
    from lore_core.state.attachments import fingerprint_of

    routing = {
        field: getattr(offer, field) for field in _FINGERPRINT_FIELDS
    }
    return fingerprint_of(routing)


def _str_or_default(v: Any, default: str) -> str:
    if isinstance(v, str) and v:
        return v
    return default


def _str_or_none(v: Any) -> str | None:
    if isinstance(v, str) and v:
        return v
    return None
