"""Frontmatter schema — required fields per note type, parsing helpers.

Schema is versioned via `schema_version` frontmatter. v1 is the current
version. Breaking changes bump this number and ship a migration.
"""

from __future__ import annotations

import re

import yaml

SCHEMA_VERSION = 2
SCHEMA_VERSIONS_SUPPORTED: tuple[int, ...] = (1, 2)

# Valid `status:` values. Core vocabulary + v2 additions for the
# `implements:` cross-reference flow (see concepts/lore/implements-cross-reference).
# The linter accepts any string today; this set is for documentation and
# for downstream tools (curator) that need to know the universe of states.
VALID_STATUSES: frozenset[str] = frozenset(
    {
        "active",
        "stable",
        "proposed",
        "accepted",
        "superseded",
        "stale",
        # v2: `implements:` target states
        "implemented",
        "partial",
        "abandoned",
    }
)

# Required frontmatter fields per note type. `schema_version` is required
# on all new notes but the linter auto-fixes by writing the current
# SCHEMA_VERSION when missing on an otherwise-valid note (see --fix).
REQUIRED_FIELDS: dict[str, list[str]] = {
    "project": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "status",
        "description",
        "tags",
    ],
    "concept": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "status",
        "description",
        "tags",
    ],
    "decision": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "status",
        "description",
        "tags",
    ],
    "session": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "status",
        "description",
    ],
    "paper": [
        "schema_version",
        "type",
        "citekey",
        "status",
        "description",
        "tags",
    ],
}

# Optional fields the linter understands across types. Listing them here
# makes the schema self-documenting; absence is not an error.
OPTIONAL_FIELDS: set[str] = {
    "repos",  # list[str] — ["org/name", ...] — enables repo-based scoping
    "scope",  # str — hierarchical, e.g. "ccat:data-center:data-transfer"
    "scopes_touched",  # list[str] — additional scopes a session spanned
    "contradicts",  # list[str] — wikilinks to notes this contradicts
    "project",  # str — primary project for session notes
    "provenance",  # str — "extracted" for inbox-processed notes
    "source",  # str — original filename for extracted notes
    "publish",  # bool — for Quartz / static-site filtering
    "aliases",  # list[str] — alternative names (Obsidian convention)
    # v2 session-note additions
    "user",  # str — canonical handle (identity-aliasing.md)
    "implements",  # list[str] — proposal slugs this session realizes
    "loose_ends",  # list[str] — short-form in-session observations
    # v2 `implements:` target-note metadata (written by curator)
    "implemented_at",  # str — YYYY-MM-DD when a proposal was realized
    "implemented_by",  # str — wikilink to the session note that did it
    "superseded_by",  # str — wikilink when status is superseded
}

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text. Empty dict if absent or malformed."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def extract_wikilinks(text: str) -> list[str]:
    """Extract all [[wikilink]] targets from body (after frontmatter), preserving order, deduped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return [link for link in dict.fromkeys(WIKILINK_RE.findall(text)) if link.strip()]
