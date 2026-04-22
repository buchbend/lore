"""Tests for v1 + v2 session-note schema acceptance.

The linter must accept both versions without false errors so existing
v1 notes keep passing while v2 writes start appearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.lint import NoteInfo, check_frontmatter
from lore_core.schema import (
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
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


def _note_from(fm: dict, path: str = "sessions/example.md") -> NoteInfo:
    return NoteInfo(
        path=path,
        filename=Path(path).stem,
        wiki="test",
        note_type=fm.get("type"),
        status=fm.get("status"),
        description=fm.get("description"),
        tags=fm.get("tags", []) or [],
    )


# ---------- schema constants ----------


def test_schema_version_bumped_to_2():
    assert SCHEMA_VERSION == 2


def test_both_versions_supported():
    assert 1 in SCHEMA_VERSIONS_SUPPORTED
    assert 2 in SCHEMA_VERSIONS_SUPPORTED


def test_v2_optional_fields_registered():
    for f in ("user", "implements", "loose_ends", "scopes_touched",
              "implemented_at", "implemented_by", "superseded_by"):
        assert f in OPTIONAL_FIELDS, f"v2 field {f!r} missing from OPTIONAL_FIELDS"


def test_required_fields_unchanged_for_session():
    # v2 doesn't add new *required* fields — only optionals.
    # status-vocabulary-minimalism dropped `status:` from required.
    assert "schema_version" in REQUIRED_FIELDS["session"]
    assert "status" not in REQUIRED_FIELDS["session"]
    assert "scope" not in REQUIRED_FIELDS["session"]
    assert "user" not in REQUIRED_FIELDS["session"]


# ---------- linter tolerance ----------


@pytest.mark.parametrize("raw,label", [(V1_NOTE, "v1"), (V2_NOTE, "v2")])
def test_both_versions_pass_frontmatter_check(raw, label):
    fm = parse_frontmatter(raw)
    assert fm, f"{label} note failed to parse"
    note = _note_from(fm)
    issues = check_frontmatter(note, fm, wiki_name="test")
    errors = [i for i in issues if i.severity == "ERROR"]
    assert errors == [], f"{label} note produced false errors: {errors}"


def test_v2_extra_fields_are_preserved_through_parse():
    fm = parse_frontmatter(V2_NOTE)
    assert fm["scope"] == "ccat:data-center:data-transfer"
    assert fm["user"] == "buchbend"
    assert fm["implements"] == ["some-proposed-concept", "another-concept:partial"]
    assert fm["loose_ends"] == ["rename process_payload → parse_payload"]


def test_v1_note_without_scope_is_fine():
    fm = parse_frontmatter(V1_NOTE)
    assert "scope" not in fm
    note = _note_from(fm)
    issues = check_frontmatter(note, fm, wiki_name="test")
    assert not any(i.severity == "ERROR" for i in issues)
