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
    """Parsed `.lore.yml`. ``wiki`` and ``scope`` are required.

    ``inherit`` opts in to subtree application: a `.lore.yml` with
    ``inherit: true`` is discovered by walk-up from descendant cwds.
    Default ``False`` means the offer applies only at its own directory.
    Not part of the fingerprint — toggling it must not invalidate prior
    accept/decline decisions.
    """

    wiki: str
    scope: str
    backend: str = "none"
    wiki_source: str | None = None
    issues: str | None = None
    prs: str | None = None
    schema_version: int = 1
    inherit: bool = False


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
        inherit=raw.get("inherit") is True,
    )


def find_lore_yml(cwd: Path, *, max_depth: int = 8) -> tuple[Path, Offer] | None:
    """Walk up from ``cwd`` looking for an applicable `.lore.yml`.

    The first `.lore.yml` we cross is authoritative for the decision.
    Returns ``(path, offer)`` when:
      * the offer is at exact ``cwd``, or
      * the offer has ``inherit: true``.

    Returns ``None`` when:
      * no `.lore.yml` found within ``max_depth``, or
      * the first found `.lore.yml` is malformed (treated as a stop
        signal — a present-but-broken file shadows any inheriting
        parent so a broken offer never triggers prompts), or
      * the first found `.lore.yml` is at an ancestor without
        ``inherit: true`` (default).

    SessionStart is not a hot path — one walk-up per session start,
    bounded depth. Unlike scope resolution (O(log n) registry lookup),
    offer discovery can tolerate a filesystem walk.
    """
    raw = find_lore_yml_raw(cwd, max_depth=max_depth)
    if raw is None:
        return None
    offer = parse_lore_yml(raw)
    if offer is None:
        return None
    cwd_resolved = cwd.resolve() if cwd.exists() else cwd.absolute()
    if raw.parent == cwd_resolved or offer.inherit:
        return raw, offer
    return None


def find_lore_yml_raw(cwd: Path, *, max_depth: int = 8) -> Path | None:
    """Find the nearest `.lore.yml` path. No parsing, no policy.

    For diagnostics only — production callers should use
    :func:`find_lore_yml`, which applies the inherit policy.
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
