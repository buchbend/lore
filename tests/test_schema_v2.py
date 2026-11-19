"""Tests for v1 + v2 session-note schema acceptance.

The linter must accept both versions without false errors so existing
v1 notes keep passing while v2 writes start appearing.
"""

from __future__ import annotations


from lore_core.schema import (
    SCHEMA_VERSION,
    SCHEMA_VERSIONS_SUPPORTED,
    parse_frontmatter,
)

V1_NOTE = """---
schema_version: 1
type: session
created: 2026-01-10
last_reviewed: 2026-01-10
status: stable
description: "A v1 session note."
tags: [topic/test]
repos: [org/foo]
project: example
---

# Session: old shape

## Open items

- Something to follow up on.
"""

V2_NOTE = """---
schema_version: 2
type: session
created: 2026-04-17
last_reviewed: 2026-04-17
status: stable
description: "A v2 session note."
tags: [topic/test]
scope: ccat:data-center:data-transfer
repos: [ccatobs/data-transfer]
scopes_touched: [ccat:data-center:system-integration]
user: buchbend
implements:
  - some-proposed-concept
  - another-concept:partial
loose_ends:
  - "rename process_payload → parse_payload"
project: lore
---

# Session: new shape

## Issues touched
- #47 retry cap missing

## Loose ends
- informal observation
"""


def test_schema_version_bumped_to_2():
    assert SCHEMA_VERSION == 2


def test_both_versions_supported():
    assert 1 in SCHEMA_VERSIONS_SUPPORTED
    assert 2 in SCHEMA_VERSIONS_SUPPORTED


# ---------- linter tolerance ----------


def test_v2_extra_fields_are_preserved_through_parse():
    fm = parse_frontmatter(V2_NOTE)
    assert fm["scope"] == "ccat:data-center:data-transfer"
    assert fm["user"] == "buchbend"
    assert fm["implements"] == ["some-proposed-concept", "another-concept:partial"]
    assert fm["loose_ends"] == ["rename process_payload → parse_payload"]
