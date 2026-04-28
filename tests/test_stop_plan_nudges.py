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

from lore_cli.hooks import (
    _missing_trailer_nudges_for_stop,
    _plan_trailer_nudges_for_stop,
    cmd_stop,
)
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


def _write_plan_with_step_body(
    wiki_root: Path, slug: str, *, step_files: dict[str, list[str]]
) -> None:
    """Write a plan whose step bodies mention file paths (one set per step).

    ``step_files = {"s1": ["lib/foo.py", "tests/test_foo.py"], "s2": [...]}``.
    The body for each step embeds the listed paths as backtick code spans
    so the missing-trailer detector's path regex picks them up.
    """
    fm = [
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
        "---",
    ]
    body = [f"\n# {slug}\n\n## Steps\n"]
    for sid, files in step_files.items():
        body.append(f"\n### {sid}: step {sid}\n")
        for f in files:
            body.append(f"- touch `{f}`\n")
    (wiki_root / "plans" / f"{slug}.md").write_text(
        "\n".join(fm) + "".join(body)
    )


def _commit_with_file(repo: Path, file_rel: str, message: str) -> str:
    """Write/append to ``file_rel`` (relative to repo) and commit."""
    target = repo / file_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    # Append a unique line so successive commits change content.
    target.write_text((target.read_text() if target.exists() else "") + "// edit\n")
    subprocess.run(
        ["git", "add", file_rel], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
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


# ---------------------------------------------------------------------------
# Missing-trailer detector — soft prompts when commits forget the trailer
# ---------------------------------------------------------------------------


def test_missing_trailer_nudge_when_commit_files_overlap_step_body(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit touches a file the active plan's step body mentions, no
    `Plan:` trailer in the message → soft prompt naming the slug+step."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"], "s2": ["lib/auth/oidc.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-missing-1")

    sha = _commit_with_file(
        env["repo"], "lib/auth/login.py", "Wire OIDC config"
    )

    nudges = _missing_trailer_nudges_for_stop(env["repo"])
    assert len(nudges) == 1
    assert sha in nudges[0]
    assert "refactor-auth#s1" in nudges[0]
    assert "no `Plan:` trailer" in nudges[0]
    assert "`Plan: refactor-auth#s1`" in nudges[0]


def test_missing_trailer_silent_when_trailer_present(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trailer-bearing commit must NOT also fire the missing-trailer
    nudge — that would be redundant with the action pass."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-missing-2")

    target = env["repo"] / "lib/auth/login.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("// edit\n")
    subprocess.run(["git", "add", "lib/auth/login.py"], cwd=env["repo"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Wire OIDC\n\nPlan: refactor-auth#s1"],
        cwd=env["repo"], check=True, capture_output=True,
    )

    nudges = _missing_trailer_nudges_for_stop(env["repo"])
    assert nudges == []


def test_missing_trailer_silent_when_no_file_overlap(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit touches a file the plan body never mentions → no nudge.
    The detector must not fire on every untrailed commit, only on
    those that plausibly closed plan work."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-missing-3")

    _commit_with_file(env["repo"], "docs/unrelated.md", "Doc tweak")

    nudges = _missing_trailer_nudges_for_stop(env["repo"])
    assert nudges == []


def test_missing_trailer_dedup_within_session(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same commit must only nudge once per session. The seen-set is
    namespaced separately from the action pass."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-missing-4")

    _commit_with_file(env["repo"], "lib/auth/login.py", "Wire it")

    first = _missing_trailer_nudges_for_stop(env["repo"])
    second = _missing_trailer_nudges_for_stop(env["repo"])
    assert len(first) == 1
    assert second == [], "second Stop in same session must stay quiet"


def test_missing_trailer_no_active_plans_returns_empty(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No active plans → nothing to compare against → empty result."""
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-missing-5")
    _commit_with_file(env["repo"], "lib/anything.py", "edit")
    assert _missing_trailer_nudges_for_stop(env["repo"]) == []


def test_missing_trailer_unattached_cwd_returns_empty(tmp_path: Path) -> None:
    """No attachment → no nudges, no crash."""
    assert _missing_trailer_nudges_for_stop(tmp_path) == []


def test_missing_trailer_action_and_missing_coexist(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two commits: one with trailer (action pass advances it), one
    without (missing pass nudges). Both passes run from cmd_stop, so
    both messages should reach the user. This pins that the two
    seen-set namespaces don't collide either."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"], "s2": ["lib/auth/oidc.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-coexist")

    # Commit 1: trailer present → action pass advances s1.
    target1 = env["repo"] / "lib/auth/login.py"
    target1.parent.mkdir(parents=True, exist_ok=True)
    target1.write_text("// 1\n")
    subprocess.run(["git", "add", "lib/auth/login.py"], cwd=env["repo"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Step 1\n\nPlan: refactor-auth#s1"],
        cwd=env["repo"], check=True, capture_output=True,
    )

    # Commit 2: no trailer, but touches the file mentioned in s2.
    sha2 = _commit_with_file(
        env["repo"], "lib/auth/oidc.py", "Wire OIDC (forgot the trailer)"
    )

    action_msgs = _plan_trailer_nudges_for_stop(env["repo"])
    missing_msgs = _missing_trailer_nudges_for_stop(env["repo"])

    # Action pass advanced s1 (one ✓ line).
    assert len(action_msgs) == 1
    assert "marked plan/refactor-auth#s1 done" in action_msgs[0]

    # Missing pass nudges for s2 (the suggested step is the now-current
    # in-progress one — but since none is in_progress, it's the next pending).
    assert len(missing_msgs) == 1
    assert sha2 in missing_msgs[0]
    assert "refactor-auth#s2" in missing_msgs[0]


# ---------------------------------------------------------------------------
# Coalesce + cross-session bleed guard
# ---------------------------------------------------------------------------


def test_missing_trailer_coalesces_multiple_commits_into_one_line(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several untrailed commits against the same plan/step → ONE
    consolidated nudge listing the SHAs, not one nudge per commit.
    The fan-out (one line per commit) was spamming Stop output when a
    parallel session worked through a plan without trailers."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-coalesce")

    shas = [
        _commit_with_file(env["repo"], "lib/auth/login.py", f"edit {i}")
        for i in range(3)
    ]

    nudges = _missing_trailer_nudges_for_stop(env["repo"])

    assert len(nudges) == 1
    msg = nudges[0]
    assert "3 commits" in msg
    for sha in shas:
        assert sha in msg
    assert "refactor-auth#s1" in msg
    assert "`Plan: refactor-auth#s1`" in msg


def test_missing_trailer_caps_sha_list_at_five(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When more than five commits hit the same step, the line shows
    the first five and a ``+N more`` tail — keeps Stop output tight
    even on a long backlog."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-cap")

    for i in range(7):
        _commit_with_file(env["repo"], "lib/auth/login.py", f"edit {i}")

    nudges = _missing_trailer_nudges_for_stop(env["repo"])

    assert len(nudges) == 1
    assert "7 commits" in nudges[0]
    assert "+2 more" in nudges[0]


def test_missing_trailer_skips_commits_before_session_start(
    env: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Commits whose committer-time predates this session's transcript
    must not nudge — they belong to a parallel session. This is the
    cross-session bleed guard: without it, every Stop in a fresh
    session would re-litigate the other session's commits."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-bleed")

    # Make the "other-session" commit happen with an old committer date.
    target = env["repo"] / "lib/auth/login.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("// old session edit\n")
    subprocess.run(
        ["git", "add", "lib/auth/login.py"],
        cwd=env["repo"], check=True, capture_output=True,
    )
    old_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
    }
    subprocess.run(
        ["git", "commit", "-m", "old session edit"],
        cwd=env["repo"], check=True, capture_output=True, env=old_env,
    )

    # Fabricate a transcript for our session whose first record is from
    # 2026 — well after the old commit.
    encoded = str(env["repo"].resolve()).replace("/", "-")
    transcript_dir = env["cache_home"] / ".claude" / "projects" / encoded
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "test-bleed.jsonl").write_text(
        json.dumps({"timestamp": "2026-04-28T12:00:00Z"}) + "\n"
    )

    nudges = _missing_trailer_nudges_for_stop(env["repo"])

    # Old commit predates session_floor → filtered out → no nudge.
    assert nudges == []


def test_missing_trailer_emits_for_in_session_commits(
    env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check the bleed guard isn't over-aggressive: when the
    transcript anchor is *before* the commit, the nudge fires normally."""
    _write_plan_with_step_body(
        env["wiki_root"],
        "refactor-auth",
        step_files={"s1": ["lib/auth/login.py"]},
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-in-session")

    encoded = str(env["repo"].resolve()).replace("/", "-")
    transcript_dir = env["cache_home"] / ".claude" / "projects" / encoded
    transcript_dir.mkdir(parents=True)
    # Anchor far in the past so any commit we make now is "after".
    (transcript_dir / "test-in-session.jsonl").write_text(
        json.dumps({"timestamp": "2000-01-01T00:00:00Z"}) + "\n"
    )

    sha = _commit_with_file(env["repo"], "lib/auth/login.py", "wire it")

    nudges = _missing_trailer_nudges_for_stop(env["repo"])
    assert len(nudges) == 1
    assert sha in nudges[0]
