"""Compatibility shim — delegates to :mod:`ingest` and :mod:`adapters`.

Originally a 480-line module owning a regex-union step detector, this
file is now a thin facade. Public API (``parse``, ``parse_payload``)
is preserved verbatim so existing imports keep working; internals
have moved:

* Markdown shape detection → :mod:`markdown_adapter`
* Hook-payload extraction → :mod:`adapters.claude_code`
* Public dispatch → :mod:`ingest`

Two helpers retain their original signatures because they're imported
across the codebase:

* :func:`parse(text, *, slug_override=None) -> StructuredPlan` —
  parses raw markdown. Always succeeds; degraded plans carry
  ``confidence="fallback"`` + structured warnings.
* :func:`parse_payload(payload) -> tuple[str | None, str]` —
  Claude-Code-shaped hook JSON → ``(markdown, source_field)``.
"""
from __future__ import annotations

from typing import Any

from . import canonical
from .types import StructuredPlan


def parse(text: str, *, slug_override: str | None = None) -> StructuredPlan:
    """Parse plan markdown into a :class:`StructuredPlan`.

    ``slug_override`` lets callers force a slug (e.g. from a manual
    ``slug:`` frontmatter the user prefixed). Otherwise the slug is
    derived from the H1 title or the first 40 chars of plain text.

    Always succeeds — non-conforming markdown degrades to a
    zero/single-step plan with ``confidence="fallback"`` rather than
    raising. The hook handler treats ``confidence="fallback"`` as a
    hard error; CLI tools may file with warnings stamped.
    """
    from . import ingest

    result = ingest.ingest_plan(
        ingest.IngestSource(kind="markdown", payload=text, producer="cli")
    )
    plan = result.plan
    if slug_override:
        # Construct a fresh plan with the overridden slug — StructuredPlan
        # is mutable, but rebuilding makes the override explicit at the
        # call site rather than mutating in place.
        from dataclasses import replace as _replace

        plan = _replace(plan, slug=slug_override)
    return plan


def parse_payload(payload: dict[str, Any]) -> tuple[str | None, str]:
    """Extract plan markdown from a Claude Code hook JSON payload.

    Returns ``(text, source_field)``. ``text`` is None if nothing
    extractable was found; ``source_field`` names which input path
    matched (for telemetry).

    Delegates to the ``claude-code`` adapter; preserved as a free
    function for back-compat with existing importers.
    """
    from .adapters import claude_code

    return claude_code.extract(payload)


__all__ = ["parse", "parse_payload", "StructuredPlan", "canonical"]
