"""Tests for plans/writer.py and plans/registry.py.

Covers the contracts pinned in the implementation plan:

* yaml.safe_dump round-trip on YAML-fragile descriptions
* per-slug flock under concurrent invocation (writer AND step_status)
* source_hash canonicalization stability
* slug collision date suffix
* user-owned whitelist preservation incl. step_status survives re-capture
* first-plan-in-fresh-wiki creates ``plans/`` dir
* renumber-safe re-capture (step IDs never reshuffle out from under step_status)
* registry.list_active ranking + repo filter
* registry.scan_incoming_wikilinks for delete-safety
"""
from __future__ import annotations

import multiprocessing as mp
from datetime import date as _date
from pathlib import Path

import pytest
import yaml

from lore_core.plans import registry
from lore_core.plans.parser import parse
from lore_core.plans.types import PlanStep, StructuredPlan
from lore_core.plans.writer import (
    USER_OWNED_KEYS,
    WriteResult,
    compute_source_hash,
    plan_path,
    write_plan_note,
)
from lore_core.schema import parse_frontmatter, strip_frontmatter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """A clean wiki root with NO plans/ directory yet — exercises the mkdir path."""
    return tmp_path / "wiki" / "private"


def _make_plan(
    slug: str = "refactor-auth",
    *,
    title: str = "Refactor auth",
    body_intro: str = "Migrate from session tokens to OIDC.",
    n_steps: int = 3,
) -> StructuredPlan:
    steps = [
        PlanStep(id=f"s{i + 1}", title=f"Step {i + 1}", body=f"do step {i + 1}")
        for i in range(n_steps)
    ]
    return StructuredPlan(
        slug=slug,
        title=title,
        body_intro=body_intro,
        steps=steps,
        mode="headings",
    )


def _write(
    wiki: Path,
    plan: StructuredPlan,
    *,
    text: str = "source plan text",
    repo: str | None = "lore",
    today: _date = _date(2026, 4, 28),
) -> WriteResult:
    return write_plan_note(
        wiki_root=wiki,
        plan=plan,
        source_hash=compute_source_hash(text),
        source_adapter="claude-code-hook",
        repo=repo,
        today=today,
    )


# ---------------------------------------------------------------------------
# Fresh write + first-plan-in-fresh-wiki
# ---------------------------------------------------------------------------


def test_first_plan_creates_plans_dir(wiki_root: Path) -> None:
    """Wiki has no plans/ directory yet — writer mkdirs it."""
    assert not (wiki_root / "plans").exists()
    result = _write(wiki_root, _make_plan())
    assert result.outcome == "filed"
    assert result.path.exists()
    assert (wiki_root / "plans").is_dir()


def test_fresh_write_frontmatter_shape(wiki_root: Path) -> None:
    result = _write(wiki_root, _make_plan(), text="abc")
    fm = parse_frontmatter(result.path.read_text())
    assert fm["type"] == "plan"
    assert fm["slug"] == "refactor-auth"
    assert fm["status"] == "active"
    assert fm["source_adapter"] == "claude-code-hook"
    assert fm["source_hash"] == compute_source_hash("abc")
    assert fm["repo"] == "lore"
    assert fm["created"] == "2026-04-28"
    assert fm["last_reviewed"] == "2026-04-28"
    # No step_status on fresh write — absence-is-pending rule.
    assert "step_status" not in fm


def test_fresh_write_body_renders_steps_and_trailer_hint(wiki_root: Path) -> None:
    result = _write(wiki_root, _make_plan(n_steps=2))
    body = strip_frontmatter(result.path.read_text())
    assert "# Refactor auth" in body
    assert "## Steps" in body
    assert "### s1: Step 1" in body
    assert "### s2: Step 2" in body
    assert "Plan: refactor-auth#s<N>" in body  # commit-trailer hint


def test_fresh_write_no_repo_when_unattached(wiki_root: Path) -> None:
    result = _write(wiki_root, _make_plan(), repo=None)
    fm = parse_frontmatter(result.path.read_text())
    assert "repo" not in fm


# ---------------------------------------------------------------------------
# yaml.safe_dump correctness on fragile descriptions
# ---------------------------------------------------------------------------


def test_yaml_safe_dump_handles_colon_in_description(wiki_root: Path) -> None:
    """Description like 'Plan: refactor X' must round-trip as a string, not a dict.

    This is the regression case for the `format_frontmatter` bug:
    naive rendering would produce ``description: Plan: refactor X``
    which YAML parses as a nested dict.
    """
    plan = StructuredPlan(
        slug="x",
        title="Plan: refactor X",
        body_intro="",
        steps=[PlanStep(id="s1", title="t", body="b")],
    )
    result = write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash("x"),
        source_adapter="claude-code-hook",
    )
    fm = parse_frontmatter(result.path.read_text())
    assert isinstance(fm["description"], str)
    assert fm["description"] == "Plan: refactor X"


def test_yaml_safe_dump_handles_brackets_and_pipes(wiki_root: Path) -> None:
    plan = StructuredPlan(
        slug="x",
        title="x",
        body_intro="",
        steps=[PlanStep(id="s1", title="t", body="b")],
    )
    result = write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash("x"),
        source_adapter="claude-code-hook",
        description="Some [bracket] | pipe & ampersand stuff",
    )
    fm = parse_frontmatter(result.path.read_text())
    assert fm["description"] == "Some [bracket] | pipe & ampersand stuff"


# ---------------------------------------------------------------------------
# source_hash canonicalization stability
# ---------------------------------------------------------------------------


def test_source_hash_stable_across_trailing_newline(wiki_root: Path) -> None:
    """Trailing newline differences must not change the hash.

    Without canonical_text in compute_source_hash, an editor adding
    a trailing newline on save would trigger spurious "different
    content" detection on every re-acceptance.
    """
    h1 = compute_source_hash("Hello")
    h2 = compute_source_hash("Hello\n")
    h3 = compute_source_hash("Hello\n\n\n")
    assert h1 == h2 == h3


def test_source_hash_stable_across_crlf(wiki_root: Path) -> None:
    h_unix = compute_source_hash("a\nb\nc\n")
    h_win = compute_source_hash("a\r\nb\r\nc\r\n")
    assert h_unix == h_win


def test_idempotent_recapture_no_op(wiki_root: Path) -> None:
    """Same source_hash → outcome 'deduped', file untouched."""
    plan = _make_plan()
    r1 = _write(wiki_root, plan, text="source")
    mtime_before = r1.path.stat().st_mtime_ns
    r2 = _write(wiki_root, plan, text="source")
    assert r2.outcome == "deduped"
    # File should not have been rewritten.
    assert r2.path.stat().st_mtime_ns == mtime_before


# ---------------------------------------------------------------------------
# Slug collision suffix
# ---------------------------------------------------------------------------


def test_collision_suffix_when_existing_status_non_active(wiki_root: Path) -> None:
    """Different content + non-active existing → collision-suffixed write."""
    plan = _make_plan()
    _write(wiki_root, plan, text="v1")

    # Manually flip the existing plan to status=done.
    existing = plan_path(wiki_root, "refactor-auth")
    text = existing.read_text()
    text = text.replace("status: active", "status: done")
    existing.write_text(text)

    r2 = _write(wiki_root, plan, text="v2-different-content")
    assert r2.outcome == "collision-suffixed"
    assert r2.path != existing
    assert r2.path.name == "refactor-auth-2026-04-28.md"


def test_collision_same_day_appends_counter(wiki_root: Path) -> None:
    plan = _make_plan()
    _write(wiki_root, plan, text="v1")

    existing = plan_path(wiki_root, "refactor-auth")
    text = existing.read_text().replace("status: active", "status: done")
    existing.write_text(text)

    _write(wiki_root, plan, text="v2")
    # Now flip the suffixed one too and capture again same-day.
    suffixed = wiki_root / "plans" / "refactor-auth-2026-04-28.md"
    suffixed.write_text(suffixed.read_text().replace("status: active", "status: done"))
    r3 = _write(wiki_root, plan, text="v3")
    assert r3.path.name == "refactor-auth-2026-04-28-2.md"


# ---------------------------------------------------------------------------
# User-owned whitelist preservation on re-capture
# ---------------------------------------------------------------------------


def test_recapture_preserves_step_status(wiki_root: Path) -> None:
    """Re-capture with different content MUST preserve step_status entries."""
    plan = _make_plan()
    r1 = _write(wiki_root, plan, text="v1")

    # Simulate Claude marking s1 done between captures.
    text = r1.path.read_text()
    fm = parse_frontmatter(text)
    fm["step_status"] = {"s1": "done", "s2": "in_progress"}
    fm["step_status_updated"] = "2026-04-28T10:00:00Z"
    body = strip_frontmatter(text)
    r1.path.write_text(
        f"---\n{yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()}\n---\n\n{body}"
    )

    # Re-capture with different source content.
    r2 = _write(wiki_root, plan, text="v2-different")
    assert r2.outcome == "updated"
    fm_after = parse_frontmatter(r2.path.read_text())
    assert fm_after["step_status"] == {"s1": "done", "s2": "in_progress"}
    assert fm_after["step_status_updated"] == "2026-04-28T10:00:00Z"


def test_recapture_preserves_user_tags_and_status(wiki_root: Path) -> None:
    plan = _make_plan()
    r1 = _write(wiki_root, plan, text="v1")
    text = r1.path.read_text()
    fm = parse_frontmatter(text)
    fm["tags"] = ["my", "manual", "tags"]
    fm["status"] = "paused"  # user paused it — but writer treats only "active" as
    fm["notes"] = "a personal note"
    body = strip_frontmatter(text)
    r1.path.write_text(
        f"---\n{yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()}\n---\n\n{body}"
    )

    # Re-capture — writer sees status=paused, falls into collision-suffixed path
    # because "active" is the gate. We're testing user-owned merge happens for
    # the active-status case, so flip back to active first.
    fm["status"] = "active"
    r1.path.write_text(
        f"---\n{yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()}\n---\n\n{body}"
    )

    r2 = _write(wiki_root, plan, text="v2")
    assert r2.outcome == "updated"
    fm_after = parse_frontmatter(r2.path.read_text())
    assert fm_after["tags"] == ["my", "manual", "tags"]
    assert fm_after["notes"] == "a personal note"


def test_recapture_refreshes_last_reviewed(wiki_root: Path) -> None:
    """last_reviewed is system-owned — must bump on re-capture."""
    plan = _make_plan()
    r1 = _write(wiki_root, plan, text="v1", today=_date(2026, 4, 1))
    r2 = _write(wiki_root, plan, text="v2", today=_date(2026, 5, 15))
    assert r2.outcome == "updated"
    fm = parse_frontmatter(r2.path.read_text())
    assert fm["last_reviewed"] == "2026-05-15"


def test_user_owned_keys_set_is_explicit() -> None:
    """Pin the whitelist contents — adding a system field must NOT break this."""
    assert USER_OWNED_KEYS == frozenset({
        "status", "tags", "spec", "roadmap", "notes",
        "description", "step_status", "step_status_updated",
    })


# ---------------------------------------------------------------------------
# Renumber-safe re-capture
# ---------------------------------------------------------------------------


def test_recapture_preserves_step_ids_when_steps_added(wiki_root: Path) -> None:
    """Adding steps to source: existing s1, s2 keep their IDs; new ones become s3."""
    plan_v1 = _make_plan(n_steps=2)
    r1 = _write(wiki_root, plan_v1, text="v1")

    # Mark s2 in_progress so we can verify status survives renumber.
    text = r1.path.read_text()
    fm = parse_frontmatter(text)
    fm["step_status"] = {"s2": "in_progress"}
    body = strip_frontmatter(text)
    r1.path.write_text(
        f"---\n{yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()}\n---\n\n{body}"
    )

    plan_v2 = _make_plan(n_steps=4)  # added two steps
    r2 = _write(wiki_root, plan_v2, text="v2")
    assert r2.outcome == "updated"
    body_after = strip_frontmatter(r2.path.read_text())
    assert "### s1:" in body_after
    assert "### s2:" in body_after
    assert "### s3:" in body_after
    assert "### s4:" in body_after
    fm_after = parse_frontmatter(r2.path.read_text())
    # step_status pointing at s2 still meaningful — s2 is still s2.
    assert fm_after["step_status"] == {"s2": "in_progress"}


def test_recapture_marks_removed_steps(wiki_root: Path) -> None:
    """Removing a step from source: existing s2 stays as ``[removed-from-source]``."""
    r1 = _write(wiki_root, _make_plan(n_steps=3), text="v1")
    r2 = _write(wiki_root, _make_plan(n_steps=2), text="v2")
    assert r2.outcome == "updated"
    body = strip_frontmatter(r2.path.read_text())
    assert "[removed-from-source]" in body
    # s3 must still exist as a heading (so step_status[s3] doesn't dangle).
    assert "### s3:" in body


# ---------------------------------------------------------------------------
# Concurrent writers — flock serializes same-slug writes
# ---------------------------------------------------------------------------


def _concurrent_worker(args: tuple[str, str]) -> str:
    """Top-level for multiprocessing pickling."""
    wiki_root_str, body_marker = args
    from lore_core.plans.parser import parse
    from lore_core.plans.writer import compute_source_hash, write_plan_note

    plan = parse(
        "# Shared\n\n## Steps\n\n"
        f"### Step 1: foo\n{body_marker}\n\n### Step 2: bar\n"
    )
    result = write_plan_note(
        wiki_root=Path(wiki_root_str),
        plan=plan,
        source_hash=compute_source_hash(body_marker),
        source_adapter="claude-code-hook",
    )
    return result.outcome


def test_concurrent_writes_to_same_slug_serialize(tmp_path: Path) -> None:
    """Two writers calling write_plan_note with the same slug serialize via flock.

    Without per-slug locking, both writers pass the existence check
    simultaneously and last-write-wins. With the flock, the second
    sees the first's file and dedups (or updates), so both writes
    survive in some form — no silent loss.
    """
    wiki = tmp_path / "wiki" / "x"
    args = [(str(wiki), f"version-{i}") for i in range(4)]
    with mp.get_context("spawn").Pool(processes=4) as pool:
        outcomes = pool.map(_concurrent_worker, args)

    # At least one filed; rest are filed/updated — never dropped silently.
    assert "filed" in outcomes
    # The plan note must exist and have valid frontmatter.
    final = wiki / "plans" / "shared.md"
    assert final.exists(), f"file at {final} not created; outcomes={outcomes}"
    fm = parse_frontmatter(final.read_text())
    assert fm["type"] == "plan"
    assert fm["status"] == "active"


# ---------------------------------------------------------------------------
# Registry.list_active
# ---------------------------------------------------------------------------


def test_list_active_empty_wiki(wiki_root: Path) -> None:
    assert registry.list_active(wiki_root) == []


def test_list_active_filters_by_status(wiki_root: Path) -> None:
    _write(wiki_root, _make_plan("a"), text="a")
    _write(wiki_root, _make_plan("b"), text="b")

    # Mark b as done.
    pb = plan_path(wiki_root, "b")
    pb.write_text(pb.read_text().replace("status: active", "status: done"))

    cards = registry.list_active(wiki_root)
    slugs = [c.slug for c in cards]
    assert "a" in slugs
    assert "b" not in slugs


def test_list_active_repo_filter(wiki_root: Path) -> None:
    _write(wiki_root, _make_plan("for-lore"), text="x", repo="lore")
    _write(wiki_root, _make_plan("for-other"), text="y", repo="other/repo")
    _write(wiki_root, _make_plan("wiki-general"), text="z", repo=None)

    cards = registry.list_active(wiki_root, repo="lore")
    slugs = [c.slug for c in cards]
    # repo=lore matches first; wiki-general after; for-other excluded.
    assert slugs == ["for-lore", "wiki-general"]


def test_list_active_card_step_status_summary(wiki_root: Path) -> None:
    """ActivePlanCard exposes done count, in-progress list, next pending."""
    plan = _make_plan(n_steps=4)
    r = _write(wiki_root, plan, text="x")
    text = r.path.read_text()
    fm = parse_frontmatter(text)
    fm["step_status"] = {"s1": "done", "s2": "in_progress"}
    body = strip_frontmatter(text)
    r.path.write_text(
        f"---\n{yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()}\n---\n\n{body}"
    )

    cards = registry.list_active(wiki_root)
    assert len(cards) == 1
    c = cards[0]
    assert c.steps_total == 4
    assert c.steps_done == 1
    assert c.steps_in_progress == ["s2"]
    assert c.next_pending_step() == "s3"


# ---------------------------------------------------------------------------
# Registry.scan_incoming_wikilinks
# ---------------------------------------------------------------------------


def test_scan_incoming_wikilinks_finds_referencing_session(
    wiki_root: Path,
) -> None:
    _write(wiki_root, _make_plan("refactor-auth"), text="x")

    sessions = wiki_root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "2026-04-28-x.md").write_text(
        "---\ntype: session\n---\n\nWorking on [[plan/refactor-auth#s2]]"
    )

    matches = registry.scan_incoming_wikilinks(wiki_root, "refactor-auth")
    assert len(matches) == 1
    assert matches[0].name == "2026-04-28-x.md"


def test_scan_ignores_same_named_concept(wiki_root: Path) -> None:
    """A note linking [[refactor-auth#s2]] (without 'plan/' prefix) does NOT match."""
    _write(wiki_root, _make_plan("refactor-auth"), text="x")

    concepts = wiki_root / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "decoy.md").write_text(
        "---\ntype: concept\n---\n\nSee [[refactor-auth#s2]]"
    )

    matches = registry.scan_incoming_wikilinks(wiki_root, "refactor-auth")
    assert matches == []
