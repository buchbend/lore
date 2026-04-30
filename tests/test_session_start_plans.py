"""Tests for the SessionStart Resume block (Phase 4).

Direct calls into ``_active_plans_resume_block`` because the full
``_session_start_from_lore`` machinery requires a real attached repo
+ gh setup that's tangential to the Resume-block contract. The
helper is tested for:

* Multi-state rendering (done count, in-progress list, next pending).
* Cap holds with N=10 active plans (3 inline + "+N more — …").
* No-catalog dependency (works with empty `_catalog.json`).
* First-plan-in-fresh-wiki survives empty `plans/*.md` glob.
* Stale (Nd) marker derived from `last_reviewed`.
* Breadcrumb nudge: commit ahead of step_status fires; commit at/behind doesn't.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from lore_cli.hooks import _active_plans_resume_block
from lore_core.plans.parser import parse
from lore_core.plans.step_status import set_step
from lore_core.plans.types import StepStatus
from lore_core.plans.writer import compute_source_hash, write_plan_note


# ---------------------------------------------------------------------------
# Test infra
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    return tmp_path / "wiki" / "private"


def _make_plan(
    wiki_root: Path,
    *,
    title: str = "Refactor auth",
    n_steps: int = 4,
    repo: str | None = "lore",
    last_reviewed: str | None = None,
) -> str:
    plan_text = (
        f"# {title}\n\n## Steps\n\n"
        + "\n\n".join(
            f"### Step {i + 1}: step {i + 1}\nbody {i + 1}"
            for i in range(n_steps)
        )
        + "\n"
    )
    plan = parse(plan_text)
    write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash(plan_text),
        source_adapter="claude-code-hook",
        repo=repo,
    )
    if last_reviewed is not None:
        # Manually edit the last_reviewed for staleness tests.
        path = wiki_root / "plans" / f"{plan.slug}.md"
        text = path.read_text()
        import re
        text = re.sub(
            r"^last_reviewed:.*$",
            f"last_reviewed: {last_reviewed}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        path.write_text(text)
    return plan.slug


# ---------------------------------------------------------------------------
# Empty / no-plans cases
# ---------------------------------------------------------------------------


def test_no_wiki_no_block(tmp_path: Path) -> None:
    """Wiki dir doesn't exist → empty block, count zero."""
    lines, count = _active_plans_resume_block(tmp_path / "nope", repo="lore")
    assert lines == []
    assert count == 0


def test_first_plan_fresh_wiki_no_plans_dir(wiki_root: Path) -> None:
    """No `plans/` directory at all → empty block, count zero, no crash."""
    wiki_root.mkdir(parents=True)
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    assert lines == []
    assert count == 0


def test_no_catalog_dependency(wiki_root: Path) -> None:
    """The helper must work without `_catalog.json` — proves the hot path
    reads `plans/*.md` directly."""
    slug = _make_plan(wiki_root)
    # Explicitly assert no catalog exists.
    assert not (wiki_root / "_catalog.json").exists()
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    assert count == 1
    assert any(slug in line for line in lines)


# ---------------------------------------------------------------------------
# Multi-state rendering
# ---------------------------------------------------------------------------


def test_fresh_plan_renders_next_pending(wiki_root: Path) -> None:
    slug = _make_plan(wiki_root, n_steps=4)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "## Resume:" in text
    assert "0/4 done" in text
    assert "Next pending: step-1" in text
    # Wikilink line targets s1 because no in-progress yet.
    assert f"[[plan/{slug}#step-1]]" in text


def test_in_progress_step_appears_in_card(wiki_root: Path) -> None:
    slug = _make_plan(wiki_root, n_steps=4)
    set_step(wiki_root=wiki_root, slug=slug, step_id="step-2", status=StepStatus.IN_PROGRESS)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "1 in-progress" in text
    assert "In progress: step-2" in text
    assert f"[[plan/{slug}#step-2]]" in text


def test_resume_block_surfaces_trailer_as_override(wiki_root: Path) -> None:
    """As of v0.35 the trailer is demoted to an override. Auto-attribution
    rides `step_files` (PostToolUse:Edit + Stop LLM judgment); the
    Resume block still surfaces the trailer literal so the model has
    the canonical string in context for the explicit short-circuit case
    — but framed as override, not the primary path."""
    slug = _make_plan(wiki_root, n_steps=3)
    set_step(wiki_root=wiki_root, slug=slug, step_id="step-1", status=StepStatus.IN_PROGRESS)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    # Trailer literal anchored to the in-progress step, framed as override.
    assert f"`Plan: {slug}#step-1`" in text
    assert "Override" in text or "override" in text
    # The new informational framing must mention the auto-attribution path.
    assert "edits" in text and "in_progress" in text


def test_resume_block_trailer_falls_back_to_next_pending(wiki_root: Path) -> None:
    """When no step is in-progress, the override trailer anchors to the
    first pending step — the most likely target of the next commit."""
    slug = _make_plan(wiki_root, n_steps=3)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert f"`Plan: {slug}#step-1`" in text


def test_multi_in_progress_lists_all(wiki_root: Path) -> None:
    slug = _make_plan(wiki_root, n_steps=4)
    set_step(wiki_root=wiki_root, slug=slug, step_id="step-2", status=StepStatus.IN_PROGRESS)
    set_step(wiki_root=wiki_root, slug=slug, step_id="step-4", status=StepStatus.IN_PROGRESS)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "2 in-progress" in text
    assert "step-2" in text and "step-4" in text


def test_blocked_step_appears_in_summary(wiki_root: Path) -> None:
    slug = _make_plan(wiki_root, n_steps=3)
    set_step(wiki_root=wiki_root, slug=slug, step_id="step-2", status=StepStatus.BLOCKED)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "1 blocked" in text


def test_auto_closed_plan_drops_out_of_active_resume_block(
    wiki_root: Path,
) -> None:
    """Marking the last step done auto-flips the plan to ``status:
    done`` (Layer B in step_status). Active resume-block filtering
    then excludes it — no "all done — `/lore:plan-advance --complete`?"
    suggestion needed because the state no longer exists.
    """
    slug = _make_plan(wiki_root, n_steps=2)
    for sid in ("step-1", "step-2"):
        set_step(wiki_root=wiki_root, slug=slug, step_id=sid, status=StepStatus.DONE)
    # Auto-close fired — plan is no longer active.
    from lore_core.schema import parse_frontmatter
    fm = parse_frontmatter((wiki_root / "plans" / f"{slug}.md").read_text())
    assert fm["status"] == "done"

    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    assert lines == []


def test_all_done_active_plan_still_offers_complete(wiki_root: Path) -> None:
    """Edge case: a manually-re-opened plan whose steps are all done.
    The suggestion path in the resume block still fires for those
    (auto-close is one-way; if a user resets ``status: active`` by
    hand, the SessionStart prompt becomes useful again).
    """
    slug = _make_plan(wiki_root, n_steps=2)
    for sid in ("step-1", "step-2"):
        set_step(wiki_root=wiki_root, slug=slug, step_id=sid, status=StepStatus.DONE)
    # Manually re-open the plan as if a user reverted the auto-close.
    plan_file = wiki_root / "plans" / f"{slug}.md"
    plan_file.write_text(
        plan_file.read_text().replace("status: done", "status: active")
    )

    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "2/2 done" in text
    assert "All steps done" in text
    assert "/lore:plan-advance" in text


# ---------------------------------------------------------------------------
# Cap + +N more
# ---------------------------------------------------------------------------


def test_cap_holds_with_many_plans(wiki_root: Path) -> None:
    slugs = [
        _make_plan(wiki_root, title=f"Plan {i}", repo="lore") for i in range(10)
    ]
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert count == 10
    # Should render at most 3 cards (look for "## Resume:" markers).
    assert text.count("## Resume:") <= 3
    assert "+7 more active plans" in text


# ---------------------------------------------------------------------------
# Stale rendering
# ---------------------------------------------------------------------------


def test_stale_marker_when_last_reviewed_old(wiki_root: Path) -> None:
    long_ago = (date.today() - timedelta(days=30)).isoformat()
    _make_plan(wiki_root, last_reviewed=long_ago)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "stale (30d)" in text


def test_no_stale_marker_when_recent(wiki_root: Path) -> None:
    _make_plan(wiki_root, last_reviewed=date.today().isoformat())
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert "stale" not in text


# ---------------------------------------------------------------------------
# Repo filter — wiki-general plans rank after repo-matched
# ---------------------------------------------------------------------------


def test_repo_filter_ranks_repo_matches_first(wiki_root: Path) -> None:
    _make_plan(wiki_root, title="For lore", repo="lore")
    _make_plan(wiki_root, title="Wiki general", repo=None)
    _make_plan(wiki_root, title="For other", repo="other/repo")

    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert count == 2  # for-lore + wiki-general
    # `For lore` must come before `Wiki general`
    assert text.index("For lore") < text.index("Wiki general")
    # Other-repo plan excluded
    assert "For other" not in text


# ---------------------------------------------------------------------------
# Breadcrumb nudges
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "commit", "--allow-empty", "-m", "initial"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def _commit(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_nudge_fires_when_commit_references_unset_step(
    wiki_root: Path, fresh_repo: Path
) -> None:
    slug = _make_plan(wiki_root, n_steps=4)
    # No step_status set → s2 is still pending. Commit references step-2.
    _commit(fresh_repo, f"work\n\nPlan: {slug}#step-2")

    lines, _ = _active_plans_resume_block(
        wiki_root, repo="lore", repo_root=fresh_repo
    )
    text = "\n".join(lines)
    assert "⚠ commit" in text
    assert "references step-2" in text


def test_nudge_does_not_fire_when_step_already_done(
    wiki_root: Path, fresh_repo: Path
) -> None:
    slug = _make_plan(wiki_root, n_steps=4)
    set_step(wiki_root=wiki_root, slug=slug, step_id="step-2", status=StepStatus.DONE)
    _commit(fresh_repo, f"work\n\nPlan: {slug}#step-2")
    lines, _ = _active_plans_resume_block(
        wiki_root, repo="lore", repo_root=fresh_repo
    )
    text = "\n".join(lines)
    assert "⚠" not in text


def test_no_repo_root_means_no_commit_breadcrumbs(
    wiki_root: Path, tmp_path: Path
) -> None:
    """Repo-root-less call (e.g. unattached cwd) → only session breadcrumbs."""
    _make_plan(wiki_root)
    lines, _ = _active_plans_resume_block(wiki_root, repo="lore", repo_root=None)
    text = "\n".join(lines)
    # No breadcrumbs → no nudge marker.
    assert "⚠ commit" not in text


# ---------------------------------------------------------------------------
# Status-line summary stays consistent with what's actually rendered
# (regression for the "lies about plan count" Phase-4 reviewer finding).
# ---------------------------------------------------------------------------


def test_status_line_count_matches_rendered_when_under_cap(wiki_root: Path) -> None:
    """N plans, N <= cap → status-line says exactly `N plans`, no `(K shown)`."""
    for i in range(2):
        _make_plan(wiki_root, title=f"Plan {i}")
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    assert count == 2
    # Two ## Resume:/### header lines? Actually one ## + one ###.
    text = "\n".join(lines)
    assert text.count("## Resume:") == 1
    assert text.count("### ") == 1


def test_status_line_count_includes_n_shown_when_over_cap(wiki_root: Path) -> None:
    """N plans > cap → caller's status-line builder gets N (cap stays the cap).

    The exact rendering of `(K shown)` is in `_session_start_from_lore`;
    here we just pin that the helper returns the full count so the
    caller can decide.
    """
    for i in range(7):
        _make_plan(wiki_root, title=f"Plan {i}")
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    assert count == 7
    text = "\n".join(lines)
    # 1 H2 (## Resume:) + 2 H3s (### ...) = 3 cards (cap)
    assert text.count("## Resume:") == 1
    assert text.count("### ") == 2
    # +N more line surfaces with the right number.
    assert "+4 more" in text


# ---------------------------------------------------------------------------
# Multi-card uses ONE ## Resume: header, subsequent cards demote to ###
# (regression for the "three peer H2s" reviewer finding).
# ---------------------------------------------------------------------------


def test_multi_plan_demotes_subsequent_cards_to_h3(wiki_root: Path) -> None:
    for i in range(3):
        _make_plan(wiki_root, title=f"Plan {i}")
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    text = "\n".join(lines)
    assert text.count("## Resume:") == 1  # only the first card uses H2
    assert text.count("### ") == 2  # second + third cards use H3


# ---------------------------------------------------------------------------
# Breadcrumb nudges render as bullets with paragraph break before them
# (regression for the "nudges visually swallowed" reviewer finding).
# ---------------------------------------------------------------------------


def test_nudge_renders_as_bullet_with_blank_separator(
    wiki_root: Path, fresh_repo: Path
) -> None:
    slug = _make_plan(wiki_root, n_steps=4)
    _commit(fresh_repo, f"work\n\nPlan: {slug}#step-2")
    lines, _ = _active_plans_resume_block(
        wiki_root, repo="lore", repo_root=fresh_repo
    )
    # Find the nudge line and the line above it.
    nudge_idx = next(i for i, line in enumerate(lines) if "⚠ commit" in line)
    assert lines[nudge_idx].startswith("- ⚠"), (
        f"nudge should be a bullet, got: {lines[nudge_idx]!r}"
    )
    assert lines[nudge_idx - 1] == "", (
        "nudges need a blank line before them so they don't collapse "
        f"into the wikilink as a continuation; got: {lines[nudge_idx - 1]!r}"
    )


# ---------------------------------------------------------------------------
# Defensive: malformed plan must not crash SessionStart
# ---------------------------------------------------------------------------


def test_malformed_plan_does_not_crash(wiki_root: Path) -> None:
    """A garbage plan note must be skipped silently — never break SessionStart."""
    slug = _make_plan(wiki_root)
    # Corrupt the file: replace frontmatter with garbage.
    path = wiki_root / "plans" / f"{slug}.md"
    path.write_text("---\nnot valid yaml: [[\n---\n\nbody\n")
    lines, count = _active_plans_resume_block(wiki_root, repo="lore")
    # Should not crash; corrupted plan is skipped from the active list.
    assert isinstance(lines, list)
    assert count == 0
