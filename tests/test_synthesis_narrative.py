"""Pre-P2 narrative-field tests (issue #94).

Asserted on ``_phase2_tool_schema`` + ``_phase2_prompt`` + the
two-region wrapping that put the model's narrative into the human-only
region. P2 collapses those into a single ``narrative`` field in the
reload-safe region; the schema/prompt symbols this module imported
are gone. Replacement coverage lives in
``tests/test_synthesis_p2.py``.

See branch ``pr/p2-style``.
"""
from __future__ import annotations

import pytest

pytest.skip(
    "Pre-P2 narrative tests — replaced by tests/test_synthesis_p2.py. "
    "See branch pr/p2-style.",
    allow_module_level=True,
)


# ---------------------------------------------------------------------------
# Schema — narrative field present in both shapes
# ---------------------------------------------------------------------------

def test_schema_work_shape_includes_narrative_field():
    schema = _phase2_tool_schema(_make_shape(has_edits=True))
    props = schema["input_schema"]["properties"]
    assert "narrative" in props
    assert props["narrative"]["type"] == "string"
    # Narrative is OPTIONAL — must not be in the required list.
    assert "narrative" not in schema["input_schema"]["required"]


def test_schema_discussion_shape_includes_narrative_field():
    schema = _phase2_tool_schema(_make_shape(has_edits=False))
    props = schema["input_schema"]["properties"]
    assert "narrative" in props
    assert props["narrative"]["type"] == "string"
    assert "narrative" not in schema["input_schema"]["required"]


def test_schema_none_shape_includes_narrative_field():
    """Tests / callers passing ``shape=None`` (work-shape default) also
    get the narrative slot — uniform schema."""
    schema = _phase2_tool_schema(None)
    assert "narrative" in schema["input_schema"]["properties"]


def test_schema_narrative_has_no_length_cap():
    """Narrative is free-form; no maxLength on the string."""
    schema = _phase2_tool_schema(_make_shape(has_edits=True))
    narrative_prop = schema["input_schema"]["properties"]["narrative"]
    assert "maxLength" not in narrative_prop


# ---------------------------------------------------------------------------
# Prompt — narrative clause + tightened takeaways
# ---------------------------------------------------------------------------

def test_prompt_work_shape_includes_narrative_clause():
    prompt = _phase2_prompt(
        turns_text="some turns",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=True),
    )
    # Anchor phrases from the PRD's prompt-contract block.
    assert "narrative" in prompt
    assert "telling a colleague" in prompt
    assert "Calibrate length to substance" in prompt
    # Voice rule.
    assert "tentative" in prompt
    # Anti-padding rule.
    assert "Padding is worse than silence" in prompt
    # Anti-duplication rule.
    assert "Do NOT re-state files modified" in prompt


def test_prompt_discussion_shape_includes_narrative_clause():
    prompt = _phase2_prompt(
        turns_text="some turns",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=False),
    )
    assert "telling a colleague" in prompt
    assert "Calibrate length to substance" in prompt


def test_prompt_discussion_takeaways_clause_tightened_to_self_contained():
    """Discussion ``summary_takeaways`` clause must teach the cold-reader
    test — takeaways are self-contained references, not session-jargon."""
    prompt = _phase2_prompt(
        turns_text="some turns",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        shape=_make_shape(has_edits=False),
    )
    # The tightening anchor — either the phrase "self-contained" or the
    # explicit cold-reader test must appear in the discussion prompt.
    assert (
        "self-contained" in prompt
        or "cold reader" in prompt
        or "colleague who was not in the session" in prompt
    )


# ---------------------------------------------------------------------------
# Renderer — marker lands iff narrative non-empty
# ---------------------------------------------------------------------------

def test_phase2_apply_emits_marker_when_narrative_non_empty(
    lore_root, patch_collectors, monkeypatch,
):
    _, _, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {
        "title": "auth handler refactor",
        "description": "Rebuilt auth.py against the policy decorator.",
        "summary_lede": "auth.py now uses the policy decorator.",
        "summary_outcomes": ["callbacks pulled out", "tests green"],
        "worked_on": ["**auth.py** — pulled callbacks"],
        "loose_ends": [],
        "narrative": (
            "We leaned toward the decorator chain after the callback "
            "approach forced too much glue code. Tried two variants of "
            "the policy lookup before settling on the registry shape."
        ),
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    assert outcome.phase2_completed is True
    text = outcome.stub_path.read_text()
    assert HUMAN_ONLY_MARKER in text
    assert "decorator chain" in text


def test_phase2_apply_no_marker_when_narrative_empty(
    lore_root, patch_collectors, monkeypatch,
):
    """Curator emits an empty narrative for pure-grind sessions; the
    marker MUST be omitted (clean omission, not an empty section)."""
    _, _, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {
        "title": "tiny cleanup",
        "description": "d",
        "summary_lede": "tiny touch-up to auth.py.",
        "summary_outcomes": [],
        "worked_on": ["**auth.py** — touched"],
        "loose_ends": [],
        "narrative": "",
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = outcome.stub_path.read_text()
    assert HUMAN_ONLY_MARKER not in text


def test_phase2_apply_no_marker_when_narrative_omitted(
    lore_root, patch_collectors, monkeypatch,
):
    """Narrative is optional. A composed dict that doesn't carry the
    key at all (legacy / older code paths) also produces no marker."""
    _, _, sidecar_path = _seed_stub(lore_root, monkeypatch)
    composed = {
        "title": "no-narrative session",
        "description": "d",
        "summary_lede": "small fix.",
        "summary_outcomes": [],
        "worked_on": ["**x.py** — touched"],
        "loose_ends": [],
        # No "narrative" key at all.
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = outcome.stub_path.read_text()
    assert HUMAN_ONLY_MARKER not in text


def test_end_to_end_split_recovers_both_regions(
    lore_root, patch_collectors, monkeypatch,
):
    """Round-trip contract: render → strip frontmatter → split_regions
    recovers structured body + narrative."""
    _, _, sidecar_path = _seed_stub(lore_root, monkeypatch)
    narrative_text = (
        "### Investigation trail\n\n"
        "We chased the wrong cache key for an hour before noticing the "
        "stale namespace.\n\n"
        "### Experiments\n\n"
        "- Tried bypassing the cache wrapper entirely — too slow.\n"
        "- Tried a custom hasher — fixed the collision.\n"
    )
    composed = {
        "title": "cache investigation",
        "description": "Tracked a stale-key bug to the namespace prefix.",
        "summary_lede": "Cache key collision traced to namespace prefix mismatch.",
        "summary_outcomes": ["fixed in cache.py", "regression test added"],
        "worked_on": ["**cache.py** — fixed key hasher"],
        "loose_ends": [],
        "narrative": narrative_text,
    }
    llm = _FakeLlmClient(_ok_responder(composed))
    outcome = synth_and_close(
        sidecar_path,
        lore_root=lore_root,
        wiki_root=lore_root / "wiki" / "private",
        llm_client=llm,
        model="m",
    )
    text = outcome.stub_path.read_text()
    body = strip_frontmatter(text)
    reload_safe, human_only = split_regions(body)
    # Structured fields land in reload-safe.
    assert "## Summary" in reload_safe
    assert "Cache key collision" in reload_safe
    # Narrative content lands in human-only.
    assert human_only is not None
    assert "Investigation trail" in human_only
    assert "stale namespace" in human_only
    # No leakage in either direction.
    assert "Investigation trail" not in reload_safe
    assert "## Summary" not in human_only
