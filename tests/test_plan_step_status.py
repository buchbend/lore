"""Tests for plans/step_status.py — the authoritative mutation primitive.

Folded into the broader `tests/test_plan_writer.py` would inflate that
file past readability; step_status earns its own seam because it's the
load-bearing surface for the "plan-as-authority" pivot.
"""
from __future__ import annotations

import multiprocessing as mp
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.plans.parser import parse
from lore_core.plans.step_status import advance, set_step
from lore_core.plans.types import StepStatus
from lore_core.plans.writer import compute_source_hash, plan_path, write_plan_note
from lore_core.schema import parse_frontmatter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plan_with_steps(tmp_path: Path) -> tuple[Path, str]:
    wiki = tmp_path / "wiki" / "x"
    plan = parse(
        "# Test plan\n\n## Steps\n\n"
        "### Step 1: alpha\na\n\n"
        "### Step 2: beta\nb\n\n"
        "### Step 3: gamma\nc\n"
    )
    write_plan_note(
        wiki_root=wiki,
        plan=plan,
        source_hash=compute_source_hash("x"),
        source_adapter="claude-code-hook",
    )
    return wiki, plan.slug


# ---------------------------------------------------------------------------
# set_step: basic transitions
# ---------------------------------------------------------------------------


def test_set_step_done(plan_with_steps: tuple[Path, str]) -> None:
    wiki, slug = plan_with_steps
    update = set_step(wiki_root=wiki, slug=slug, step_id="s2", status=StepStatus.DONE)
    assert update.previous is None
    assert update.current == "done"
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["step_status"] == {"s2": "done"}
    assert "step_status_updated" in fm


def test_set_step_in_progress_then_done(plan_with_steps: tuple[Path, str]) -> None:
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status="in_progress")
    update = set_step(wiki_root=wiki, slug=slug, step_id="s1", status="done")
    assert update.previous == "in_progress"
    assert update.current == "done"
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["step_status"]["s1"] == "done"


def test_set_step_blocked(plan_with_steps: tuple[Path, str]) -> None:
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status=StepStatus.BLOCKED)
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["step_status"]["s2"] == "blocked"


def test_set_step_clear_via_none(plan_with_steps: tuple[Path, str]) -> None:
    """Setting status=None removes the entry — moves the step back to pending."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status="done")
    update = set_step(wiki_root=wiki, slug=slug, step_id="s1", status=None)
    assert update.previous == "done"
    assert update.current is None
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert "s1" not in (fm.get("step_status") or {})


def test_set_step_no_op_when_unchanged_preserves_mtime(
    plan_with_steps: tuple[Path, str],
) -> None:
    """Setting the same status twice doesn't rewrite the file."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status="done")
    path = plan_path(wiki, slug)
    mtime_before = path.stat().st_mtime_ns
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status="done")
    assert path.stat().st_mtime_ns == mtime_before


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_set_step_unknown_step_id_raises(plan_with_steps: tuple[Path, str]) -> None:
    wiki, slug = plan_with_steps
    with pytest.raises(ValueError, match="not in plan"):
        set_step(wiki_root=wiki, slug=slug, step_id="s99", status="done")


def test_set_step_invalid_status_string_raises(
    plan_with_steps: tuple[Path, str],
) -> None:
    wiki, slug = plan_with_steps
    with pytest.raises(ValueError, match="unknown step status"):
        set_step(wiki_root=wiki, slug=slug, step_id="s1", status="finished")


def test_set_step_unknown_plan_raises(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "x"
    wiki.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        set_step(wiki_root=wiki, slug="nonexistent", step_id="s1", status="done")


# ---------------------------------------------------------------------------
# Timestamp behaviour
# ---------------------------------------------------------------------------


def test_step_status_updated_bumps_on_change(
    plan_with_steps: tuple[Path, str],
) -> None:
    wiki, slug = plan_with_steps
    t1 = datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 28, 11, 0, 0, tzinfo=UTC)
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status="in_progress", now=t1)
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status="done", now=t2)
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["step_status_updated"] == "2026-04-28T11:00:00Z"


# ---------------------------------------------------------------------------
# advance() sugar
# ---------------------------------------------------------------------------


def test_advance_marks_first_pending_done(
    plan_with_steps: tuple[Path, str],
) -> None:
    wiki, slug = plan_with_steps
    update = advance(wiki_root=wiki, slug=slug)
    assert update is not None
    assert update.step_id == "s1"
    assert update.current == "done"


def test_advance_picks_first_in_progress_when_present(
    plan_with_steps: tuple[Path, str],
) -> None:
    """If any step is in_progress, advance marks that one done (not the next pending)."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status="in_progress")
    update = advance(wiki_root=wiki, slug=slug)
    assert update is not None
    assert update.step_id == "s2"
    assert update.current == "done"


def test_advance_picks_earliest_in_progress_for_determinism(
    plan_with_steps: tuple[Path, str],
) -> None:
    """Multiple in-progress (parallel agents): advance picks the first by document order."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status="in_progress")
    set_step(wiki_root=wiki, slug=slug, step_id="s3", status="in_progress")
    update = advance(wiki_root=wiki, slug=slug)
    assert update is not None
    assert update.step_id == "s2"  # earliest by document order


def test_advance_returns_none_when_all_done(
    plan_with_steps: tuple[Path, str],
) -> None:
    wiki, slug = plan_with_steps
    for sid in ("s1", "s2", "s3"):
        set_step(wiki_root=wiki, slug=slug, step_id=sid, status="done")
    assert advance(wiki_root=wiki, slug=slug) is None


# ---------------------------------------------------------------------------
# Concurrent set_step on the same slug — flock serializes
# ---------------------------------------------------------------------------


def _concurrent_set_step(args: tuple[str, str, str, str]) -> str:
    wiki_root_str, slug, step_id, status = args
    from lore_core.plans.step_status import set_step

    update = set_step(
        wiki_root=Path(wiki_root_str), slug=slug, step_id=step_id, status=status
    )
    return f"{step_id}:{update.current}"


# ---------------------------------------------------------------------------
# Auto-close: plan status flips to ``done`` when the last step lands
# ---------------------------------------------------------------------------


def test_auto_close_when_last_step_done(plan_with_steps: tuple[Path, str]) -> None:
    """Setting the final remaining step to ``done`` flips ``status:
    active → done`` and bumps ``last_reviewed`` automatically. No
    manual ``status: done`` write needed."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status=StepStatus.DONE)
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status=StepStatus.DONE)

    # Two of three done — plan must still be active.
    fm_mid = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm_mid["status"] == "active"

    # Final step lands → auto-close.
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    set_step(
        wiki_root=wiki, slug=slug, step_id="s3", status=StepStatus.DONE, now=now
    )
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["status"] == "done"
    assert fm["last_reviewed"] == "2026-05-01"


def test_auto_close_does_not_overwrite_terminal_status(
    plan_with_steps: tuple[Path, str],
) -> None:
    """A plan already at ``superseded`` / ``abandoned`` must not be
    auto-flipped to ``done`` — those are author-set terminal states.

    Edge case in practice (the vault is mostly self-writing) but cheap
    to guard against.
    """
    wiki, slug = plan_with_steps
    # Hand-promote to superseded as if a curator had decided so.
    target = plan_path(wiki, slug)
    text = target.read_text().replace("status: active", "status: superseded")
    target.write_text(text)

    set_step(wiki_root=wiki, slug=slug, step_id="s1", status=StepStatus.DONE)
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status=StepStatus.DONE)
    set_step(wiki_root=wiki, slug=slug, step_id="s3", status=StepStatus.DONE)

    fm = parse_frontmatter(target.read_text())
    assert fm["status"] == "superseded"


def test_auto_close_skips_partial_completion(plan_with_steps: tuple[Path, str]) -> None:
    """Marking a non-final step done must not flip plan status."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status=StepStatus.DONE)
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["status"] == "active"


def test_auto_close_no_op_path_does_not_trigger(
    plan_with_steps: tuple[Path, str],
) -> None:
    """Repeating ``set_step(s1, done)`` after s1 is already done must
    not write to the file. The auto-close branch is on the mutation
    path; the no-op fast path returns early before reaching it."""
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status=StepStatus.DONE)
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status=StepStatus.DONE)
    # s3 is the last step — but we re-set s1 instead. status must stay active.
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status=StepStatus.DONE)
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["status"] == "active"


def test_un_doing_step_does_not_revert_plan_status(
    plan_with_steps: tuple[Path, str],
) -> None:
    """Auto-close is a one-way ratchet: clearing a previously-done
    step (``status=None``) does NOT flip plan back to ``active``.

    Rationale: the status flip implies "this work shipped"; an
    accidental clear shouldn't re-open the plan. If the user genuinely
    wants to re-open, they edit ``status`` back themselves.
    """
    wiki, slug = plan_with_steps
    set_step(wiki_root=wiki, slug=slug, step_id="s1", status=StepStatus.DONE)
    set_step(wiki_root=wiki, slug=slug, step_id="s2", status=StepStatus.DONE)
    set_step(wiki_root=wiki, slug=slug, step_id="s3", status=StepStatus.DONE)
    # Plan is now done.

    set_step(wiki_root=wiki, slug=slug, step_id="s3", status=None)
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    # Plan stays done; the user can choose to re-open by hand.
    assert fm["status"] == "done"
    # But step_status reflects the clear — s3 is back to pending (absent).
    assert "s3" not in (fm.get("step_status") or {})


def test_advance_triggers_auto_close_on_last_step(
    plan_with_steps: tuple[Path, str],
) -> None:
    """End-to-end: ``advance`` calls into ``set_step``, so its final
    invocation must trip the auto-close exactly the same way."""
    wiki, slug = plan_with_steps
    advance(wiki_root=wiki, slug=slug)  # s1 → done
    advance(wiki_root=wiki, slug=slug)  # s2 → done
    final = advance(wiki_root=wiki, slug=slug)  # s3 → done
    assert final is not None
    assert final.current == "done"

    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    assert fm["status"] == "done"


def test_concurrent_set_step_serializes(plan_with_steps: tuple[Path, str]) -> None:
    """Four workers mutate distinct steps in parallel; all writes survive.

    Without per-slug flock, read-modify-write would lose updates as
    each writer reads the original frontmatter and overwrites.
    """
    wiki, slug = plan_with_steps
    args = [
        (str(wiki), slug, "s1", "done"),
        (str(wiki), slug, "s2", "in_progress"),
        (str(wiki), slug, "s3", "blocked"),
        (str(wiki), slug, "s1", "in_progress"),  # last writer wins for s1
    ]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        outcomes = pool.map(_concurrent_set_step, args)

    assert len(outcomes) == 4
    # All three steps must have entries; s1 is either done or in_progress
    # depending on race ordering, but never missing entirely.
    fm = parse_frontmatter(plan_path(wiki, slug).read_text())
    final_status = fm.get("step_status") or {}
    assert "s1" in final_status
    assert final_status["s2"] == "in_progress"
    assert final_status["s3"] == "blocked"
