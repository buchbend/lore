"""Two-region note primitives — reload-safe vs human-only.

A note body may carry a ``<!-- lore:human-only -->`` HTML-comment
marker. Everything before the marker is the *reload-safe* region (what
LLM retrieval is allowed to see); everything after is the *human-only*
region (private scratch space the user keeps for themselves).

The primitives are pure:

* :data:`HUMAN_ONLY_MARKER` — the canonical marker literal.
* :func:`split_regions` — forgiving parser returning
  ``(reload_safe, human_only_or_None)``.
* :func:`render_regions` — round-trip companion that omits the marker
  cleanly when the human-only region is empty/``None``.
* :func:`redact_human_only` — convenience returning only the
  reload-safe portion; the default filter at every LLM-facing
  boundary.

Code-fence aware. A marker line that lives inside a fenced code
block (``\\``` ``` / ``~~~``) is NOT a region boundary — symmetric to
the fence handling in :mod:`lib.lore_core.wikilinks`.
"""

from __future__ import annotations

import re

HUMAN_ONLY_MARKER = "<!-- lore:human-only -->"

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MARKER_RE = re.compile(r"^\s*" + re.escape(HUMAN_ONLY_MARKER) + r"\s*$")


def split_regions(body: str) -> tuple[str, str | None]:
    """Split ``body`` at the first real ``HUMAN_ONLY_MARKER`` line.

    Returns ``(reload_safe, human_only_or_None)``.

    Rules:
    * No marker → whole body is reload-safe; second tuple element is
      ``None``.
    * Multiple markers → first one wins; subsequent markers stay
      verbatim in the human-only region.
    * Whitespace tolerant — leading/trailing whitespace on the marker
      line is ignored.
    * Code-fence aware — a marker line inside a ``\\``` ``` or ``~~~``
      fenced code block is NOT a boundary.
    """
    lines = body.splitlines(keepends=True)
    in_fence = False
    for idx, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _MARKER_RE.match(line):
            reload_safe = "".join(lines[:idx])
            human_only = "".join(lines[idx + 1:])
            return reload_safe, human_only
    return body, None


def render_regions(reload_safe: str, human_only: str | None) -> str:
    """Round-trip companion to :func:`split_regions`.

    Emits the marker line only when ``human_only`` is non-empty. The
    marker is delimited by single newlines so a subsequent
    :func:`split_regions` call recovers the same regions verbatim
    (modulo a possible trailing newline normalisation that does not
    affect content).
    """
    if not human_only:
        return reload_safe
    sep_left = "" if reload_safe.endswith("\n") or reload_safe == "" else "\n"
    return f"{reload_safe}{sep_left}{HUMAN_ONLY_MARKER}\n{human_only}"


def redact_human_only(body: str) -> str:
    """Return only the reload-safe portion of ``body``.

    Convenience wrapper over :func:`split_regions` for the LLM-facing
    default. Notes without a marker pass through unchanged.
    """
    reload_safe, _ = split_regions(body)
    return reload_safe
