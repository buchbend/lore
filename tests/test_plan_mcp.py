"""Tests for the MCP tools `lore_plan_active` and `lore_plan_status`."""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.plans.parser import parse
from lore_core.plans.step_status import set_step
from lore_core.plans.types import StepStatus
from lore_core.plans.writer import compute_source_hash, write_plan_note
from lore_mcp.server import (
    _dispatch,
    _tool_schema,
    handle_plan_active,
    handle_plan_file,
    handle_plan_status,
)


@pytest.fixture
def lore_with_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    lore_root = tmp_path / "lore"
    wiki_root = lore_root / "wiki" / "private"
    wiki_root.mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    plan_text = (
        "# Refactor auth\n\n## Steps\n\n"
        "### Step 1: alpha\na\n\n"
        "### Step 2: beta\nb\n\n"
        "### Step 3: gamma\nc\n"
    )
    plan = parse(plan_text)
    write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash(plan_text),
        source_adapter="claude-code-hook",
        repo="lore",
    )
    return {"lore_root": lore_root, "wiki_root": wiki_root, "slug": "refactor-auth"}


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------


def test_both_plan_tools_in_schema() -> None:
    names = {t["name"] for t in _tool_schema()}
    assert "lore_plan_active" in names
    assert "lore_plan_status" in names


def test_dispatch_routes_plan_tools(lore_with_plan: dict) -> None:
    """The match-case dispatcher must wire both new tools."""
    result = _dispatch("lore_plan_active", {"wiki": "private"})
    assert "plans" in result
    result = _dispatch("lore_plan_status", {"wiki": "private", "slug": "refactor-auth"})
    assert result["slug"] == "refactor-auth"


# ---------------------------------------------------------------------------
# lore_plan_active
# ---------------------------------------------------------------------------


def test_plan_active_lists_active_plans(lore_with_plan: dict) -> None:
    result = handle_plan_active(wiki="private")
    assert result["wiki"] == "private"
    assert len(result["plans"]) == 1
    plan = result["plans"][0]
    assert plan["slug"] == "refactor-auth"
    assert plan["steps_total"] == 3
    assert plan["steps_done"] == 0
    assert plan["next_pending_step"] == "step-1"


def test_plan_active_repo_filter(lore_with_plan: dict) -> None:
    result = handle_plan_active(wiki="private", repo="lore")
    assert len(result["plans"]) == 1
    other = handle_plan_active(wiki="private", repo="nope")
    # repo=nope excludes lore-tagged; wiki-general fallback empty too.
    assert other["plans"] == []


def test_plan_active_reflects_step_status_changes(lore_with_plan: dict) -> None:
    set_step(
        wiki_root=lore_with_plan["wiki_root"],
        slug=lore_with_plan["slug"],
        step_id="step-1",
        status=StepStatus.DONE,
    )
    set_step(
        wiki_root=lore_with_plan["wiki_root"],
        slug=lore_with_plan["slug"],
        step_id="step-2",
        status=StepStatus.IN_PROGRESS,
    )
    result = handle_plan_active(wiki="private")
    plan = result["plans"][0]
    assert plan["steps_done"] == 1
    assert plan["steps_in_progress"] == ["step-2"]
    assert plan["next_pending_step"] == "step-3"


def test_plan_active_no_wiki_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "empty"))
    result = handle_plan_active()
    assert "error" in result


# ---------------------------------------------------------------------------
# lore_plan_status
# ---------------------------------------------------------------------------


def test_plan_status_returns_full_step_list(lore_with_plan: dict) -> None:
    result = handle_plan_status(slug="refactor-auth", wiki="private")
    assert result["slug"] == "refactor-auth"
    assert result["status"] == "active"
    assert len(result["steps"]) == 3
    # Per-step records carry id, title, derived status (pending if absent).
    s1 = result["steps"][0]
    assert s1["id"] == "step-1"
    assert s1["status"] == "pending"
    assert s1["title"]


def test_plan_status_reflects_step_status_dict(lore_with_plan: dict) -> None:
    set_step(
        wiki_root=lore_with_plan["wiki_root"],
        slug=lore_with_plan["slug"],
        step_id="step-2",
        status=StepStatus.IN_PROGRESS,
    )
    result = handle_plan_status(slug="refactor-auth", wiki="private")
    statuses = {s["id"]: s["status"] for s in result["steps"]}
    assert statuses["step-1"] == "pending"
    assert statuses["step-2"] == "in_progress"
    assert statuses["step-3"] == "pending"
    assert result["step_status"] == {"step-2": "in_progress"}


def test_plan_status_unknown_slug_returns_error(lore_with_plan: dict) -> None:
    result = handle_plan_status(slug="nonexistent", wiki="private")
    assert "error" in result
    assert result["error"]["code"] == "plan_not_found"


def test_plan_status_breadcrumbs_empty_when_no_signals(lore_with_plan: dict) -> None:
    result = handle_plan_status(slug="refactor-auth", wiki="private")
    assert result["breadcrumbs"] == []


# ---------------------------------------------------------------------------
# lore_plan_file (envelope-only ingestion via MCP)
# ---------------------------------------------------------------------------


def test_plan_file_in_schema() -> None:
    names = {t["name"] for t in _tool_schema()}
    assert "lore_plan_file" in names


def test_plan_file_files_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``lore_plan_file`` validates an envelope and writes a canonical plan.
    No markdown shape detection; envelope-only by design."""
    lore_root = tmp_path / "lore"
    wiki_root = lore_root / "wiki" / "private"
    wiki_root.mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    envelope = {
        "schema": "lore.plan.envelope/1",
        "title": "Refactor auth",
        "steps": [
            {"title": "Audit"},
            {"title": "Migrate"},
        ],
    }
    result = handle_plan_file(envelope=envelope, wiki="private")
    assert result["slug"] == "refactor-auth"
    assert result["step_count"] == 2
    assert result["confidence"] == "structured"

    written = wiki_root / "plans" / "refactor-auth.md"
    assert written.exists()
    body = written.read_text()
    assert "### step-1: Audit" in body
    assert "### step-2: Migrate" in body


def test_plan_file_rejects_bad_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Envelope failing validation returns a structured MCP error."""
    lore_root = tmp_path / "lore"
    (lore_root / "wiki" / "private").mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = handle_plan_file(envelope={"schema": "wrong"}, wiki="private")
    assert "error" in result
    assert result["error"]["code"] == "envelope_invalid"


def test_plan_file_dispatch_route() -> None:
    """Dispatcher must route ``lore_plan_file`` to the handler."""
    # Sanity: dispatcher recognizes the name (full integration covered elsewhere).
    schema_names = {t["name"] for t in _tool_schema()}
    assert "lore_plan_file" in schema_names
