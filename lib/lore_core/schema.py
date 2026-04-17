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
#
# `status` was removed from REQUIRED_FIELDS by the status-vocabulary-
# minimalism decision: notes are implicitly canonical unless they carry
# `draft: true` or `superseded_by: [[...]]`. Legacy `status:` values are
# still accepted by the parser during the deprecation window.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "project": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "description",
        "tags",
    ],
    "concept": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "description",
        "tags",
    ],
    "decision": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "description",
        "tags",
    ],
    "session": [
        "schema_version",
        "type",
        "created",
        "last_reviewed",
        "description",
    ],
    "paper": [
        "schema_version",
        "type",
        "citekey",
        "description",
        "tags",
    ],
}

# Optional fields the linter understands across types. Listing them here
# makes the schema self-documenting; absence is not an error.
OPTIONAL_FIELDS: set[str] = {
    "status",  # legacy — accepted during deprecation, no operational effect
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
    # status-vocabulary-minimalism — lifecycle via opt-in signals
    "draft",  # bool — note is not yet committed to (rare, opt-in)
    "superseded_by",  # str | list[str] — wikilink(s) to successor note(s)
}


def compute_lifecycle(fm: dict) -> str:
    """Return `canonical | draft | superseded` derived from frontmatter.

    Per status-vocabulary-minimalism, lifecycle is a derived property,
    not a user-maintained field. Precedence: superseded wins (the note
    has been replaced, so the draft/canonical distinction is moot).
    """
    if fm.get("superseded_by"):
        return "superseded"
    if fm.get("draft") is True:
        return "draft"
    return "canonical"

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
