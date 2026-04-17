"""Tests for the curator's `implements:` cross-reference pass.

Contract (post status-vocabulary-minimalism):
  - `implements: slug` on a draft target → drop `draft: true`, stamp
    `implemented_at` + `implemented_by` back-link.
  - `implements: slug` on a canonical target → no frontmatter effect
    (pure back-link in the session note).
  - `implements: slug:superseded-by:other` → write `superseded_by:
    [[other]]` on target.
  - `:partial` / `:abandoned` markers are session-note documentation
    only; no target frontmatter change.
  - Idempotent on repeat runs; unresolvable slugs are silently skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_curator.core import _parse_implements_entry, _pass_implements

# ---------- parser ----------


@pytest.mark.parametrize("entry,expected", [
    ("my-concept", ("my-concept", "implements", None)),
    ("my-concept:partial", ("my-concept", "partial", None)),
    ("my-concept:abandoned", ("my-concept", "abandoned", None)),
    ("my-concept:superseded-by:other-slug",
     ("my-concept", "superseded", "other-slug")),
    ("slug-with-dashes", ("slug-with-dashes", "implements", None)),
    # Unknown trailing marker → fall through to default
    ("slug:weird-state", ("slug:weird-state", "implements", None)),
    # Whitespace tolerance
    ("  my-concept  ", ("my-concept", "implements", None)),
])
def test_parse_implements_entry(entry, expected):
    assert _parse_implements_entry(entry) == expected


# ---------- pass integration ----------


@pytest.fixture
def wiki(tmp_path: Path, monkeypatch) -> Path:
    """Minimal wiki layout with sessions + concepts + decisions dirs."""
    w = tmp_path / "wiki" / "testwiki"
    (w / "sessions").mkdir(parents=True)
    (w / "concepts").mkdir()
    (w / "decisions").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return w


def _write_concept(wiki: Path, slug: str, draft: bool = False,
                   extra_fm: str = "") -> Path:
    fpath = wiki / "concepts" / f"{slug}.md"
    draft_line = "draft: true\n" if draft else ""
    fpath.write_text(
        f"""---
schema_version: 2
type: concept
created: 2026-01-01
last_reviewed: 2026-01-01
description: "A test concept."
tags: [topic/test]
{draft_line}{extra_fm}---
# {slug}
body
"""
    )
    return fpath


def _write_session(wiki: Path, slug: str, implements: list[str],
                    created: str = "2026-04-17") -> Path:
    implements_yaml = "\n".join(f"  - {e}" for e in implements) if implements else "[]"
    fpath = wiki / "sessions" / f"{created}-{slug}.md"
    fpath.write_text(
        f"""---
schema_version: 2
type: session
created: {created}
last_reviewed: {created}
description: "A test session."
implements:
{implements_yaml}
---
# Session
body
"""
    )
    return fpath


def test_draft_target_gets_promoted_and_stamped(wiki):
    target = _write_concept(wiki, "my-proposal", draft=True)
    _write_session(wiki, "s1", ["my-proposal"])
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    a = actions[0]
    assert a.path == target
    assert a.kind == "implements"
    # draft flag gets removed (sentinel None)
    assert a.patch["draft"] is None
    assert a.patch["implemented_by"] == "[[2026-04-17-s1]]"
    assert a.patch["implemented_at"] == "2026-04-17"


def test_canonical_target_no_frontmatter_effect(wiki):
    """When target has no `draft:` flag, implements is a pure back-link."""
    _write_concept(wiki, "already-canonical")
    _write_session(wiki, "s2", ["already-canonical"])
    actions = _pass_implements(wiki)
    # First run: curator adds back-link stamp (implemented_by/at).
    # Subsequent runs: idempotent.
    if actions:
        assert len(actions) == 1
        assert "draft" not in actions[0].patch  # nothing to promote
    # Idempotency check — apply any action, re-run, expect no further actions.
    from lore_curator.core import _apply_patch
    if actions:
        new_text = _apply_patch(
            actions[0].path.read_text(), actions[0].patch
        )
        actions[0].path.write_text(new_text)
    assert _pass_implements(wiki) == []


def test_superseded_by_marker_writes_relation(wiki):
    _write_concept(wiki, "old-idea")
    _write_concept(wiki, "new-idea")
    _write_session(wiki, "s3", ["old-idea:superseded-by:new-idea"])
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "mark_superseded"
    assert a.patch == {"superseded_by": "[[new-idea]]"}


def test_partial_marker_no_frontmatter_effect(wiki):
    _write_concept(wiki, "gappy-concept", draft=True)
    _write_session(wiki, "s4", ["gappy-concept:partial"])
    assert _pass_implements(wiki) == []


def test_abandoned_marker_no_frontmatter_effect(wiki):
    _write_concept(wiki, "dropped-concept", draft=True)
    _write_session(wiki, "s5", ["dropped-concept:abandoned"])
    assert _pass_implements(wiki) == []


def test_unresolvable_slug_is_silent(wiki):
    _write_session(wiki, "s6", ["does-not-exist"])
    assert _pass_implements(wiki) == []


def test_mixed_list_only_promotes_drafts(wiki):
    _write_concept(wiki, "alpha", draft=True)
    _write_concept(wiki, "beta")  # already canonical
    _write_concept(wiki, "old")
    _write_concept(wiki, "new")
    _write_session(wiki, "s7", [
        "alpha",
        "beta:partial",                 # no effect
        "ghost",                        # unresolvable
        "old:superseded-by:new",
    ])
    actions = _pass_implements(wiki)
    kinds = {a.path.stem: a.kind for a in actions}
    # alpha promoted (draft → canonical); old gets superseded_by.
    # beta is canonical with :partial marker → no action.
    assert "alpha" in kinds
    assert kinds["alpha"] == "implements"
    assert kinds["old"] == "mark_superseded"
    assert "beta" not in kinds
    assert "ghost" not in kinds


def test_idempotent_on_second_run(wiki):
    _write_concept(wiki, "promote-me", draft=True)
    _write_session(wiki, "s8", ["promote-me"])
    from lore_curator.core import _apply_patch
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    # Apply the patch and re-run.
    target = actions[0].path
    target.write_text(_apply_patch(target.read_text(), actions[0].patch))
    assert _pass_implements(wiki) == []


def test_superseded_by_is_idempotent(wiki):
    target = _write_concept(
        wiki, "old-idea",
        extra_fm='superseded_by: "[[new-idea]]"\n',
    )
    _write_concept(wiki, "new-idea")
    _write_session(wiki, "s9", ["old-idea:superseded-by:new-idea"])
    assert _pass_implements(wiki) == []
    # Target intact
    assert "superseded_by" in target.read_text()


def test_no_implements_no_actions(wiki):
    _write_concept(wiki, "untouched")
    _write_session(wiki, "quiet", [])
    assert _pass_implements(wiki) == []


def test_non_session_notes_are_ignored(wiki):
    _write_concept(wiki, "target")
    odd = wiki / "sessions" / "not-a-session.md"
    odd.write_text(
        """---
type: concept
implements: [target]
---
body
"""
    )
    assert _pass_implements(wiki) == []
