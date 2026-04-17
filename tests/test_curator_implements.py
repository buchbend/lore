"""Tests for the curator's `implements:` cross-reference pass.

Contract (from concepts/lore/implements-cross-reference):
  - Session note's `implements: [slug, slug:partial, ...]` flips target
    concept/decision status.
  - Target gets `implemented_at` (session's created date) + wikilink
    back in `implemented_by`.
  - Idempotent: same session, same target, same status → no action.
  - Unresolvable slugs are silently skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_curator.core import _parse_implements_entry, _pass_implements

# ---------- parser ----------


@pytest.mark.parametrize("entry,expected", [
    ("my-concept", ("my-concept", "implemented", None)),
    ("my-concept:partial", ("my-concept", "partial", None)),
    ("my-concept:abandoned", ("my-concept", "abandoned", None)),
    ("my-concept:superseded-by:other-slug",
     ("my-concept", "superseded", "other-slug")),
    ("slug-with-dashes", ("slug-with-dashes", "implemented", None)),
    # Unknown trailing state → fall through to default
    ("slug:weird-state", ("slug:weird-state", "implemented", None)),
    # Whitespace tolerance
    ("  my-concept  ", ("my-concept", "implemented", None)),
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
    # Point LORE_ROOT at the tmp vault so discover_notes' wiki_root resolution
    # doesn't matter; _pass_implements takes the wiki path directly.
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return w


def _write_concept(wiki: Path, slug: str, status: str = "proposed") -> Path:
    fpath = wiki / "concepts" / f"{slug}.md"
    fpath.write_text(
        f"""---
schema_version: 2
type: concept
created: 2026-01-01
last_reviewed: 2026-01-01
status: {status}
description: "A test concept."
tags: [topic/test]
---
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
status: stable
description: "A test session."
implements:
{implements_yaml}
---
# Session
body
"""
    )
    return fpath


def test_single_implements_flips_to_implemented(wiki):
    target = _write_concept(wiki, "some-proposed-concept")
    _write_session(wiki, "s1", ["some-proposed-concept"])
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    a = actions[0]
    assert a.path == target
    assert a.patch["status"] == "implemented"
    assert a.patch["implemented_by"] == "[[2026-04-17-s1]]"
    assert a.patch["implemented_at"] == "2026-04-17"


def test_partial_state(wiki):
    _write_concept(wiki, "gappy-concept")
    _write_session(wiki, "s2", ["gappy-concept:partial"])
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    assert actions[0].patch["status"] == "partial"


def test_abandoned_state(wiki):
    _write_concept(wiki, "dropped-concept")
    _write_session(wiki, "s3", ["dropped-concept:abandoned"])
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    assert actions[0].patch["status"] == "abandoned"


def test_superseded_state_includes_superseded_by(wiki):
    _write_concept(wiki, "old-idea")
    _write_concept(wiki, "new-idea")
    _write_session(wiki, "s4", ["old-idea:superseded-by:new-idea"])
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    patch = actions[0].patch
    assert patch["status"] == "superseded"
    assert patch["superseded_by"] == "[[new-idea]]"


def test_unresolvable_slug_is_silent(wiki):
    _write_session(wiki, "s5", ["does-not-exist"])
    actions = _pass_implements(wiki)
    assert actions == []


def test_mixed_list(wiki):
    _write_concept(wiki, "alpha")
    _write_concept(wiki, "beta")
    _write_concept(wiki, "gamma")
    _write_session(wiki, "s6", ["alpha", "beta:partial", "gamma:abandoned", "ghost"])
    actions = _pass_implements(wiki)
    kinds = {a.path.stem: a.patch["status"] for a in actions}
    assert kinds == {"alpha": "implemented", "beta": "partial", "gamma": "abandoned"}


def test_idempotent_when_already_flipped(wiki):
    target = _write_concept(wiki, "already-done")
    # Simulate a prior curator run: target already has the patched metadata
    target.write_text(
        """---
schema_version: 2
type: concept
created: 2026-01-01
last_reviewed: 2026-01-01
status: implemented
implemented_by: "[[2026-04-17-s7]]"
implemented_at: 2026-04-17
description: "A test concept."
tags: [topic/test]
---
body
"""
    )
    _write_session(wiki, "s7", ["already-done"])
    actions = _pass_implements(wiki)
    assert actions == []


def test_different_session_same_target_rewrites(wiki):
    """If a newer session implements the same target, the pass proposes
    an update (overwriting implemented_by with the newer session)."""
    target = _write_concept(wiki, "twice-done")
    target.write_text(
        """---
schema_version: 2
type: concept
created: 2026-01-01
last_reviewed: 2026-01-01
status: implemented
implemented_by: "[[2026-01-05-old-session]]"
description: "A test concept."
tags: [topic/test]
---
body
"""
    )
    _write_session(wiki, "new-session", ["twice-done"], created="2026-04-17")
    actions = _pass_implements(wiki)
    assert len(actions) == 1
    assert actions[0].patch["implemented_by"] == "[[2026-04-17-new-session]]"


def test_no_implements_no_actions(wiki):
    _write_concept(wiki, "untouched")
    _write_session(wiki, "quiet", [])
    actions = _pass_implements(wiki)
    assert actions == []


def test_non_session_notes_are_ignored(wiki):
    """A concept note that happens to contain an `implements:` field
    should not be processed — only session notes drive this pass."""
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
    actions = _pass_implements(wiki)
    assert actions == []
