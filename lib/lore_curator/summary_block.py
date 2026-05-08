"""Compose / parse the body ``## Summary`` block.

Pure helpers — no synthesis-pipeline dependencies — so the new
lede + bullets shape can be unit-tested without spinning up the
full Phase-2 flow.

Shape (per PRD #61, slice #62):

    {lede sentence}

    - bullet one
    - bullet two

For thin-signal sessions the bullet list is empty; ``compose`` then
returns just the lede with no trailing blank line. ``parse`` is
forgiving: legacy multi-paragraph prose Summaries (no leading ``- ``)
round-trip as ``(full_text, [])``.
"""
from __future__ import annotations

__all__ = ["compose", "parse"]


def compose(lede: str, items: list[str]) -> str:
    """Render the body ``## Summary`` text from a lede + bullet items.

    ``items`` are bullet bodies *without* the leading ``- `` marker;
    ``compose`` adds the marker. Empty / whitespace-only items are
    dropped. When no items survive, the result is just the lede with
    no trailing blank line.
    """
    lede_norm = (lede or "").strip()
    cleaned = [item.strip() for item in (items or []) if item and item.strip()]
    if not cleaned:
        return lede_norm
    bullet_lines = "\n".join(f"- {item}" for item in cleaned)
    if not lede_norm:
        return bullet_lines
    return f"{lede_norm}\n\n{bullet_lines}"


def parse(text: str) -> tuple[str, list[str]]:
    """Best-effort split of a Summary string into ``(lede, bullets)``.

    The lede is everything before the first ``- `` line; bullets are
    the contiguous ``- `` lines that follow. Legacy prose Summaries
    (no bullets) round-trip as ``(text, [])`` without exception.

    Bullet bodies are returned *without* the leading ``- `` marker so
    a round-trip ``compose(*parse(s))`` re-produces the same shape.
    """
    if not text:
        return "", []
    lines = text.splitlines()
    bullet_start: int | None = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("- "):
            bullet_start = i
            break
    if bullet_start is None:
        return text.strip(), []
    lede = "\n".join(lines[:bullet_start]).strip()
    bullets: list[str] = []
    for line in lines[bullet_start:]:
        stripped = line.lstrip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
        elif not stripped:
            continue
        else:
            break
    return lede, bullets
