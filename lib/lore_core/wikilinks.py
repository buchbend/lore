"""Wikilink primitives — parse, validate, strip-broken.

Used in two complementary places:

* **Forward guard** — curator note writers sanitize LLM output before
  the atomic write, so broken `[[wikilinks]]` never land in the vault
  in the first place.
* **Backward migration** — ``lore migrate --strip-broken-wikilinks``
  cleans up legacy notes written before the forward guard existed.

Both call :func:`strip_broken_wikilinks`. A "broken" wikilink is one
whose target slug doesn't match any existing ``.md`` file in the wiki.
The primitive *converts*, never deletes: ``[[Foo Bar]]`` becomes the
plain text ``Foo Bar``; ``[[slug|displayed]]`` becomes ``displayed``.
The textual content survives, only the link bracketing is removed.

Frontmatter, fenced code blocks, and inline ``\\`code\\``` spans are
preserved verbatim — wikilinks inside YAML metadata or code samples
stay literal.
"""

from __future__ import annotations

import re
from pathlib import Path

# Wikilink with optional alias: [[slug]] or [[slug|displayed text]]
WIKILINK_RE = re.compile(r"\[\[([^\]|\n]+?)(?:\|([^\]\n]+))?\]\]")

_FENCE_RE = re.compile(r"^\s*```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def existing_slugs(wiki_path: Path) -> set[str]:
    """Return the set of note slugs (filename stems) under ``wiki_path``.

    Considers ``.md`` files only; files whose name starts with ``_``
    (``_index.md``, ``_recent.md``, etc.) are excluded since they're
    derived/regenerable, not link targets. Generated collection files
    use ``.txt`` (``_concepts.txt``, ``_decisions.txt``, ``_threads.txt``,
    ``_recent.txt``) and are naturally excluded by the ``.md``-only glob —
    that's the whole point of the ``.txt`` convention.

    Frontmatter ``aliases:`` (Obsidian convention) are also included so
    that a renamed note can leave an ``aliases: [old-stem]`` breadcrumb
    and existing ``[[old-stem]]`` references keep resolving without a
    vault-wide rewrite.

    Wikilink resolution in Lore is **per-wiki**. Wikis are portable
    units (shareable across vaults / teams); a wikilink that only
    resolves via a sibling wiki breaks the moment the wiki is
    extracted. Validators and forward guards must call this function
    against the originating wiki only — never union slugs across
    multiple wikis.
    """
    slugs: set[str] = set()
    for p in wiki_path.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        slugs.add(p.stem)
        slugs.update(_aliases_from_file(p))
    return slugs


def find_orphan_links(wiki_path: Path) -> list[tuple[Path, str, int]]:
    """Return ``(note_path, orphan_slug, match_start_offset)`` for every
    wikilink in the wiki whose target doesn't resolve.

    An orphan is a ``[[slug]]`` whose target is not among
    :func:`existing_slugs` (which is alias-aware and per-wiki). Derived
    ``_``-prefixed files are skipped as sources. Each occurrence is listed
    once — a note with three uses of the same orphan returns three entries.

    Used by the linter to cache the orphan set into ``_catalog.json`` so
    the read-time freshness check can flag broken-link notes without
    re-walking the wiki (positive-evidence staleness — a broken link is a
    named cause; see :mod:`lore_core.freshness`).
    """
    slugs = existing_slugs(wiki_path)
    results: list[tuple[Path, str, int]] = []
    for p in sorted(wiki_path.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for m in WIKILINK_RE.finditer(text):
            slug = m.group(1).strip()
            if slug and slug not in slugs:
                results.append((p, slug, m.start()))
    return results


def _aliases_from_file(path: Path) -> set[str]:
    """Read ``aliases:`` from a note's frontmatter, if any.

    Cheap pre-filter: peek the first 2KB and bail out unless both a
    YAML fence and the literal substring ``aliases`` are present. Avoids
    a full YAML parse on the vast majority of notes that don't use
    aliases.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            head = fh.read(2048)
    except OSError:
        return set()
    if not head.startswith("---") or "aliases" not in head:
        return set()
    from lore_core.schema import parse_frontmatter

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    fm = parse_frontmatter(text)
    raw = fm.get("aliases")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    return {str(a).strip() for a in raw if str(a).strip()}


def sanitize_for_write(text: str, wiki_root: Path) -> str:
    """Forward guard for curator note writers.

    Strips wikilinks whose target doesn't resolve in ``wiki_root``,
    returning the cleaned text. Use this between body rendering and
    ``atomic_write_text`` so LLM-hallucinated ``[[noun phrase]]`` and
    ``[[file/path.py]]`` references never land on disk in the first
    place. Pairs with the backward migration
    ``lore migrate --strip-broken-wikilinks`` for legacy notes; both
    share the same primitive and the same per-wiki scoping.

    Per-wiki scoping is intentional — see :func:`existing_slugs`.
    Cross-wiki references are broken by design and treated as dangles.
    """
    new_text, _, _ = strip_broken_wikilinks(text, existing_slugs(wiki_root))
    return new_text


def _strip_prose(s: str, valid: set[str]) -> tuple[str, list[str]]:
    """Apply the wikilink regex to a prose-only string (no code spans)."""
    replaced: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        slug = m.group(1).strip()
        alias = m.group(2)
        if slug in valid:
            return m.group(0)
        replaced.append(slug)
        return alias.strip() if alias else slug

    return WIKILINK_RE.sub(_sub, s), replaced


def _strip_in_line(line: str, valid: set[str]) -> tuple[str, list[str]]:
    """Strip broken wikilinks in one line, preserving inline `code` spans."""
    pieces: list[str] = []
    replaced: list[str] = []
    last = 0
    for m in _INLINE_CODE_RE.finditer(line):
        prose = line[last : m.start()]
        new_prose, targets = _strip_prose(prose, valid)
        pieces.append(new_prose)
        replaced.extend(targets)
        pieces.append(m.group(0))
        last = m.end()
    tail = line[last:]
    new_tail, targets = _strip_prose(tail, valid)
    pieces.append(new_tail)
    replaced.extend(targets)
    return "".join(pieces), replaced


def strip_broken_wikilinks(
    text: str, valid_slugs: set[str]
) -> tuple[str, int, list[str]]:
    """Strip wikilinks whose target isn't in ``valid_slugs``.

    Returns ``(new_text, n_replaced, replaced_targets)``. Frontmatter,
    fenced code blocks, and inline ``\\`code\\``` spans are passed
    through unchanged. Idempotent: a second call with the same
    ``valid_slugs`` is a no-op.

    ``[[slug]]``           → ``slug``                (broken, no alias)
    ``[[slug|displayed]]`` → ``displayed``           (broken, with alias)
    ``[[real-slug]]``      → ``[[real-slug]]``       (kept; in valid_slugs)
    """
    # Locate the byte boundary between frontmatter and body without
    # passing through ``split_frontmatter`` — that helper strips leading
    # newlines from the body, which would silently collapse the blank
    # line many notes have between the closing ``---`` and their first
    # heading. We need byte-exact preservation here.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            boundary = end + 4  # past the closing ``\n---``
            if boundary < len(text) and text[boundary] == "\n":
                boundary += 1
            prefix, body = text[:boundary], text[boundary:]
        else:
            prefix, body = "", text
    else:
        prefix, body = "", text

    out: list[str] = []
    in_fence = False
    all_replaced: list[str] = []
    for line in body.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        new_line, replaced = _strip_in_line(line, valid_slugs)
        all_replaced.extend(replaced)
        out.append(new_line)

    return prefix + "".join(out), len(all_replaced), all_replaced
