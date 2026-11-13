"""Frontmatter schema — version constants and parsing helpers.

Schema is versioned via `schema_version` frontmatter. v1 is the current
version. Breaking changes bump this number and ship a migration.
"""

from __future__ import annotations

import re

import yaml

SCHEMA_VERSION = 2
SCHEMA_VERSIONS_SUPPORTED: tuple[int, ...] = (1, 2)

# No field carries a note lifecycle (issue #596). A note in a wiki is in
# force. The parser accepts `status`, `draft`, `superseded_by` and
# `last_reviewed` where an old note holds them, and Lore ignores them.
# `lore wiki check` asks for `description` only.

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Split a markdown text into (yaml_frontmatter_text, body).

    Returns the inner YAML (no `---` delimiters) and the body that
    follows the closing `---`, with the body's leading newlines stripped.
    Returns ``None`` if the text has no frontmatter.

    This is the canonical splitter — every site that picks frontmatter
    apart goes through this so the shape ("opens with `---`, closes with
    `\\n---`, body follows after the closing fence") lives in one place.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[4:end], text[end + 4 :].lstrip("\n")


def strip_frontmatter(text: str) -> str:
    """Return ``text`` with any leading YAML frontmatter removed.

    Pass-through if there is no frontmatter (or the closing fence is
    missing) — never silently drops content.
    """
    split = split_frontmatter(text)
    return split[1] if split is not None else text


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text. Empty dict if absent or malformed."""
    split = split_frontmatter(text)
    if split is None:
        return {}
    try:
        return yaml.safe_load(split[0]) or {}
    except yaml.YAMLError:
        return {}


def extract_wikilinks(text: str) -> list[str]:
    """Extract all [[wikilink]] targets from body (after frontmatter), preserving order, deduped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return [link for link in dict.fromkeys(WIKILINK_RE.findall(text)) if link.strip()]

