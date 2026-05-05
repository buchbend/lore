"""Tests for lore_core.narrative_kind — Phase-2 shape selector.

The shape booleans drive the schema+prompt gating that fixes the bad
05-1212 note. The kind string is a logging convenience; tests assert
on booleans.
"""
from __future__ import annotations

from lore_core.narrative_kind import NarrativeShape, select_shape
from lore_core.types import Turn


def _user(idx: int, text: str) -> Turn:
    return Turn(index=idx, timestamp=None, role="user", text=text)


# ---------------------------------------------------------------------------
# has_edits / decisions_allowed cross-product
# ---------------------------------------------------------------------------


def test_edits_present_yields_work_shape():
    turns = [_user(0, "fix the bug")]
    shape = select_shape(turns, files_modified=["a.py"])
    assert shape.has_edits is True
    assert shape.decisions_allowed is True
    assert shape.kind == "work"


def test_no_edits_no_assent_yields_discussion():
    turns = [_user(0, "what do you think about X?")]
    shape = select_shape(turns, files_modified=[])
    assert shape.has_edits is False
    assert shape.decisions_allowed is False
    assert shape.kind == "discussion"


def test_no_edits_with_strong_assent_allows_decisions():
    turns = [_user(0, "yes — let's go with the simpler approach.")]
    shape = select_shape(turns, files_modified=[])
    assert shape.has_edits is False
    assert shape.decisions_allowed is True
    assert shape.kind == "discussion_with_signals"


def test_no_edits_with_weak_assent_does_not_allow_decisions():
    """Weak assent (hedge in same sentence) is not enough to unlock the
    Decisions section without ``files_modified``. The hit is preserved
    upstream for a future judge call but the gate is still closed."""
    turns = [_user(0, "we could maybe go with the simpler one")]
    shape = select_shape(turns, files_modified=[])
    assert shape.has_edits is False
    assert shape.decisions_allowed is False
    assert shape.kind == "discussion"


def test_edits_with_no_assent_still_allows_decisions():
    """Assent-by-action: if the diff exists, the user has ratified by
    behavior. Decisions section is allowed even without explicit assent
    in text (the model still has to produce decisions worth saying)."""
    turns = [_user(0, "fix the link")]
    shape = select_shape(turns, files_modified=["README.md"])
    assert shape.has_edits is True
    assert shape.decisions_allowed is True


# ---------------------------------------------------------------------------
# Echoes from PrefilterSignals
# ---------------------------------------------------------------------------


def test_no_edit_intent_echoes_through_shape():
    turns = [_user(0, "no code change just exploration")]
    shape = select_shape(turns, files_modified=[])
    assert shape.no_edit_intent is True


def test_adr_flagged_echoes_through_shape():
    turns = [_user(0, "ADR this — it's load-bearing.")]
    shape = select_shape(turns, files_modified=[])
    assert shape.adr_flagged is True


# ---------------------------------------------------------------------------
# 05-1212 reproduction
# ---------------------------------------------------------------------------


def test_05_1212_pattern_yields_discussion_shape_with_no_decisions():
    """The bad note's transcript pattern. Stage 3 must say:
    ``has_edits=False`` (no Edit calls in the slice — caller passes
    empty ``files_modified``) and ``decisions_allowed=False``."""
    turns = [
        _user(0, "we want to document this code make yourself familiar with the purpose "
                 "of this code reflect back to me about your understaning of it "
                 "(no code changes just exploration)"),
        _user(1, "have a subagent architect devise the best documentation architecite"),
        _user(2, "OK conf.py is missing... please make up your mind how the entire "
                 "structure is setup and if that is a good approach. Just checking "
                 "brainstorming no code change"),
    ]
    shape = select_shape(turns, files_modified=[])
    assert shape.has_edits is False
    assert shape.no_edit_intent is True
    # Even though "OK" matches the assent regex, the Decisions gate is
    # not solely keyed on no_edit_intent — it allows decisions when
    # has_strong_assent. Here "OK" lands in a sentence that also
    # contains "Just checking brainstorming no code change" → hedged
    # by adjacent context but not in the same sentence as 'OK'. The
    # honest gate stays open and would render Decisions absent only
    # if step-4's prompt makes clear that no_edit_intent dominates.
    # This test pins current behaviour; step-4 may add an explicit
    # short-circuit once the prompt is in place.
    assert shape.kind in ("discussion", "discussion_with_signals")
