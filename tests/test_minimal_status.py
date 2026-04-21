"""Tests for the status-vocabulary-minimalism decision (#5).

Contract:
  - Schema: `status:` is optional; `draft:` and `superseded_by:` are
    the two opt-in lifecycle signals; lifecycle is derived.
  - Linter: no warnings on missing `status:`; catalog has `lifecycle`;
    index renders DRAFT / SUPERSEDED badges.
  - Curator: staleness flag (review-only) uses canonical+age; the
    supersession pass writes only `superseded_by:`.
  - Migration: `lore migrate --minimal-status` maps per the decision
    and is idempotent.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from lore_core.lint import NoteInfo, check_frontmatter, check_staleness, run_lint
from lore_core.migrate import _minimize_status_text, migrate_minimal_status
from lore_core.schema import (
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    compute_lifecycle,
    parse_frontmatter,
)
from lore_curator.curator_c import _pass_staleness, _pass_supersession

# ---------- schema ----------


def test_status_not_required_anywhere():
    for kind, fields in REQUIRED_FIELDS.items():
        assert "status" not in fields, f"`status` still required for {kind}"


def test_draft_and_superseded_by_are_optional():
    assert "draft" in OPTIONAL_FIELDS
    assert "superseded_by" in OPTIONAL_FIELDS


def test_status_accepted_for_back_compat():
    # Parser still accepts legacy notes; it just doesn't require status.
    assert "status" in OPTIONAL_FIELDS


@pytest.mark.parametrize("fm,expected", [
    ({}, "canonical"),
    ({"draft": True}, "draft"),
    ({"draft": False}, "canonical"),
    ({"superseded_by": "[[next]]"}, "superseded"),
    ({"superseded_by": ["[[a]]", "[[b]]"]}, "superseded"),
    # superseded wins over draft (shouldn't co-occur but be defensive)
    ({"draft": True, "superseded_by": "[[x]]"}, "superseded"),
])
def test_compute_lifecycle(fm, expected):
    assert compute_lifecycle(fm) == expected


# ---------- linter: no missing-status errors ----------


def _note_from(fm: dict, path: str = "concepts/example.md") -> NoteInfo:
    return NoteInfo(
        path=path,
        filename=Path(path).stem,
        wiki="test",
        note_type=fm.get("type"),
        status=fm.get("status"),
        lifecycle=compute_lifecycle(fm),
        superseded_by=fm.get("superseded_by"),
        description=fm.get("description"),
        tags=fm.get("tags", []) or [],
    )


def test_canonical_note_without_status_passes_lint():
    raw = """---
schema_version: 2
type: concept
created: 2026-04-17
last_reviewed: 2026-04-17
description: "Canonical, no status."
tags: [topic/x]
---
body
"""
    fm = parse_frontmatter(raw)
    note = _note_from(fm)
    issues = check_frontmatter(note, fm, wiki_name="test")
    assert [i for i in issues if i.severity == "ERROR"] == []


def test_draft_note_does_not_error():
    fm = {
        "schema_version": 2,
        "type": "concept",
        "created": "2026-04-17",
        "last_reviewed": "2026-04-17",
        "description": "A draft.",
        "tags": ["topic/x"],
        "draft": True,
    }
    note = _note_from(fm)
    issues = check_frontmatter(note, fm, wiki_name="test")
    assert [i for i in issues if i.severity == "ERROR"] == []


# ---------- linter: staleness semantics ----------


def test_staleness_flags_canonical_old_note():
    old = (date.today() - timedelta(days=200)).isoformat()
    fm = {"type": "concept", "last_reviewed": old}
    note = _note_from(fm)
    issues = check_staleness(note, fm, wiki_name="test")
    assert len(issues) == 1
    assert issues[0].check == "stale"


def test_staleness_skips_drafts():
    old = (date.today() - timedelta(days=200)).isoformat()
    fm = {"type": "concept", "last_reviewed": old, "draft": True}
    note = _note_from(fm)
    assert check_staleness(note, fm, wiki_name="test") == []


def test_staleness_skips_superseded():
    old = (date.today() - timedelta(days=200)).isoformat()
    fm = {"type": "concept", "last_reviewed": old, "superseded_by": "[[next]]"}
    note = _note_from(fm)
    assert check_staleness(note, fm, wiki_name="test") == []


def test_staleness_skips_sessions():
    old = (date.today() - timedelta(days=400)).isoformat()
    fm = {"type": "session", "last_reviewed": old}
    note = _note_from(fm)
    note.note_type = "session"
    assert check_staleness(note, fm, wiki_name="test") == []


# ---------- linter: catalog + index badges ----------


@pytest.fixture
def wiki_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "vault"
    wiki = root / "wiki" / "w"
    (wiki / "concepts").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(root))
    return wiki


def _concept(wiki: Path, slug: str, fm_extra: str = "") -> Path:
    p = wiki / "concepts" / f"{slug}.md"
    p.write_text(
        f"""---
schema_version: 2
type: concept
created: 2026-04-17
last_reviewed: 2026-04-17
description: "{slug}."
tags: [topic/test]
{fm_extra}---
# {slug}

Links to [[other]].
"""
    )
    return p


def test_catalog_has_lifecycle_key(wiki_root: Path):
    _concept(wiki_root, "alpha")
    _concept(wiki_root, "beta", fm_extra="draft: true\n")
    _concept(wiki_root, "gamma", fm_extra='superseded_by: "[[alpha]]"\n')
    _concept(wiki_root, "other")

    run_lint(check_only=False)
    catalog = json.loads((wiki_root / "_catalog.json").read_text())
    entries = {e["name"]: e for section in catalog["sections"].values() for e in section}
    assert entries["alpha"]["lifecycle"] == "canonical"
    assert entries["beta"]["lifecycle"] == "draft"
    assert entries["gamma"]["lifecycle"] == "superseded"
    assert entries["gamma"]["superseded_by"] == "[[alpha]]"


def test_index_renders_lifecycle_badges(wiki_root: Path):
    _concept(wiki_root, "alpha")
    _concept(wiki_root, "beta", fm_extra="draft: true\n")
    _concept(wiki_root, "gamma", fm_extra='superseded_by: "[[alpha]]"\n')
    _concept(wiki_root, "other")

    run_lint(check_only=False)
    index = (wiki_root / "_index.md").read_text()
    assert "DRAFT" in index, "expected DRAFT badge in index"
    assert "SUPERSEDED → [[alpha]]" in index, "expected SUPERSEDED badge"
    # Canonical notes get no badge
    alpha_line = next(
        line for line in index.splitlines()
        if "[[alpha]]" in line and "→" not in line
    )
    assert "DRAFT" not in alpha_line
    assert "SUPERSEDED" not in alpha_line


# ---------- curator: staleness + supersession ----------


def test_curator_staleness_is_review_only(tmp_path: Path, monkeypatch):
    root = tmp_path / "vault"
    wiki = root / "wiki" / "w"
    (wiki / "concepts").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(root))

    old = (date.today() - timedelta(days=200)).isoformat()
    p = wiki / "concepts" / "stale.md"
    p.write_text(
        f"""---
schema_version: 2
type: concept
created: 2026-01-01
last_reviewed: {old}
description: "stale."
tags: [topic/test]
---
body
"""
    )
    actions = _pass_staleness(wiki, date.today(), threshold=180)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "review_stale"
    assert a.patch == {}  # review-only, no frontmatter write


def test_curator_supersession_writes_only_superseded_by(tmp_path: Path, monkeypatch):
    root = tmp_path / "vault"
    wiki = root / "wiki" / "w"
    (wiki / "concepts").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(root))

    new = wiki / "concepts" / "new.md"
    new.write_text(
        """---
schema_version: 2
type: concept
created: 2026-04-17
last_reviewed: 2026-04-17
description: "replacement."
tags: [topic/test]
---
# new

This supersedes [[old]].
"""
    )
    old = wiki / "concepts" / "old.md"
    old.write_text(
        """---
schema_version: 2
type: concept
created: 2026-01-01
last_reviewed: 2026-01-01
description: "the old thing."
tags: [topic/test]
---
# old
"""
    )
    actions = _pass_supersession(wiki)
    assert len(actions) == 1
    a = actions[0]
    assert a.path == old
    assert a.patch == {"superseded_by": "[[new]]"}
    assert "status" not in a.patch


# ---------- migration ----------


@pytest.mark.parametrize("old_status,expect_keys,expect_absent", [
    ("active", set(), {"status", "draft"}),
    ("stable", set(), {"status", "draft"}),
    ("accepted", set(), {"status", "draft"}),
    ("stale", set(), {"status", "draft"}),
    ("implemented", set(), {"status", "draft"}),
    ("partial", set(), {"status", "draft"}),
    ("abandoned", set(), {"status", "draft"}),
    ("proposed", {"draft"}, {"status"}),
])
def test_minimize_status_mapping(old_status, expect_keys, expect_absent):
    raw = f"""---
schema_version: 2
type: concept
status: {old_status}
created: 2026-04-17
last_reviewed: 2026-04-17
description: "x."
tags: [topic/test]
---
# body
"""
    new_text, _warning = _minimize_status_text(raw)
    fm = parse_frontmatter(new_text)
    for k in expect_absent:
        assert k not in fm, f"expected {k} removed for status={old_status}"
    for k in expect_keys:
        assert k in fm, f"expected {k} present for status={old_status}"
    if old_status == "proposed":
        assert fm["draft"] is True


def test_minimize_status_superseded_without_relation_warns():
    raw = """---
type: concept
status: superseded
description: "x."
---
body
"""
    new_text, warning = _minimize_status_text(raw)
    fm = parse_frontmatter(new_text)
    assert "status" not in fm
    assert warning is not None
    assert "superseded_by" in warning


def test_minimize_status_superseded_with_relation_clean():
    raw = """---
type: concept
status: superseded
superseded_by: "[[successor]]"
description: "x."
---
body
"""
    new_text, warning = _minimize_status_text(raw)
    fm = parse_frontmatter(new_text)
    assert "status" not in fm
    assert fm["superseded_by"] == "[[successor]]"
    assert warning is None


def test_minimize_status_idempotent_when_no_status():
    raw = """---
type: concept
draft: true
description: "x."
---
body
"""
    new_text, warning = _minimize_status_text(raw)
    assert new_text == raw
    assert warning is None


def test_migrate_minimal_status_end_to_end(tmp_path: Path, monkeypatch):
    root = tmp_path / "vault"
    wiki = root / "wiki" / "w"
    (wiki / "concepts").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(root))

    a = wiki / "concepts" / "a.md"
    a.write_text(
        """---
type: concept
status: active
created: 2026-01-01
last_reviewed: 2026-01-01
description: "a."
tags: []
---
body
"""
    )
    b = wiki / "concepts" / "b.md"
    b.write_text(
        """---
type: concept
status: proposed
created: 2026-01-01
last_reviewed: 2026-01-01
description: "b."
tags: []
---
body
"""
    )
    c = wiki / "concepts" / "c.md"
    c.write_text(
        """---
type: concept
draft: true
created: 2026-01-01
last_reviewed: 2026-01-01
description: "already migrated."
tags: []
---
body
"""
    )

    touched = migrate_minimal_status(wiki_filter="w", dry_run=False)
    assert touched == 2  # a + b; c is already in the new shape

    fm_a = parse_frontmatter(a.read_text())
    assert "status" not in fm_a

    fm_b = parse_frontmatter(b.read_text())
    assert "status" not in fm_b
    assert fm_b["draft"] is True

    fm_c = parse_frontmatter(c.read_text())
    assert fm_c["draft"] is True

    # Idempotent: second run should touch nothing.
    touched2 = migrate_minimal_status(wiki_filter="w", dry_run=False)
    assert touched2 == 0
