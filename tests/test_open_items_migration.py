"""Tests for v1 → v2 `## Open items` migration.

Pure-logic tests — the interactive CLI is a thin wrapper over these
functions and covered separately by manual runs.
"""

from __future__ import annotations

from lore_curator.curator_c import (
    extract_open_items,
    migrate_open_items,
)

V1_WITH_ITEMS = """---
schema_version: 1
type: session
created: 2026-01-10
last_reviewed: 2026-01-10
status: stable
description: "A v1 session note."
tags: [topic/test]
---

# Session: sample

## What we worked on

- bullet one

## Decisions made

- None

## Commits / PRs

- `abcdef0` initial commit

## Open items

- retry cap missing on transfer queue
- rename process_payload to parse_payload
- stale docs in README

## Vault updates

- None
"""


V1_NONE = """---
schema_version: 1
type: session
created: 2026-01-10
last_reviewed: 2026-01-10
status: stable
description: "Empty open items."
---

# Session

## Open items

- None

## Vault updates

- None
"""


V1_NO_OPEN_ITEMS = """---
schema_version: 1
type: session
created: 2026-01-10
last_reviewed: 2026-01-10
status: stable
description: "No open items section."
---

# Session

## What we worked on

- something
"""


# ---------- extract ----------


def test_extract_returns_bullet_list():
    items = extract_open_items(V1_WITH_ITEMS)
    assert items == [
        "retry cap missing on transfer queue",
        "rename process_payload to parse_payload",
        "stale docs in README",
    ]


def test_extract_treats_none_placeholder_as_empty():
    assert extract_open_items(V1_NONE) == []


def test_extract_returns_empty_when_heading_absent():
    assert extract_open_items(V1_NO_OPEN_ITEMS) == []


# ---------- migrate ----------


def test_migrate_bumps_schema_version():
    out = migrate_open_items(V1_WITH_ITEMS, [])
    assert "schema_version: 2" in out
    assert "schema_version: 1" not in out


def test_migrate_replaces_open_items_with_two_sections():
    out = migrate_open_items(
        V1_WITH_ITEMS,
        [
            ("issue", "#47"),
            ("loose_end", None),
            ("resolved", None),
        ],
    )
    assert "## Open items" not in out
    assert "## Issues touched" in out
    assert "## Loose ends" in out
    assert "#47 retry cap missing on transfer queue" in out
    assert "- rename process_payload to parse_payload" in out
    # Resolved item dropped entirely
    assert "stale docs in README" not in out


def test_migrate_preserves_surrounding_sections():
    out = migrate_open_items(V1_WITH_ITEMS, [("loose_end", None)] * 3)
    assert "## What we worked on" in out
    assert "## Decisions made" in out
    assert "## Commits / PRs" in out
    assert "## Vault updates" in out
    # Order preserved: Commits / PRs comes before new sections, which come before Vault updates
    commits_idx = out.index("## Commits / PRs")
    issues_idx = out.index("## Issues touched")
    loose_idx = out.index("## Loose ends")
    vault_idx = out.index("## Vault updates")
    assert commits_idx < issues_idx < loose_idx < vault_idx


def test_migrate_issue_without_number_annotates():
    out = migrate_open_items(V1_WITH_ITEMS, [("issue", None)])
    assert "retry cap missing on transfer queue (needs issue)" in out


def test_migrate_empty_open_items_produces_none_placeholders():
    out = migrate_open_items(V1_NONE, [])
    assert "## Issues touched" in out
    assert "## Loose ends" in out
    # Both sections fall back to the _None_ placeholder when empty
    assert out.count("- _None_") >= 2


def test_migrate_note_without_open_items_still_bumps_schema():
    out = migrate_open_items(V1_NO_OPEN_ITEMS, [])
    assert "schema_version: 2" in out
    # Body untouched
    assert "## Issues touched" not in out
    assert "## Loose ends" not in out


def test_migrate_is_idempotent():
    first = migrate_open_items(V1_WITH_ITEMS, [
        ("issue", "#47"),
        ("loose_end", None),
        ("resolved", None),
    ])
    # Re-running on already-migrated text: no ## Open items to process, schema unchanged
    second = migrate_open_items(first, [])
    assert first == second


def test_migrate_unmapped_bullets_default_to_loose_end():
    """Fewer decisions than bullets — extras fall through as loose ends."""
    out = migrate_open_items(V1_WITH_ITEMS, [("issue", "#47")])
    # First bullet becomes an issue
    assert "#47 retry cap missing on transfer queue" in out
    # Remaining two fall through
    assert "- rename process_payload to parse_payload" in out
    assert "- stale docs in README" in out
