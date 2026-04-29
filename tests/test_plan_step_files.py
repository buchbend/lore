"""Tests for `step_files` capture: per-step file lists in plan frontmatter.

Plans gain an authoritative ``files: list[str]`` field per step so the
Stop hook (and PostToolUse:Edit handler) can attribute commits/edits to
plan steps without regex-mining prose. The plan-authoring LLM writes a
``Files:`` line per step body; the parser extracts it; the writer emits
``step_files: {step-N: [paths]}`` under plan frontmatter.

Format supported:
* **Inline comma list** — ``Files: lib/foo.py, lib/bar.py``
* **Bulleted list** — ``Files:`` line followed by ``- path`` bullets
  until blank line or non-bullet.

The ``Files:`` line must be at start-of-line (after optional indent) to
avoid false positives from prose mentions like *"the Files: section"*.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from lore_core.plans.canonical import extract_step_files
from lore_core.plans.types import PlanStep, StructuredPlan
from lore_core.plans.writer import write_plan_note
from lore_core.schema import parse_frontmatter


# ---------------------------------------------------------------------------
# PlanStep.files field
# ---------------------------------------------------------------------------


class TestPlanStepFilesField:
    def test_default_files_empty_list(self) -> None:
        step = PlanStep(id="step-1", title="t", body="b")
        assert step.files == []

    def test_explicit_files(self) -> None:
        step = PlanStep(
            id="step-1",
            title="t",
            body="b",
            files=["lib/foo.py", "lib/bar.py"],
        )
        assert step.files == ["lib/foo.py", "lib/bar.py"]

    def test_files_independent_per_instance(self) -> None:
        # Frozen dataclass + default_factory: each instance gets its own list.
        a = PlanStep(id="step-1", title="t", body="b")
        b = PlanStep(id="step-2", title="t", body="b")
        assert a.files is not b.files


# ---------------------------------------------------------------------------
# extract_step_files() — body → list[str]
# ---------------------------------------------------------------------------


class TestExtractStepFiles:
    def test_inline_comma_list(self) -> None:
        body = "do the thing\nFiles: lib/foo.py, lib/bar.py\nmore prose"
        assert extract_step_files(body) == ["lib/foo.py", "lib/bar.py"]

    def test_inline_single_path(self) -> None:
        body = "Files: lib/foo.py"
        assert extract_step_files(body) == ["lib/foo.py"]

    def test_inline_strips_whitespace_around_paths(self) -> None:
        body = "Files:  lib/foo.py ,   lib/bar.py "
        assert extract_step_files(body) == ["lib/foo.py", "lib/bar.py"]

    def test_inline_case_insensitive(self) -> None:
        body = "files: lib/foo.py"
        assert extract_step_files(body) == ["lib/foo.py"]

    def test_bulleted_list(self) -> None:
        body = (
            "do the thing\n"
            "\n"
            "Files:\n"
            "- lib/foo.py\n"
            "- lib/bar.py\n"
            "- tests/test_foo.py\n"
            "\n"
            "more prose"
        )
        assert extract_step_files(body) == [
            "lib/foo.py",
            "lib/bar.py",
            "tests/test_foo.py",
        ]

    def test_bulleted_terminates_on_blank_line(self) -> None:
        body = (
            "Files:\n"
            "- lib/foo.py\n"
            "\n"
            "- lib/bar.py\n"  # past the blank — not part of the Files block
        )
        assert extract_step_files(body) == ["lib/foo.py"]

    def test_bulleted_terminates_on_non_bullet(self) -> None:
        body = (
            "Files:\n"
            "- lib/foo.py\n"
            "verification: run tests\n"  # plain prose ends the block
        )
        assert extract_step_files(body) == ["lib/foo.py"]

    def test_no_files_line(self) -> None:
        body = "do the thing\nverification: run tests\n"
        assert extract_step_files(body) == []

    def test_files_must_anchor_start_of_line(self) -> None:
        # Prose mention of "Files:" mid-sentence is NOT a Files block.
        body = "we will modify the Files: section as we go"
        assert extract_step_files(body) == []

    def test_files_with_indent_accepted(self) -> None:
        # Up to a few leading spaces is fine — common in nested markdown.
        body = "  Files: lib/foo.py"
        assert extract_step_files(body) == ["lib/foo.py"]

    def test_empty_body(self) -> None:
        assert extract_step_files("") == []

    def test_inline_with_asterisk_bullet(self) -> None:
        body = (
            "Files:\n"
            "* lib/foo.py\n"
            "* lib/bar.py\n"
        )
        assert extract_step_files(body) == ["lib/foo.py", "lib/bar.py"]

    def test_strips_backticks_around_paths(self) -> None:
        # Markdown convention — paths often wrapped in backticks.
        body = "Files: `lib/foo.py`, `lib/bar.py`"
        assert extract_step_files(body) == ["lib/foo.py", "lib/bar.py"]

    def test_bulleted_with_backticks(self) -> None:
        body = (
            "Files:\n"
            "- `lib/foo.py`\n"
            "- `tests/test_foo.py`\n"
        )
        assert extract_step_files(body) == ["lib/foo.py", "tests/test_foo.py"]


# ---------------------------------------------------------------------------
# Parser populates PlanStep.files end-to-end
# ---------------------------------------------------------------------------


class TestParserPopulatesFiles:
    def test_atx_steps_extract_files(self) -> None:
        from lore_core.plans.parser import parse

        text = (
            "# Test plan\n"
            "\n"
            "## Steps\n"
            "\n"
            "### step-1: First\n"
            "do thing\n"
            "Files: lib/foo.py, lib/bar.py\n"
            "\n"
            "### step-2: Second\n"
            "do other thing\n"
            "Files: tests/test_foo.py\n"
        )
        plan = parse(text)
        assert len(plan.steps) == 2
        assert plan.steps[0].files == ["lib/foo.py", "lib/bar.py"]
        assert plan.steps[1].files == ["tests/test_foo.py"]

    def test_step_without_files_line_has_empty_files(self) -> None:
        from lore_core.plans.parser import parse

        text = (
            "# Plan\n"
            "\n"
            "### step-1: First\n"
            "no files mentioned here\n"
            "\n"
            "### step-2: Second\n"
            "Files: foo.py\n"
        )
        plan = parse(text)
        assert plan.steps[0].files == []
        assert plan.steps[1].files == ["foo.py"]


# ---------------------------------------------------------------------------
# Writer emits step_files frontmatter
# ---------------------------------------------------------------------------


class TestWriterEmitsStepFiles:
    def test_step_files_in_frontmatter(self, tmp_path: Path) -> None:
        plan = StructuredPlan(
            slug="test-plan",
            title="Test plan",
            body_intro="",
            steps=[
                PlanStep(
                    id="step-1",
                    title="First",
                    body="do thing",
                    files=["lib/foo.py", "lib/bar.py"],
                ),
                PlanStep(
                    id="step-2",
                    title="Second",
                    body="do other thing",
                    files=["tests/test_foo.py"],
                ),
            ],
            mode="headings",
        )
        wiki_root = tmp_path / "wiki"
        result = write_plan_note(
            wiki_root=wiki_root,
            plan=plan,
            source_hash="sha256:test",
            source_adapter="test",
            today=date(2026, 4, 29),
        )

        text = result.path.read_text()
        fm = parse_frontmatter(text)
        assert fm["step_files"] == {
            "step-1": ["lib/foo.py", "lib/bar.py"],
            "step-2": ["tests/test_foo.py"],
        }

    def test_step_files_omitted_when_no_files(self, tmp_path: Path) -> None:
        # Plans where no step has files: the step_files key is omitted entirely
        # so legacy plans don't acquire empty-dict noise on capture.
        plan = StructuredPlan(
            slug="empty-plan",
            title="Empty",
            body_intro="",
            steps=[
                PlanStep(id="step-1", title="t", body="b"),
                PlanStep(id="step-2", title="t", body="b"),
            ],
            mode="headings",
        )
        wiki_root = tmp_path / "wiki"
        result = write_plan_note(
            wiki_root=wiki_root,
            plan=plan,
            source_hash="sha256:empty",
            source_adapter="test",
            today=date(2026, 4, 29),
        )
        fm = parse_frontmatter(result.path.read_text())
        assert "step_files" not in fm

    def test_step_files_preserved_through_yaml_roundtrip(self, tmp_path: Path) -> None:
        # Ensure the dict serializes cleanly via yaml.safe_dump (paths often
        # contain colons in URLs or windows drive prefixes — guard against
        # YAML-fragile content).
        plan = StructuredPlan(
            slug="rt-plan",
            title="RT",
            body_intro="",
            steps=[
                PlanStep(
                    id="step-1",
                    title="t",
                    body="b",
                    files=["lib/lore_cli/hooks.py", "tests/test_x.py"],
                ),
            ],
            mode="single",
        )
        wiki_root = tmp_path / "wiki"
        result = write_plan_note(
            wiki_root=wiki_root,
            plan=plan,
            source_hash="sha256:rt",
            source_adapter="test",
            today=date(2026, 4, 29),
        )
        # Re-parse via raw yaml to catch any quoting issues.
        text = result.path.read_text()
        fm_text = text.split("---")[1]
        fm = yaml.safe_load(fm_text)
        assert fm["step_files"]["step-1"] == [
            "lib/lore_cli/hooks.py",
            "tests/test_x.py",
        ]
