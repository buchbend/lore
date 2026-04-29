"""Tests for the markdown shape classifier — :mod:`markdown_adapter`.

The classifier replaces the closed regex union in the old parser. Its
contract: given any markdown that might be a plan, return a typed
:class:`Shape` verdict the dispatcher can route on. No silent
fall-through to ``single`` mode; if classification fails, the verdict
says so loudly.

These tests pin the verdicts against representative AI-tool outputs:

* ``Shape*`` typed dataclasses cover the recognized cases.
* ``ShapeHierarchical`` covers the failing plan (``## Phase N`` +
  ``### N.M``) that triggered this whole redesign.
* ``ShapeUnknown`` / ``ShapeAmbiguous`` are explicit failure modes
  with diagnoses agents can act on.
"""
from __future__ import annotations

import pytest

from lore_core.plans.markdown_adapter import (
    classify,
    parse_markdown,
)
from lore_core.plans.shapes import (
    ShapeAmbiguous,
    ShapeATXSteps,
    ShapeCheckboxList,
    ShapeHierarchical,
    ShapeNumberedList,
    ShapeUnknown,
)


# ---------------------------------------------------------------------------
# ATX-step shapes — Phase, Step, P, S, bare numerals, etc.
# ---------------------------------------------------------------------------


class TestATXSteps:
    def test_phase_word_prefix(self) -> None:
        text = "# Plan\n\n### Phase 1: setup\nfoo\n\n### Phase 2: do\nbar\n"
        shape = classify(text)
        assert isinstance(shape, ShapeATXSteps)
        assert shape.level == 3
        assert len(shape.hits) == 2

    def test_step_word_prefix(self) -> None:
        text = "# P\n\n### Step 1: alpha\na\n\n### Step 2: beta\nb\n"
        shape = classify(text)
        assert isinstance(shape, ShapeATXSteps)
        assert len(shape.hits) == 2

    def test_p_letter_prefix(self) -> None:
        """Compact ``### P1`` form (Claude Code's phase shorthand)."""
        text = "# x\n\n### P1 — Foundation\na\n\n### P2 — Cleanup\nb\n"
        shape = classify(text)
        assert isinstance(shape, ShapeATXSteps)
        assert len(shape.hits) == 2

    def test_canonical_step_form(self) -> None:
        """``### step-1: …`` (already-canonical anchor) is recognized."""
        text = "# x\n\n### step-1: alpha\na\n\n### step-2: beta\nb\n"
        shape = classify(text)
        assert isinstance(shape, ShapeATXSteps)
        assert len(shape.hits) == 2

    def test_legacy_s_prefix(self) -> None:
        """Legacy ``### s1: …`` (pre-rename) is still recognized."""
        text = "# x\n\n### s1: alpha\na\n\n### s2: beta\nb\n"
        shape = classify(text)
        assert isinstance(shape, ShapeATXSteps)
        assert len(shape.hits) == 2

    def test_h2_numeric_prefix(self) -> None:
        text = "# x\n\n## 1. setup\na\n\n## 2. teardown\nb\n"
        shape = classify(text)
        assert isinstance(shape, ShapeATXSteps)
        assert shape.level == 2


# ---------------------------------------------------------------------------
# Hierarchical shapes — the failing plan that triggered this redesign
# ---------------------------------------------------------------------------


class TestHierarchical:
    def test_h2_phase_with_h3_dotted_steps(self) -> None:
        """``## Phase N`` containers + ``### N.M`` leaves → hierarchical.

        This is the exact shape of the plan that fell to single mode
        and reported ``steps_total: 0``. Classifier must recognize it.
        """
        text = """# Plan

## Phase 1 — Foundation

### 1.1 First step
body for 1.1

### 1.2 Second step
body for 1.2

## Phase 2 — Cleanup

### 2.1 Third step
body for 2.1

### 2.2 Fourth step
body for 2.2
"""
        shape = classify(text)
        assert isinstance(shape, ShapeHierarchical)
        assert shape.container_level == 2
        assert shape.item_level == 3
        # Leaves win as step hits — 4 total leaves across 2 phases.
        assert len(shape.hits) == 4

    def test_parse_markdown_lifts_leaves_with_group(self) -> None:
        """``parse_markdown`` returns flat ``step-1..step-4`` IDs and
        each step carries the parent container title in ``group``."""
        text = """# Plan

## Phase 1 — Foundation

### 1.1 alpha
a body

### 1.2 beta
b body

## Phase 2 — Cleanup

### 2.1 gamma
c body
"""
        shape = classify(text)
        steps, body_intro = parse_markdown(text, shape)
        assert [s.id for s in steps] == ["step-1", "step-2", "step-3"]
        # Group annotation lifted from H2 container titles.
        assert steps[0].group == "Phase 1 — Foundation"
        assert steps[1].group == "Phase 1 — Foundation"
        assert steps[2].group == "Phase 2 — Cleanup"
        # Titles stripped of ordinal prefix.
        assert steps[0].title == "alpha"
        assert steps[1].title == "beta"
        assert steps[2].title == "gamma"

    def test_orphan_leaf_dropped_when_major_doesnt_match_container(self) -> None:
        """``### 99.42`` inside ``## Phase 1`` is an orphan — drop it
        rather than promote it with a wrong group annotation.
        """
        text = """# Plan

## Phase 1 — Foundation

### 1.1 Real leaf
a body

### 99.42 Orphan leaf — wrong major
b body

### 1.2 Another real leaf
c body
"""
        shape = classify(text)
        assert isinstance(shape, ShapeHierarchical)
        # 2 valid leaves under Phase 1; the orphan ``99.42`` is dropped.
        assert len(shape.hits) == 2
        assert all(h["ordinal"] == 1 for h in shape.hits)

    def test_hierarchical_rejects_when_no_leaves_match_containers(self) -> None:
        """All H3 leaves' majors disagree with their container — no
        valid hierarchical mapping; classifier falls through to next probe.
        """
        text = """# Plan

## Phase 1 — Foundation

### 7.1 mismatched
### 8.2 mismatched
### 9.3 mismatched
"""
        shape = classify(text)
        # Could classify as flat ATX (3 hierarchical-prefixed H3s at one level)
        # or fall to ShapeUnknown — either is acceptable, but it MUST NOT
        # be ShapeHierarchical because the leaves don't belong to Phase 1.
        assert not isinstance(shape, ShapeHierarchical)

    def test_failing_plan_real_corpus(self) -> None:
        """The actual plan that triggered the redesign — read from the
        live vault if available, falls back to a representative sample."""
        from pathlib import Path
        candidate = Path(
            "/home/buchbend/git/vault/wiki/private/plans/"
            "lore-mcp-search-upgrade-push-back-items-small-differences.md"
        )
        if not candidate.exists():
            pytest.skip("real-corpus fixture not present in this checkout")
        text = candidate.read_text()
        # Strip frontmatter so the classifier sees plan body only.
        from lore_core.schema import strip_frontmatter
        body = strip_frontmatter(text)
        shape = classify(body)
        assert isinstance(shape, ShapeHierarchical), (
            f"failing plan should classify as ShapeHierarchical, got {type(shape).__name__}"
        )
        steps, _ = parse_markdown(body, shape)
        # Real plan has 10 leaf steps across 3 phases.
        assert len(steps) == 10
        # Phase containers folded into group annotations.
        assert any("Phase 1" in (s.group or "") for s in steps)
        assert any("Phase 2" in (s.group or "") for s in steps)
        assert any("Phase 3" in (s.group or "") for s in steps)


# ---------------------------------------------------------------------------
# Numbered-list shapes
# ---------------------------------------------------------------------------


class TestNumberedList:
    def test_top_level_numbered_list(self) -> None:
        text = "# Plan\n\n## Steps\n\n1. First\n2. Second\n3. Third\n"
        shape = classify(text)
        assert isinstance(shape, ShapeNumberedList)
        assert len(shape.hits) == 3


# ---------------------------------------------------------------------------
# Checkbox-list shapes
# ---------------------------------------------------------------------------


class TestCheckboxList:
    def test_simple_checkbox_list(self) -> None:
        text = (
            "# Plan\n\n## Steps\n\n"
            "- [ ] First step\n- [ ] Second step\n- [x] Third (already done)\n"
        )
        shape = classify(text)
        assert isinstance(shape, ShapeCheckboxList)
        assert len(shape.hits) == 3


# ---------------------------------------------------------------------------
# Failure modes — Unknown / Ambiguous
# ---------------------------------------------------------------------------


class TestFailureModes:
    def test_pure_prose_returns_unknown(self) -> None:
        """No headings, no list, no checkboxes → ShapeUnknown with diagnosis."""
        text = "# Plan\n\nJust some prose with no obvious steps.\nMore prose.\n"
        shape = classify(text)
        assert isinstance(shape, ShapeUnknown)
        assert shape.reason  # non-empty diagnosis

    def test_single_heading_not_enough(self) -> None:
        """One ``### Step 1:`` alone is not a step list — needs ≥2 siblings."""
        text = "# x\n\n### Step 1: only one\nbody\n"
        shape = classify(text)
        assert isinstance(shape, ShapeUnknown)

    def test_empty_body_returns_unknown(self) -> None:
        shape = classify("")
        assert isinstance(shape, ShapeUnknown)


# ---------------------------------------------------------------------------
# Code-fence safety
# ---------------------------------------------------------------------------


class TestRealCorpusRegression:
    """End-to-end: the failing plan that triggered this redesign now files cleanly.

    Pins the bug fix: ``## Phase N`` containers + ``### N.M`` leaves
    are recognized as :class:`ShapeHierarchical`, lifted into flat
    ``step-1..step-10`` IDs, written with ``confidence="high"``.
    """

    def test_failing_plan_full_pipeline(self, tmp_path, monkeypatch) -> None:
        from pathlib import Path
        from lore_core.plans.ingest import IngestSource, ingest_plan
        from lore_core.plans.writer import compute_source_hash, write_plan_note
        from lore_core.schema import strip_frontmatter

        candidate = Path(
            "/home/buchbend/git/vault/wiki/private/plans/"
            "lore-mcp-search-upgrade-push-back-items-small-differences.md"
        )
        if not candidate.exists():
            pytest.skip("real-corpus fixture not present in this checkout")
        body = strip_frontmatter(candidate.read_text())

        result = ingest_plan(
            IngestSource(kind="markdown", payload=body, producer="claude-code")
        )
        assert result.confidence == "high"
        assert result.adapter_name == "markdown/hierarchical"
        assert len(result.plan.steps) == 10
        # Flat canonical IDs.
        assert [s.id for s in result.plan.steps] == [
            f"step-{i}" for i in range(1, 11)
        ]
        # Phase group annotations preserved.
        groups = {s.group for s in result.plan.steps}
        assert any("Phase 1" in (g or "") for g in groups)
        assert any("Phase 2" in (g or "") for g in groups)
        assert any("Phase 3" in (g or "") for g in groups)

        # Full round-trip through the writer to confirm the canonical
        # body emission and frontmatter shape.
        wiki = tmp_path / "wiki" / "private"
        wiki.mkdir(parents=True)
        write_result = write_plan_note(
            wiki_root=wiki,
            plan=result.plan,
            source_hash=compute_source_hash(body),
            source_adapter="claude-code-hook",
        )
        written = write_result.path.read_text()
        assert "### step-1: AND-then-OR FTS query semantics" in written
        assert "### step-10:" in written
        # Trailer hint uses canonical anchor.
        assert "#step-<N>" in written


class TestIngestMarkdownPath:
    """Integration: ``ingest_plan(IngestSource(kind="markdown"))`` end-to-end."""

    def test_hierarchical_path_returns_high_confidence(self) -> None:
        from lore_core.plans.ingest import IngestSource, ingest_plan

        text = """# Plan

## Phase 1 — Foundation

### 1.1 alpha
a body

### 1.2 beta
b body

## Phase 2 — Cleanup

### 2.1 gamma
c body
"""
        result = ingest_plan(
            IngestSource(kind="markdown", payload=text, producer="cli")
        )
        assert result.confidence == "high"
        assert result.adapter_name == "markdown/hierarchical"
        assert [s.id for s in result.plan.steps] == ["step-1", "step-2", "step-3"]
        assert result.plan.steps[0].group == "Phase 1 — Foundation"
        assert result.plan.warnings == []

    def test_unknown_path_returns_fallback_with_warnings(self) -> None:
        from lore_core.plans.ingest import IngestSource, ingest_plan

        text = "# Plan\n\nJust prose, no structure.\n"
        result = ingest_plan(
            IngestSource(kind="markdown", payload=text, producer="cli")
        )
        assert result.confidence == "fallback"
        assert result.adapter_name == "markdown/unknown"
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "shape_unknown"
        # Plan still has a slug + title so callers can render a useful error.
        assert result.plan.slug
        assert result.plan.title == "Plan"

    def test_atxsteps_path_returns_high_confidence(self) -> None:
        from lore_core.plans.ingest import IngestSource, ingest_plan

        text = "# Plan\n\n### Step 1: alpha\na\n\n### Step 2: beta\nb\n"
        result = ingest_plan(
            IngestSource(kind="markdown", payload=text, producer="cli")
        )
        assert result.confidence == "high"
        assert result.adapter_name == "markdown/atxsteps"
        assert [s.id for s in result.plan.steps] == ["step-1", "step-2"]


def test_classifier_ignores_headings_inside_code_fences() -> None:
    """``### Step 1`` inside a fenced code block must NOT count as a step."""
    text = """# Plan

```python
### Step 1: this is example code
not a real step
```

### Phase 1 — alpha
real step

### Phase 2 — beta
another real step
"""
    shape = classify(text)
    assert isinstance(shape, ShapeATXSteps)
    # Only the two real H3 headings outside the fence count.
    assert len(shape.hits) == 2
