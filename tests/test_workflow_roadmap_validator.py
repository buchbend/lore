"""Tests for `lore_workflow.roadmap_validator` — the roadmap-DAG gate.

Ported from ccat-agent-workflow's `tests/test_roadmap_validator.py`. Only the
behavioural half is ported: that repo's prose-gate checks against its own
`SKILL.md` files don't apply here — skills are ported separately (#172).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_workflow import roadmap_validator as mod

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "roadmaps"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _kinds(result: mod.ValidationResult) -> set[str]:
    return {p.kind for p in result.problems}


# ---------------------------------------------------------------------------
# Well-formed
# ---------------------------------------------------------------------------


def test_well_formed_passes() -> None:
    result = mod.validate_roadmap(_fixture("well-formed.md"))
    assert result.ok, f"well-formed roadmap must pass; problems={result.problems}"
    assert not result.problems


def test_well_formed_rows_parsed() -> None:
    result = mod.validate_roadmap(_fixture("well-formed.md"))
    assert len(result.rows) == 3
    first = result.rows[0]
    assert first.issue == "ccatobs/widget#12"
    assert first.blocked_by == ()  # em-dash means no blocker
    third = result.rows[2]
    assert third.blocked_by == ("#12", "#13"), "multiple blockers split on comma"


def test_or_raise_returns_on_valid() -> None:
    result = mod.validate_roadmap_or_raise(_fixture("well-formed.md"))
    assert result.ok


# ---------------------------------------------------------------------------
# Cross-repo roadmap (fully-qualified edges resolve across repos)
# ---------------------------------------------------------------------------


def test_cross_repo_passes() -> None:
    result = mod.validate_roadmap(_fixture("cross-repo.md"))
    assert result.ok, f"cross-repo roadmap must pass; problems={result.problems}"
    repos = {row.repo for row in result.rows}
    assert repos == {"producer", "consumer"}, "cross-repo rows span >1 repo"


def test_cross_repo_full_ref_edges_resolve() -> None:
    """A fully-qualified blocked-by resolves even with a number shared across
    repos (producer#5 vs consumer#5)."""
    result = mod.validate_roadmap(_fixture("cross-repo.md"))
    assert "dangling_edge" not in _kinds(result)


# ---------------------------------------------------------------------------
# Malformed cases
# ---------------------------------------------------------------------------


def test_malformed_columns_fails() -> None:
    result = mod.validate_roadmap(_fixture("malformed-columns.md"))
    assert not result.ok
    assert "columns" in _kinds(result), "missing required column must be reported"


def test_non_fq_ref_fails() -> None:
    result = mod.validate_roadmap(_fixture("malformed-non-fq.md"))
    assert not result.ok
    assert "non_fq_ref" in _kinds(result), (
        "an Issue ref lacking owner/repo must be rejected"
    )


def test_dangling_edge_fails() -> None:
    result = mod.validate_roadmap(_fixture("malformed-dangling.md"))
    assert not result.ok
    assert "dangling_edge" in _kinds(result)


def test_cyclic_fails() -> None:
    result = mod.validate_roadmap(_fixture("cyclic.md"))
    assert not result.ok
    assert "cycle" in _kinds(result)


def test_or_raise_raises_on_invalid() -> None:
    with pytest.raises(mod.RoadmapError) as exc:
        mod.validate_roadmap_or_raise(_fixture("cyclic.md"))
    assert exc.value.problems


def test_missing_table_returns_no_rows() -> None:
    result = mod.validate_roadmap("# no table here")
    assert not result.ok
    assert result.rows == []


# ---------------------------------------------------------------------------
# Structured result API
# ---------------------------------------------------------------------------


def test_result_and_problem_shape() -> None:
    result = mod.validate_roadmap(_fixture("cyclic.md"))
    assert hasattr(result, "ok")
    assert hasattr(result, "problems")
    assert hasattr(result, "rows")
    problem = result.problems[0]
    assert hasattr(problem, "kind")
    assert hasattr(problem, "message")
    assert isinstance(problem.kind, str)
    assert isinstance(problem.message, str)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def test_cli_returns_zero_on_valid() -> None:
    assert mod.main([str(FIXTURES / "well-formed.md")]) == 0


def test_cli_returns_nonzero_on_invalid() -> None:
    assert mod.main([str(FIXTURES / "cyclic.md")]) != 0
