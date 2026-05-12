"""Tests for lib/lore_core/regions.py — two-region note primitives."""
from __future__ import annotations

import pytest

from lore_core.regions import (
    HUMAN_ONLY_MARKER,
    redact_human_only,
    render_regions,
    split_regions,
)


# ---------------------------------------------------------------------------
# split_regions — basic
# ---------------------------------------------------------------------------

def test_split_no_marker_returns_whole_body():
    body = "# Title\n\nSome content.\n"
    reload_safe, human_only = split_regions(body)
    assert reload_safe == body
    assert human_only is None


def test_split_with_marker():
    body = (
        "# Title\n\n"
        "Reload-safe content.\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "Human-only scratch.\n"
    )
    reload_safe, human_only = split_regions(body)
    assert reload_safe == "# Title\n\nReload-safe content.\n"
    assert human_only == "Human-only scratch.\n"


def test_split_empty_body():
    reload_safe, human_only = split_regions("")
    assert reload_safe == ""
    assert human_only is None


def test_split_marker_only():
    body = f"{HUMAN_ONLY_MARKER}\n"
    reload_safe, human_only = split_regions(body)
    assert reload_safe == ""
    assert human_only == ""


def test_split_human_only_with_sub_headings():
    body = (
        "Public lede.\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "## My private notes\n\n"
        "- thought 1\n"
        "- thought 2\n"
    )
    reload_safe, human_only = split_regions(body)
    assert reload_safe == "Public lede.\n"
    assert "## My private notes" in human_only
    assert "thought 2" in human_only


# ---------------------------------------------------------------------------
# split_regions — forgiveness
# ---------------------------------------------------------------------------

def test_split_multiple_markers_first_wins():
    body = (
        "A\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "B\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "C\n"
    )
    reload_safe, human_only = split_regions(body)
    assert reload_safe == "A\n"
    # Subsequent markers stay verbatim in the human-only region.
    assert human_only == f"B\n{HUMAN_ONLY_MARKER}\nC\n"


def test_split_whitespace_tolerant_marker():
    body = f"A\n   {HUMAN_ONLY_MARKER}   \nB\n"
    reload_safe, human_only = split_regions(body)
    assert reload_safe == "A\n"
    assert human_only == "B\n"


# ---------------------------------------------------------------------------
# split_regions — code-fence aware
# ---------------------------------------------------------------------------

def test_split_marker_inside_fenced_block_is_not_a_boundary():
    body = (
        "Public.\n\n"
        "```markdown\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "```\n\n"
        "Still public.\n"
    )
    reload_safe, human_only = split_regions(body)
    assert reload_safe == body
    assert human_only is None


def test_split_real_marker_after_closed_fence():
    body = (
        "Public.\n\n"
        "```python\n"
        f"# {HUMAN_ONLY_MARKER}\n"
        "```\n\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "Private.\n"
    )
    reload_safe, human_only = split_regions(body)
    assert "Public." in reload_safe
    assert "```python" in reload_safe
    assert HUMAN_ONLY_MARKER not in reload_safe.split("```")[2] if "```" in reload_safe else True
    assert human_only == "Private.\n"


def test_split_tilde_fence_aware():
    body = (
        "Public.\n\n"
        "~~~\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "~~~\n"
    )
    reload_safe, human_only = split_regions(body)
    assert reload_safe == body
    assert human_only is None


# ---------------------------------------------------------------------------
# render_regions
# ---------------------------------------------------------------------------

def test_render_no_human_only_omits_marker():
    rendered = render_regions("Just reload-safe.\n", None)
    assert rendered == "Just reload-safe.\n"
    assert HUMAN_ONLY_MARKER not in rendered


def test_render_empty_human_only_omits_marker():
    rendered = render_regions("Just reload-safe.\n", "")
    assert rendered == "Just reload-safe.\n"
    assert HUMAN_ONLY_MARKER not in rendered


def test_render_with_human_only():
    rendered = render_regions("Public.\n", "Private.\n")
    assert HUMAN_ONLY_MARKER in rendered
    assert rendered.startswith("Public.\n")
    assert rendered.endswith("Private.\n")


def test_render_handles_missing_trailing_newline():
    rendered = render_regions("Public.", "Private.\n")
    # Should still parse back cleanly.
    rs, ho = split_regions(rendered)
    assert "Public." in rs
    assert ho == "Private.\n"


# ---------------------------------------------------------------------------
# Round-trip identity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture",
    [
        "# Title\n\nNo marker here.\n",
        f"# Title\n\nPublic.\n{HUMAN_ONLY_MARKER}\nPrivate.\n",
        f"Public.\n{HUMAN_ONLY_MARKER}\n## Subheading\n\n- a\n- b\n",
        "```\n" + HUMAN_ONLY_MARKER + "\n```\nstill public\n",
    ],
)
def test_roundtrip_split_render_split_is_identity(fixture):
    rs1, ho1 = split_regions(fixture)
    rendered = render_regions(rs1, ho1)
    rs2, ho2 = split_regions(rendered)
    assert rs2 == rs1
    assert ho2 == ho1


# ---------------------------------------------------------------------------
# redact_human_only
# ---------------------------------------------------------------------------

def test_redact_no_marker_unchanged():
    body = "# Title\n\nNo marker.\n"
    assert redact_human_only(body) == body


def test_redact_drops_human_only_region():
    body = (
        "Public.\n"
        f"{HUMAN_ONLY_MARKER}\n"
        "Private — should not appear.\n"
    )
    out = redact_human_only(body)
    assert "Public." in out
    assert "Private" not in out
    assert HUMAN_ONLY_MARKER not in out
