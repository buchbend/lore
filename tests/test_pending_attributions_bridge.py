"""Tests for the SessionStart pending-attributions bridge.

When a Stop hook in session N parks a (commit, plan, step) attribution
to ``~/.cache/lore/sessions/<sid>/pending-attributions.json`` (because
the LLM said skip, returned low confidence, or wasn't available), the
*next* SessionStart in the same repo must surface that as a system-
reminder block — closing the loop instead of leaving the user to
manually relay the warning.

Tests cover:
* basic rendering of one entry → one bullet line in the block
* multiple entries grouped under one heading
* attributions for plans not currently active are filtered out
* attributions for plans in OTHER repos are filtered out
* dedup across sessions for the same (commit, plan, step) triple
* empty / missing / malformed cache files don't break SessionStart
* the block is omitted entirely when no actionable entries remain
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_cli.hooks import _pending_attributions_block
from lore_core.plans.types import PlanStep, StructuredPlan
from lore_core.plans.writer import write_plan_note


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    cache_home = tmp_path / "home"
    cache_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: cache_home, raising=True)
    wiki_root = tmp_path / "wiki"
    (wiki_root / "plans").mkdir(parents=True)
    return {"cache_home": cache_home, "wiki_root": wiki_root}


def _file_a_plan(
    wiki_root: Path, *, slug: str, repo: str = "test/repo"
) -> None:
    plan = StructuredPlan(
        slug=slug,
        title=slug,
        body_intro="",
        steps=[
            PlanStep(id="step-1", title="t", body="b", files=["lib/foo.py"]),
            PlanStep(id="step-2", title="t", body="b", files=["lib/bar.py"]),
        ],
        mode="headings",
    )
    write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=f"sha256:{slug}",
        source_adapter="test",
        repo=repo,
    )


def _write_pending(
    cache_home: Path,
    *,
    sid: str,
    entries: list[dict],
) -> Path:
    path = (
        cache_home / ".cache" / "lore" / "sessions" / sid
        / "pending-attributions.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2))
    return path


def _entry(
    *,
    sha: str,
    slug: str,
    step_id: str = "step-1",
    decision: str = "skip",
    confidence: float = 0.3,
    reason: str = "tangential",
) -> dict:
    return {
        "commit_sha": sha,
        "plan_slug": slug,
        "step_id": step_id,
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
        "judged_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_one_entry_renders_one_bullet(env: dict) -> None:
    _file_a_plan(env["wiki_root"], slug="plan-a")
    _write_pending(
        env["cache_home"],
        sid="sess-1",
        entries=[_entry(sha="abc123", slug="plan-a")],
    )

    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")

    assert block, "expected non-empty block"
    text = "\n".join(block)
    assert "Unresolved plan attributions" in text
    assert "abc123" in text
    assert "plan-a" in text
    assert "step-1" in text
    assert "tangential" in text


def test_multiple_entries_grouped(env: dict) -> None:
    _file_a_plan(env["wiki_root"], slug="plan-a")
    _file_a_plan(env["wiki_root"], slug="plan-b")
    _write_pending(
        env["cache_home"],
        sid="sess-1",
        entries=[
            _entry(sha="abc123", slug="plan-a", step_id="step-1"),
            _entry(sha="def456", slug="plan-b", step_id="step-2"),
        ],
    )

    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    text = "\n".join(block)
    assert "abc123" in text
    assert "def456" in text
    assert "plan-a" in text
    assert "plan-b" in text


def test_inactive_plan_filtered_out(env: dict) -> None:
    # Pending-attribution refers to a plan that no longer exists in the
    # vault — drop it silently rather than dangle.
    _file_a_plan(env["wiki_root"], slug="plan-a")
    _write_pending(
        env["cache_home"],
        sid="sess-1",
        entries=[
            _entry(sha="abc123", slug="plan-a"),
            _entry(sha="ghost", slug="archived-plan"),
        ],
    )

    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    text = "\n".join(block)
    assert "abc123" in text
    assert "ghost" not in text
    assert "archived-plan" not in text


def test_other_repo_filtered_out(env: dict) -> None:
    _file_a_plan(env["wiki_root"], slug="plan-a", repo="test/repo")
    _file_a_plan(env["wiki_root"], slug="other-plan", repo="someone/else")
    _write_pending(
        env["cache_home"],
        sid="sess-1",
        entries=[
            _entry(sha="abc123", slug="plan-a"),
            _entry(sha="zzz999", slug="other-plan"),
        ],
    )

    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    text = "\n".join(block)
    assert "abc123" in text
    assert "zzz999" not in text


def test_dedup_across_sessions(env: dict) -> None:
    # Same (commit, plan, step) parked in two sessions — render once.
    _file_a_plan(env["wiki_root"], slug="plan-a")
    _write_pending(
        env["cache_home"],
        sid="sess-1",
        entries=[_entry(sha="abc123", slug="plan-a")],
    )
    _write_pending(
        env["cache_home"],
        sid="sess-2",
        entries=[_entry(sha="abc123", slug="plan-a")],
    )

    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    text = "\n".join(block)
    # exactly one occurrence
    assert text.count("abc123") == 1


def test_empty_when_no_pending(env: dict) -> None:
    _file_a_plan(env["wiki_root"], slug="plan-a")
    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    assert block == []


def test_malformed_cache_file_is_silent(env: dict) -> None:
    _file_a_plan(env["wiki_root"], slug="plan-a")
    bad = (
        env["cache_home"] / ".cache" / "lore" / "sessions" / "sess-1"
        / "pending-attributions.json"
    )
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json")

    # Must not raise.
    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    assert block == []


def test_non_list_payload_skipped(env: dict) -> None:
    _file_a_plan(env["wiki_root"], slug="plan-a")
    bad = (
        env["cache_home"] / ".cache" / "lore" / "sessions" / "sess-1"
        / "pending-attributions.json"
    )
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({"unexpected": "shape"}))

    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    assert block == []


def test_done_decision_with_low_confidence_renders(env: dict) -> None:
    # A "done" verdict at confidence 0.4 lands in pending-attr (below
    # the floor); SessionStart should still surface it as actionable.
    _file_a_plan(env["wiki_root"], slug="plan-a")
    _write_pending(
        env["cache_home"],
        sid="sess-1",
        entries=[
            _entry(
                sha="abc123",
                slug="plan-a",
                decision="done",
                confidence=0.4,
                reason="uncertain implementation",
            ),
        ],
    )
    block = _pending_attributions_block(env["wiki_root"], repo="test/repo")
    text = "\n".join(block)
    assert "abc123" in text
    assert "done" in text
    assert "0.4" in text or "uncertain" in text
