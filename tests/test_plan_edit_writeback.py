"""Tests for ``lore hook plan-edit-writeback`` (PostToolUse:Edit/Write).

Auto-flips ``pending → in_progress`` for plan steps whose ``step_files``
overlap with the just-edited file. Deterministic — no LLM call.

Covers:

* Edit on a file in step_files → step transitions pending → in_progress.
* Edit on a file with no overlap → no-op.
* Edit on a step already in_progress / done → no-op.
* Multiple plans with overlapping step_files → all matching plans flip.
* Multiple steps within one plan share a file → all matching pending
  steps flip.
* Plans without step_files → never flipped (no false positives).
* Unattached cwd / no repo / missing wiki → silent no-op.
* Relative file_path (defensive) and absolute file_path (typical) both
  match correctly.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_cli.hooks import cmd_plan_edit_writeback
from lore_core.plans.types import StepStatus
from lore_core.plans.writer import write_plan_note
from lore_core.plans.types import PlanStep, StructuredPlan
from lore_core.plans.step_status import set_step
from lore_core.schema import parse_frontmatter
from lore_core.state.attachments import Attachment, AttachmentsFile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    lore_root = tmp_path / "lore"
    (lore_root / "wiki" / "private").mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    cache_home = tmp_path / "home"
    cache_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: cache_home, raising=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    # Make `repo` a real git repo so current_repo / git_repo_root resolve.
    import subprocess
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

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

    return {
        "lore_root": lore_root,
        "wiki_root": lore_root / "wiki" / "private",
        "repo": repo,
    }


def _patch_stdin(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")

    class _FakeBuffer:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                out, self._data = self._data, b""
                return out
            out, self._data = self._data[:n], self._data[n:]
            return out

    class _FakeStdin:
        def __init__(self, data: bytes) -> None:
            self.buffer = _FakeBuffer(data)

        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", _FakeStdin(data))


def _file_a_plan(
    wiki_root: Path,
    *,
    slug: str,
    step_files: dict[str, list[str]],
    repo: str = "test/repo",
) -> Path:
    """File a plan with the given step_files and return its path."""
    steps = [
        PlanStep(id=sid, title=f"Step {sid}", body="...", files=files)
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


def _read_step_status(plan_path: Path) -> dict[str, str]:
    fm = parse_frontmatter(plan_path.read_text())
    return fm.get("step_status") or {}


def _payload(repo: Path, file_path: str) -> dict:
    """Build a Claude Code PostToolUse:Edit-shaped payload."""
    return {
        "tool_input": {"file_path": file_path},
        "tool_response": {},
        "cwd": str(repo),
        "session_id": "test-session",
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_edit_on_step_file_flips_pending_to_in_progress(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    assert _read_step_status(plan_path) == {}  # initially pending

    abs_file = env["repo"] / "lib" / "foo.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(plan_path) == {"step-1": "in_progress"}


def test_edit_on_unrelated_file_is_noop(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )

    abs_file = env["repo"] / "lib" / "unrelated.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(plan_path) == {}


def test_edit_when_step_already_in_progress_is_noop(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    set_step(
        wiki_root=env["wiki_root"],
        slug="test-plan",
        step_id="step-1",
        status=StepStatus.IN_PROGRESS,
    )
    fm_before = parse_frontmatter(plan_path.read_text())
    timestamp_before = fm_before.get("step_status_updated")

    abs_file = env["repo"] / "lib" / "foo.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    fm_after = parse_frontmatter(plan_path.read_text())
    assert fm_after["step_status"] == {"step-1": "in_progress"}
    # Idempotent — timestamp not bumped because nothing changed.
    assert fm_after.get("step_status_updated") == timestamp_before


def test_edit_when_step_done_is_noop(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    abs_file = env["repo"] / "lib" / "foo.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    # Done stays done — no regression to in_progress.
    assert _read_step_status(plan_path) == {"step-1": "done"}


def test_edit_flips_multiple_plans(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    abs_file = env["repo"] / "lib" / "shared.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(p1) == {"step-1": "in_progress"}
    assert _read_step_status(p2) == {"step-1": "in_progress"}


def test_edit_flips_multiple_pending_steps_in_same_plan(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={
            "step-1": ["lib/foo.py", "lib/shared.py"],
            "step-2": ["lib/shared.py"],
        },
    )

    abs_file = env["repo"] / "lib" / "shared.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(plan_path) == {
        "step-1": "in_progress",
        "step-2": "in_progress",
    }


def test_plan_without_step_files_is_never_flipped(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Plans authored before step_files (no Files: lines) get no
    # step_files frontmatter — must NOT auto-flip on any edit, since we
    # have no signal to attribute the edit to a specific step.
    plan = StructuredPlan(
        slug="legacy",
        title="Legacy",
        body_intro="",
        steps=[PlanStep(id="step-1", title="t", body="b")],  # no files
        mode="headings",
    )
    result = write_plan_note(
        wiki_root=env["wiki_root"],
        plan=plan,
        source_hash="sha256:legacy",
        source_adapter="test",
        repo="test/repo",
    )
    abs_file = env["repo"] / "lib" / "anything.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(result.path) == {}


# ---------------------------------------------------------------------------
# Defensive / edge cases
# ---------------------------------------------------------------------------


def test_unattached_cwd_is_silent_noop(
    env: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Working dir not registered in attachments — handler must not crash.
    other = tmp_path / "outside"
    other.mkdir()
    abs_file = other / "foo.py"
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(other, str(abs_file)))

    # No exception expected.
    cmd_plan_edit_writeback(cwd=str(other))


def test_no_active_plans_is_silent_noop(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No plans at all — handler does nothing.
    abs_file = env["repo"] / "lib" / "foo.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))


def test_relative_file_path_matches(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Defensive: if file_path arrives as a relative path (uncommon but
    # possible), it should still match against step_files entries.
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _patch_stdin(monkeypatch, _payload(env["repo"], "lib/foo.py"))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(plan_path) == {"step-1": "in_progress"}


def test_missing_file_path_in_payload_is_silent_noop(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = _file_a_plan(
        env["wiki_root"],
        slug="test-plan",
        step_files={"step-1": ["lib/foo.py"]},
    )
    _patch_stdin(monkeypatch, {
        "tool_input": {},  # no file_path
        "tool_response": {},
        "cwd": str(env["repo"]),
        "session_id": "test",
    })

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(plan_path) == {}


def test_repo_filter_excludes_other_repo_plans(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plan tagged for a different repo must NOT match edits in this repo.
    other_plan = _file_a_plan(
        env["wiki_root"],
        slug="other-repo-plan",
        step_files={"step-1": ["lib/foo.py"]},
        repo="someone-else/other-repo",
    )

    abs_file = env["repo"] / "lib" / "foo.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.touch()
    _patch_stdin(monkeypatch, _payload(env["repo"], str(abs_file)))

    cmd_plan_edit_writeback(cwd=str(env["repo"]))

    assert _read_step_status(other_plan) == {}
