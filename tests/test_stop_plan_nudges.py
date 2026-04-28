"""Tests for the Stop-hook plan-trailer nudge.

Closes the mid-session advance gap: SessionStart's breadcrumb nudge only
fires once per session, but ``Plan: <slug>#sN`` trailers can land on any
commit during a session. The Stop hook now scans the active plans for
unaddressed trailers and emits ⚠ nudges, with a per-session cursor so
the same trailer doesn't re-fire on every Stop.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_cli.hooks import _plan_trailer_nudges_for_stop, cmd_stop
from lore_core.state.attachments import Attachment, AttachmentsFile


# ---------------------------------------------------------------------------
# Test infra
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """LORE_ROOT + a real git repo + isolated cache home."""
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
        ["git", "commit", "--allow-empty", "-m", "initial"],
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


def _write_plan_note(
    wiki_root: Path,
    slug: str,
    *,
    step_status: dict[str, str] | None = None,
    step_status_updated: str | None = None,
) -> None:
    fm_lines = [
        "---",
        "schema_version: 2",
        "type: plan",
        f"slug: {slug}",
        "status: active",
        "created: '2026-04-28'",
        "last_reviewed: '2026-04-28'",
        f"description: {slug}",
        "source_adapter: claude-code-hook",
        "source_hash: sha256:deadbeef",
    ]
    if step_status:
        fm_lines.append("step_status:")
        for sid, status in step_status.items():
            fm_lines.append(f"  {sid}: {status}")
    if step_status_updated:
        fm_lines.append(f"step_status_updated: '{step_status_updated}'")
    fm_lines.append("---")
    body = (
        "\n# {slug}\n\n## Steps\n\n"
        "### s1: First\n\n### s2: Second\n\n### s3: Third\n"
    ).format(slug=slug)
    (wiki_root / "plans" / f"{slug}.md").write_text("\n".join(fm_lines) + body)


def _commit_with_trailer(repo: Path, message: str) -> str:
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


def test_trailer_auto_advances_step(env: dict) -> None:
    """A `Plan: <slug>#sN` trailer auto-advances the step at Stop time
    and emits a confirmation line — no manual `/lore:plan-step` action
    needed. Verifies the on-disk `step_status` write too."""
    _write_plan_note(env["wiki_root"], "refactor-auth")
    sha = _commit_with_trailer(env["repo"], "Wire OIDC config\n\nPlan: refactor-auth#s1")

    nudges = _plan_trailer_nudges_for_stop(env["repo"])
    assert len(nudges) == 1
    assert sha in nudges[0]
    assert "refactor-auth#s1" in nudges[0]
    assert nudges[0].startswith("✓ marked plan/")
    # And the on-disk plan note must reflect the advance.
    plan_text = (env["wiki_root"] / "plans" / "refactor-auth.md").read_text()
    assert "s1: done" in plan_text


def test_no_nudge_when_step_already_done(env: dict) -> None:
    _write_plan_note(
        env["wiki_root"],
        "refactor-auth",
        step_status={"s1": "done"},
        step_status_updated="2099-01-01T00:00:00Z",  # newer than any commit
    )
    _commit_with_trailer(env["repo"], "Wire OIDC config\n\nPlan: refactor-auth#s1")

    nudges = _plan_trailer_nudges_for_stop(env["repo"])
    assert nudges == []


def test_per_session_cursor_silences_repeat(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same unadvanced trailer must not nudge twice in one session."""
    _write_plan_note(env["wiki_root"], "refactor-auth")
    _commit_with_trailer(env["repo"], "Wire OIDC config\n\nPlan: refactor-auth#s1")

    # Pin session_id so both calls share a cursor file.
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-session-deadbeef")

    first = _plan_trailer_nudges_for_stop(env["repo"])
    second = _plan_trailer_nudges_for_stop(env["repo"])

    assert len(first) == 1
    assert second == [], "second Stop in same session must stay quiet"


def test_new_trailer_after_cursor_does_nudge(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh commit after the cursor was set still produces a nudge."""
    _write_plan_note(env["wiki_root"], "refactor-auth")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-session-deadbeef")

    _commit_with_trailer(env["repo"], "First step\n\nPlan: refactor-auth#s1")
    first = _plan_trailer_nudges_for_stop(env["repo"])
    assert len(first) == 1

    # Sleep is overkill — git commits get distinct ISO timestamps already.
    _commit_with_trailer(env["repo"], "Second step\n\nPlan: refactor-auth#s2")
    second = _plan_trailer_nudges_for_stop(env["repo"])
    assert len(second) == 1
    assert "refactor-auth#s2" in second[0]


def test_unattached_cwd_returns_empty(tmp_path: Path) -> None:
    """No attachment → no nudges, no crash."""
    nudges = _plan_trailer_nudges_for_stop(tmp_path)
    assert nudges == []


def test_no_active_plans_returns_empty(env: dict) -> None:
    """Attached but no plan notes → empty."""
    _commit_with_trailer(env["repo"], "Random commit\n\nPlan: ghost-slug#s1")
    nudges = _plan_trailer_nudges_for_stop(env["repo"])
    assert nudges == []


# ---------------------------------------------------------------------------
# Stop-command integration
# ---------------------------------------------------------------------------


def _patch_stdin(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
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

        def read(self, *a, **kw) -> str:
            return self.buffer.read(-1).decode()

    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload))


def test_cmd_stop_emits_nudge_envelope(
    env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full Stop hook path: nudge surfaces in the systemMessage envelope."""
    _write_plan_note(env["wiki_root"], "refactor-auth")
    _commit_with_trailer(env["repo"], "Wire OIDC\n\nPlan: refactor-auth#s1")

    payload = {"cwd": str(env["repo"]), "session_id": "stop-test-sid"}
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_stop(plain=False)

    out = capsys.readouterr().out
    msg = None
    for line in out.splitlines():
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and "systemMessage" in obj:
            msg = obj["systemMessage"]
            break
    assert msg is not None
    assert "refactor-auth#s1" in msg
