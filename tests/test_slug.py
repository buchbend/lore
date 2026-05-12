"""Tests for ``lore_curator.session_filer._slug``."""
from __future__ import annotations

from lore_curator.session_filer import _slug


def test_slug_sanitises_title():
    """Title with special chars produces clean hyphen-separated slug."""
    s = _slug("Add: Ledger! Now?")
    assert s == "add-ledger-now"
    assert "--" not in s
    assert all(c.isalnum() or c == "-" for c in s)


def test_slug_word_boundary_truncates_at_dash():
    """Long titles truncate at a hyphen boundary, never mid-word.

    The old hard ``[:60]`` produced filenames like
    ``"...rebase-onto-pha"`` (cut mid-word in "Phase"). The fix walks back
    to the last hyphen that fits.
    """
    title = (
        "Ship v0.13.1 — fix #29 mid-stream curator notes "
        "(rebase onto Phase 12)"
    )
    s = _slug(title)
    assert len(s) <= 60
    assert not s.endswith("-")
    full = "ship-v0-13-1-fix-29-mid-stream-curator-notes-rebase-onto-phase-12"
    assert full.startswith(s + "-"), (
        f"slug {s!r} should be a prefix of {full!r} stopping at a hyphen"
    )


def test_slug_short_title_unchanged():
    """A short, already-clean title passes through untouched."""
    assert _slug("Add Ledger Feature") == "add-ledger-feature"


def test_slug_hard_cut_when_no_word_boundary():
    """One giant unbroken token has no boundary to walk back to — fall
    back to the hard ``[:60]`` cut so we always produce *some* slug."""
    s = _slug("a" * 80)
    assert len(s) == 60
    assert s == "a" * 60
