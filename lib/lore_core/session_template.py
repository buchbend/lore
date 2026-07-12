"""Per-wiki session-note template loader.

Per-wiki overrides live at ``<wiki>/templates/session.md`` and replace
the shipped ``standard.md`` for that wiki only. Other wikis (and any
flow without a wiki override) fall through to the shipped default.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def load_session_template(wiki_dir: Path | None) -> str:
    """Return the active session-note template markdown for ``wiki_dir``.

    Resolution order:

    1. ``<wiki_dir>/templates/session.md`` — per-wiki override.
    2. The shipped ``lore_core.session_templates.standard.md``.

    Returns the raw markdown content. Never raises: if neither file
    is readable the shipped default is loaded via importlib resources
    (which is bundled in the package data).
    """
    if wiki_dir is not None:
        override = wiki_dir / "templates" / "session.md"
        if override.is_file():
            try:
                return override.read_text(encoding="utf-8")
            except OSError:
                pass
    return _load_packaged_standard()


@lru_cache(maxsize=1)
def _load_packaged_standard() -> str:
    """Read the shipped ``standard.md``. Cached after first call."""
    from importlib import resources

    return resources.files("lore_core.session_templates").joinpath(
        "standard.md"
    ).read_text(encoding="utf-8")


def section_norms_for_prompt(wiki_dir: Path | None) -> str:
    """Return just the section-authoring norms for prompt injection.

    Slices the ``## Section-authoring norms`` block out of the active
    template. Curator A injects this into its noteworthy prompt so the
    LLM's Decisions / Loose ends / What-we-worked-on output respects
    the wiki's locked conventions without re-stating them in code.
    """
    text = load_session_template(wiki_dir)
    marker = "## Section-authoring norms"
    idx = text.find(marker)
    if idx == -1:
        # Template lacks the section — fall back to the whole template
        # rather than nothing. Better to over-include than to silently
        # lose the steering.
        return text
    return text[idx:]
