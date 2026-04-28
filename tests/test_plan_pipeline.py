"""End-to-end tests for the plans/ pipeline: parser → writer → step_status.

This file consolidates what the original 13-file test plan called
``test_plan_parser.py`` + the parser slice of ``test_plan_pipeline.py``.
The remaining seams (writer collision/concurrent/canonicalization,
hook handler edges, project stub, SessionStart rendering) live in
their own files.
"""
from __future__ import annotations

import pytest

from lore_core.plans.parser import parse, parse_payload
from lore_core.plans.types import PlanStep, StructuredPlan


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------


def test_slug_from_h1_heading() -> None:
    plan = parse("# Refactor Auth\n\nbody")
    assert plan.slug == "refactor-auth"
    assert plan.title == "Refactor Auth"


def test_slug_fallback_when_no_h1() -> None:
    """No H1 → first 40 chars of plain text → slugified."""
    plan = parse("Just some plan text without a heading line\n\nmore body")
    assert plan.slug.startswith("just-some-plan-text")
    assert plan.title == ""


def test_slug_override_wins() -> None:
    plan = parse("# Refactor Auth\n\nbody", slug_override="custom-slug")
    assert plan.slug == "custom-slug"


def test_slug_path_traversal_safe() -> None:
    """Adversarial slug input must not yield a path-escaping slug.

    The slugify pipeline already strips ``[^\\w\\s-]`` so slashes/dots
    drop out; this test pins that property at the parser boundary.
    """
    plan = parse("# ../../etc/passwd\n\nbody")
    assert "/" not in plan.slug
    assert ".." not in plan.slug


def test_slug_unicode_safe() -> None:
    """Unicode in title degrades to a safe slug, doesn't crash."""
    plan = parse("# 日本語 plan\n\nbody")
    # Slugify strips non-word chars; we accept any non-empty result.
    assert plan.slug


def test_slug_unnamed_when_truly_empty() -> None:
    plan = parse("")
    assert plan.slug == "unnamed-plan"


# ---------------------------------------------------------------------------
# Mode 1: headings
# ---------------------------------------------------------------------------


def test_mode_headings_step_prefix() -> None:
    text = """# Plan

Intro paragraph.

### Step 1: Foo
do foo

### Step 2: Bar
do bar
"""
    plan = parse(text)
    assert plan.mode == "headings"
    assert [s.id for s in plan.steps] == ["s1", "s2"]
    assert plan.steps[0].title == "Foo"
    assert "do foo" in plan.steps[0].body
    assert plan.body_intro == "Intro paragraph."


def test_mode_headings_phase_prefix() -> None:
    """`### Phase N` is also a step heading."""
    text = "# P\n\n### Phase 1: setup\nx\n\n### Phase 2: rollout\ny\n"
    plan = parse(text)
    assert plan.mode == "headings"
    assert plan.steps[0].title == "setup"


def test_mode_headings_s_prefix() -> None:
    """`### s1: ...` (already-canonical anchor form) also works."""
    text = "# P\n\n### s1: alpha\na\n\n### s2: beta\nb\n"
    plan = parse(text)
    assert plan.mode == "headings"
    assert plan.steps[1].title == "beta"


def test_mode_headings_h2_numeric_prefix() -> None:
    """`## 1. ...` form."""
    text = "# P\n\n## 1. Alpha\na\n\n## 2. Beta\nb\n"
    plan = parse(text)
    assert plan.mode == "headings"
    assert len(plan.steps) == 2


def test_mode_headings_single_heading_falls_through_to_single_mode() -> None:
    """One step heading is not enough — needs ≥2 siblings."""
    text = "# P\n\n### Step 1: only one\nbody\n"
    plan = parse(text)
    assert plan.mode == "single"
    assert len(plan.steps) == 1


# ---------------------------------------------------------------------------
# Mode 2: top-level numbered list
# ---------------------------------------------------------------------------


def test_mode_list_basic() -> None:
    text = """# Plan

Some intro.

1. First step
2. Second step
3. Third step
"""
    plan = parse(text)
    assert plan.mode == "list"
    assert [s.id for s in plan.steps] == ["s1", "s2", "s3"]
    assert plan.steps[0].title == "First step"


def test_mode_list_with_indented_continuations() -> None:
    """Indented lines under a list item are part of the step body, not new items."""
    text = """1. Big step
   - sub-bullet
   - another sub-bullet
2. Second
"""
    plan = parse(text)
    assert plan.mode == "list"
    assert len(plan.steps) == 2
    assert "sub-bullet" in plan.steps[0].body


def test_mode_list_single_item_falls_through_to_single() -> None:
    """One numbered item is not enough — needs ≥2 siblings."""
    text = "# P\n\n1. Only one item\n"
    plan = parse(text)
    assert plan.mode == "single"


# ---------------------------------------------------------------------------
# Mode 3: single (degenerate)
# ---------------------------------------------------------------------------


def test_mode_single_no_steps_detected() -> None:
    text = "# Plan\n\nJust some prose with no obvious steps.\nMore prose.\n"
    plan = parse(text)
    assert plan.mode == "single"
    assert len(plan.steps) == 1
    assert plan.steps[0].id == "s1"
    assert "Just some prose" in plan.steps[0].body


def test_mode_single_with_only_title() -> None:
    """Title only, no body → zero steps, body_intro carries the title."""
    plan = parse("# Title only\n")
    assert plan.mode == "single"
    assert plan.steps == []


def test_mode_single_empty_input() -> None:
    plan = parse("")
    assert plan.mode == "single"
    assert plan.steps == []


# ---------------------------------------------------------------------------
# Code-fence pathology — `1. foo` inside a fence must NOT trigger list mode
# ---------------------------------------------------------------------------


def test_fenced_numbered_list_does_not_fire_list_mode() -> None:
    """The classic LLM-output pathology: numbered example inside a fence."""
    text = """# Plan

Here's an example:

```python
# 1. foo
# 2. bar
1. inside_code = True
2. also_inside = True
```

That's it.
"""
    plan = parse(text)
    # No real list outside the fence → falls through to single mode.
    assert plan.mode == "single"


def test_fenced_step_headings_do_not_fire_headings_mode() -> None:
    """Heading-shaped lines inside a fence are inert."""
    text = """# Plan

```
### Step 1: not real
### Step 2: also not real
```

Body proper.
"""
    plan = parse(text)
    assert plan.mode == "single"


def test_real_steps_after_fence_still_detected() -> None:
    """Fence-stripping must not eat real subsequent step headings."""
    text = """# Plan

```python
1. example
```

### Step 1: real
do this

### Step 2: also real
do that
"""
    plan = parse(text)
    assert plan.mode == "headings"
    assert len(plan.steps) == 2


def test_tilde_fences_also_stripped() -> None:
    text = """# Plan

~~~
1. fake
2. fake
~~~

OK
"""
    plan = parse(text)
    assert plan.mode == "single"


# ---------------------------------------------------------------------------
# Permissive payload-shape fallback
# ---------------------------------------------------------------------------


def test_parse_payload_documented_field() -> None:
    text, source = parse_payload({"tool_input": {"plan": "## Heading\nbody"}})
    assert text == "## Heading\nbody"
    assert source == "tool_input.plan"


def test_parse_payload_alternate_field_names() -> None:
    """Try plan_text, content, text, markdown in order before falling back."""
    text, source = parse_payload({"tool_input": {"plan_text": "alt"}})
    assert text == "alt"
    assert source == "tool_input.plan_text"

    text, source = parse_payload({"tool_input": {"content": "from content"}})
    assert source == "tool_input.content"


def test_parse_payload_fallback_on_long_string() -> None:
    """Unknown field name with a ≥100-char string still wins via fallback."""
    long_plan = "# Long plan\n" + ("body line\n" * 20)  # well over 100 chars
    text, source = parse_payload({"tool_input": {"weird_new_field": long_plan}})
    assert text == long_plan
    assert "fallback" in source


def test_parse_payload_fallback_rejects_short_strings() -> None:
    """Short strings that happen to live alongside the plan are NOT picked up."""
    text, source = parse_payload({"tool_input": {"id": "abc123", "session": "x"}})
    assert text is None
    assert source == "no-match"


def test_parse_payload_no_tool_input() -> None:
    """Neither ``tool_input`` nor ``tool_response`` carries a plan."""
    text, source = parse_payload({"other": "stuff"})
    assert text is None
    assert source == "no-match"


def test_parse_payload_empty_string_skipped() -> None:
    """Empty/whitespace-only documented field falls through to next."""
    text, source = parse_payload(
        {"tool_input": {"plan": "   ", "plan_text": "real"}}
    )
    assert text == "real"


def test_parse_payload_falls_back_to_tool_response() -> None:
    """Empty ``tool_input`` + plan in ``tool_response.plan`` — captured 2026-04-28.

    Why: when the model calls ExitPlanMode with no ``plan`` argument, Claude
    Code's harness reads the plan file content and surfaces it via
    ``tool_response.plan``; ``tool_input`` arrives as an empty dict. Older
    parse_payload only inspected ``tool_input`` so plans were silently
    orphan-dumped.
    """
    plan_text = (
        "# Refactor authentication\n\n## Steps\n\n### s1: thing\n- thing\n"
    )
    text, source = parse_payload(
        {
            "tool_input": {},
            "tool_response": {
                "plan": plan_text,
                "isAgent": False,
                "filePath": "/x.md",
                "hasTaskTool": True,
            },
        }
    )
    assert text == plan_text
    assert source == "tool_response.plan"


def test_parse_payload_prefers_tool_input_over_tool_response() -> None:
    """When both sections have a plan, ``tool_input`` wins (model intent first)."""
    text, source = parse_payload(
        {
            "tool_input": {"plan": "from input"},
            "tool_response": {"plan": "from response"},
        }
    )
    assert text == "from input"
    assert source == "tool_input.plan"


# ---------------------------------------------------------------------------
# Mixed: real-world ExitPlanMode-shaped output
# ---------------------------------------------------------------------------


def test_realistic_exitplanmode_output() -> None:
    """A plan shaped like what Claude Code actually produces."""
    text = """# Refactor authentication

Migrate from session tokens to OIDC across the auth service.

## Steps

### Step 1: Add OIDC provider configuration
- Add provider config to settings
- Wire up the discovery endpoint

### Step 2: Implement new login flow
- Add the new endpoint
- Update the redirect URI handling

### Step 3: Migrate existing sessions
- Read all active session tokens
- Issue OIDC refresh tokens

### Step 4: Remove old token code
- Drop the legacy verifier
- Update tests
"""
    plan = parse(text)
    assert plan.slug == "refactor-authentication"
    assert plan.mode == "headings"
    assert plan.title == "Refactor authentication"
    assert len(plan.steps) == 4
    assert [s.id for s in plan.steps] == ["s1", "s2", "s3", "s4"]
    assert "Migrate from session tokens" in plan.body_intro
