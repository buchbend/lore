"""Tests for the LLM-gated commit→step attribution at Stop.

The Stop hook scans recent commits, intersects each commit's changed
files with each active plan's ``step_files``, and asks the LLM
(via ``closure_judgment``) whether the commit completed the step.

Decisions:
* ``done`` + confidence ≥ 0.6 → set_step(DONE), confirmation line emitted
* ``in_progress`` + confidence ≥ 0.6 → set_step(IN_PROGRESS) if not already
* anything else (low confidence, ``skip``, no LLM available, LLM error)
  → write to ``pending-attributions.json`` for the next session to handle

Trailer-bearing commits are skipped — the existing path at
``hooks.py:1339`` handles them via direct set_step(DONE).

Tests use a fake LlmClient injected via ``make_llm_client`` patch so the
suite stays deterministic and offline.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lore_cli.hooks import _attribute_commits_with_judgment
from lore_core.plans.types import PlanStep, StepStatus, StructuredPlan
from lore_core.plans.step_status import set_step
from lore_core.plans.writer import write_plan_note
from lore_core.schema import parse_frontmatter
from lore_core.state.attachments import Attachment, AttachmentsFile


# ---------------------------------------------------------------------------
# Fake LLM client
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolUseBlock:
    type: str = "tool_use"
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[_FakeToolUseBlock]
    model: str = "fake"


class _FakeMessages:
    def __init__(self, scripted: list[dict[str, Any] | Exception]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("LLM called more times than scripted")
        next_value = self._scripted.pop(0)
        if isinstance(next_value, Exception):
            raise next_value
        return _FakeResponse(content=[_FakeToolUseBlock(input=next_value)])


class _FakeClient:
    def __init__(self, scripted: list[dict[str, Any] | Exception]) -> None:
        self.messages = _FakeMessages(scripted)
        self.backend_name = "fake"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    lore_root = tmp_path / "lore"
    wiki_root = lore_root / "wiki" / "private"
    (wiki_root / "plans").mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    cache_home = tmp_path / "home"
    cache_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: cache_home, raising=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=repo,
            wiki="private",
            scope="private",
            attached_at=datetime(2026, 4, 28, tzinfo=UTC),
            source="manual",
        )
    )
    af.save()

    monkeypatch.chdir(repo)

    return {
        "lore_root": lore_root,
        "wiki_root": wiki_root,
        "repo": repo,
        "cache_home": cache_home,
    }


def _file_a_plan(
    wiki_root: Path,
    *,
    slug: str,
    step_files: dict[str, list[str]],
    repo: str = "test/repo",
) -> Path:
    steps = [
        PlanStep(id=sid, title=f"Step {sid}", body="impl details", files=files)
        for sid, files in step_files.items()
    ]
    plan = StructuredPlan(
        slug=slug,
        title=slug,
        body_intro="",
        steps=steps,
        mode="headings",
    )
    result = write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=f"sha256:{slug}",
        source_adapter="test",
        repo=repo,
    )
    return result.path


def _commit(repo: Path, file_path: str, content: str, msg: str) -> str:
    abs_path = repo / file_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content)
    subprocess.run(
        ["git", "add", file_path], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True
    )
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha


def _read_step_status(plan_path: Path) -> dict[str, str]:
    fm = parse_frontmatter(plan_path.read_text())
    return fm.get("step_status") or {}


def _read_pending(cache_home: Path) -> list[dict]:
    """Read the pending-attributions JSON for any session."""
    sessions = cache_home / ".cache" / "lore" / "sessions"
    if not sessions.exists():
        return []
    out: list[dict] = []
    for sid_dir in sessions.iterdir():
        f = sid_dir / "pending-attributions.json"
        if not f.exists():
            continue
        try:
            out.extend(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# Happy path: LLM verdicts drive set_step
# ---------------------------------------------------------------------------


def test_done_verdict_high_confidence_closes_step(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    sha = _commit(env["repo"], "lib/foo.py", "x = 1\n", "implement step-1: add foo")

    client = _FakeClient([
        {"decision": "done", "confidence": 0.9, "reason": "implements step-1"},
    ])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        msgs = _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {"step-1": "done"}
    assert any("test-plan#step-1" in m and "done" in m for m in msgs)
    # No pending-attr — high confidence + actionable verdict.
    assert _read_pending(env["cache_home"]) == []


def test_in_progress_verdict_flips_pending_step(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _commit(env["repo"], "lib/foo.py", "x = 1\n", "wip on step-1, partial")

    client = _FakeClient([
        {"decision": "in_progress", "confidence": 0.8, "reason": "wip"},
    ])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {"step-1": "in_progress"}
    assert _read_pending(env["cache_home"]) == []


def test_skip_verdict_writes_pending_attribution(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    sha = _commit(env["repo"], "lib/foo.py", "x = 1\n", "rename helper")

    client = _FakeClient([
        {"decision": "skip", "confidence": 0.3, "reason": "tangential"},
    ])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {}
    pending = _read_pending(env["cache_home"])
    assert len(pending) == 1
    entry = pending[0]
    assert entry["plan_slug"] == "test-plan"
    assert entry["step_id"] == "step-1"
    assert entry["decision"] == "skip"
    assert entry["reason"] == "tangential"
    assert entry["commit_sha"].startswith(sha[:7])


def test_low_confidence_done_writes_pending_not_close(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _commit(env["repo"], "lib/foo.py", "x = 1\n", "ambiguous")

    client = _FakeClient([
        {"decision": "done", "confidence": 0.4, "reason": "uncertain"},
    ])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    # Despite "done" verdict, low confidence parks it for next session.
    assert _read_step_status(plan_path) == {}
    pending = _read_pending(env["cache_home"])
    assert len(pending) == 1
    assert pending[0]["confidence"] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Skip rules — when the LLM is NOT called
# ---------------------------------------------------------------------------


def test_trailer_bearing_commit_skips_llm(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _commit(
        env["repo"], "lib/foo.py", "x = 1\n",
        "implement\n\nPlan: test-plan#step-1",
    )

    # Empty-script client — would AssertionError if called.
    client = _FakeClient([])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    # set_step is the trailer-detector's job (existing path); we just
    # confirm we didn't double-act here.
    assert _read_step_status(plan_path) == {}  # not our responsibility
    assert client.messages.calls == []


def test_unrelated_commit_skips_llm(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _commit(env["repo"], "docs/README.md", "hello\n", "update docs")

    client = _FakeClient([])  # would error if called
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {}
    assert client.messages.calls == []


def test_already_done_step_skips_llm(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    set_step(
        wiki_root=env["wiki_root"],
        slug="test-plan",
        step_id="step-1",
        status=StepStatus.DONE,
    )
    _commit(env["repo"], "lib/foo.py", "x = 2\n", "tweak after done")

    client = _FakeClient([])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {"step-1": "done"}
    assert client.messages.calls == []


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_no_llm_client_writes_all_overlaps_to_pending(env: dict) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    sha = _commit(env["repo"], "lib/foo.py", "x = 1\n", "edit foo")

    with patch("lore_curator.llm_client.make_llm_client", return_value=None):
        msgs = _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {}
    assert msgs == []
    pending = _read_pending(env["cache_home"])
    assert len(pending) == 1
    assert pending[0]["plan_slug"] == "test-plan"
    assert pending[0]["step_id"] == "step-1"
    assert pending[0]["decision"] == "skip"
    assert pending[0]["reason"] == "no LLM client available"


def test_llm_error_writes_pending(env: dict) -> None:
    from lore_curator.llm_client import LlmClientError

    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _commit(env["repo"], "lib/foo.py", "x = 1\n", "edit foo")

    client = _FakeClient([LlmClientError("backend exploded")])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    assert _read_step_status(plan_path) == {}
    pending = _read_pending(env["cache_home"])
    assert len(pending) == 1
    assert pending[0]["decision"] == "skip"
    assert "exploded" in pending[0]["reason"] or "error" in pending[0]["reason"].lower()


# ---------------------------------------------------------------------------
# Multi-plan / multi-step
# ---------------------------------------------------------------------------


def test_two_plans_judged_independently(env: dict) -> None:
    p1 = _file_a_plan(
        env["wiki_root"],
        slug="plan-a",
        step_files={"step-1": ["lib/shared.py"]},
    )
    p2 = _file_a_plan(
        env["wiki_root"],
        slug="plan-b",
        step_files={"step-1": ["lib/shared.py"]},
    )
    _commit(env["repo"], "lib/shared.py", "x = 1\n", "edit shared")

    client = _FakeClient([
        {"decision": "done", "confidence": 0.9, "reason": "closes plan-a"},
        {"decision": "in_progress", "confidence": 0.7, "reason": "wip plan-b"},
    ])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])

    # Both plans were judged; outcomes differ.
    statuses = {p1: _read_step_status(p1), p2: _read_step_status(p2)}
    # The order of plan iteration is registry-dependent; assert as a set.
    flipped = sorted(statuses.values(), key=lambda d: list(d.values())[0])
    assert flipped == [{"step-1": "done"}, {"step-1": "in_progress"}]


def test_idempotency_seen_set_prevents_re_judgment(env: dict) -> None:
    # The same (commit, plan, step) seen on a second Stop must NOT
    # re-call the LLM, even if the step is still pending.
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _commit(env["repo"], "lib/foo.py", "x = 1\n", "tangential")

    client = _FakeClient([
        {"decision": "skip", "confidence": 0.3, "reason": "tangential"},
    ])
    with patch("lore_curator.llm_client.make_llm_client", return_value=client):
        _attribute_commits_with_judgment(env["repo"])
        # Second invocation in same session — should not call LLM again.
        _attribute_commits_with_judgment(env["repo"])

    assert len(client.messages.calls) == 1
