"""Tests for ``lore hook plan-capture`` (PostToolUse:ExitPlanMode).

Covers:

* Captured fixture (accepted plan) → file written + systemMessage.
* Rejected → silent, no write.
* Unattached cwd → soft hint, no write.
* Stdin TTY / empty / oversized → handled per outcome.
* Malformed JSON → orphan dump.
* No-plan-in-payload → orphan dump.
* Idempotent re-acceptance → silent dedup.
* Within-day vs day-boundary update message variants.
* Top-level exception → orphan dump (write_plan_note mocked to raise).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_cli.hooks import cmd_plan_capture
from lore_core.state.attachments import Attachment, AttachmentsFile

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Test infra
# ---------------------------------------------------------------------------


@pytest.fixture
def lore_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Set up LORE_ROOT, an isolated repo dir, and isolate the orphan-dump cache."""
    lore_root = tmp_path / "lore"
    (lore_root / "wiki" / "private").mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    cache_home = tmp_path / "home"
    cache_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: cache_home, raising=True)

    repo = tmp_path / "repo"
    repo.mkdir()

    return {
        "lore_root": lore_root,
        "wiki_root": lore_root / "wiki" / "private",
        "repo": repo,
        "cache_home": cache_home,
    }


def _attach(lore_root: Path, repo: Path, *, wiki: str = "private") -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=repo,
            wiki=wiki,
            scope=wiki,
            attached_at=datetime(2026, 4, 28, tzinfo=UTC),
            source="manual",
        )
    )
    af.save()


def _patch_stdin(
    monkeypatch: pytest.MonkeyPatch, payload: bytes, *, isatty: bool = False
) -> None:
    """Install fake stdin matching the io.read_hook_stdin contract."""

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
        def __init__(self, data: bytes, tty: bool) -> None:
            self.buffer = _FakeBuffer(data)
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload, isatty))


def _read_systemMessage(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and "systemMessage" in obj:
            return obj["systemMessage"]
    return None


def _payload_for(repo: Path, *, fixture: str = "exitplanmode-payload.json") -> dict:
    payload = json.loads((FIXTURES / fixture).read_text())
    payload["cwd"] = str(repo)
    return payload


# ---------------------------------------------------------------------------
# Captured fixture — happy path
# ---------------------------------------------------------------------------


def test_accepted_plan_files_to_wiki(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _attach(lore_env["lore_root"], lore_env["repo"])
    payload = _payload_for(lore_env["repo"])
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)

    plan_file = lore_env["wiki_root"] / "plans" / "refactor-authentication.md"
    assert plan_file.exists()
    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "filed" in msg
    assert "refactor-authentication" in msg
    assert "4 steps" in msg


def test_real_claude_code_payload_no_approved_field(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real ExitPlanMode tool_response has no `approved` key — captured 2026-04-28.

    Why: an earlier handler version gated on ``tool_response.approved`` and
    silently dropped every plan because Claude Code never sets that field.
    Claude Code only fires PostToolUse on user acceptance, so absence of the
    field must not block capture.
    """
    _attach(lore_env["lore_root"], lore_env["repo"])
    plan_text = (
        "# Refactor authentication\n\n"
        "## Steps\n\n"
        "### Step 1: Add OIDC config\n- thing\n\n"
        "### Step 2: Migrate sessions\n- thing\n"
    )
    payload = {
        "session_id": "01J9X7K3R4D5N6Q7T8V9W0X1Y2",
        "cwd": str(lore_env["repo"]),
        "hook_event_name": "PostToolUse",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": plan_text},
        "tool_response": {
            "plan": plan_text,
            "isAgent": False,
            "filePath": "/home/user/.claude/plans/foo.md",
            "hasTaskTool": True,
        },
    }
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)

    plan_file = lore_env["wiki_root"] / "plans" / "refactor-authentication.md"
    assert plan_file.exists()
    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "filed" in msg


def test_idempotent_reacceptance_silent_dedup(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _attach(lore_env["lore_root"], lore_env["repo"])
    payload = _payload_for(lore_env["repo"])
    raw = json.dumps(payload).encode("utf-8")

    _patch_stdin(monkeypatch, raw)
    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    capsys.readouterr()  # drain

    _patch_stdin(monkeypatch, raw)
    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    assert _read_systemMessage(capsys.readouterr().out) is None  # silent dedup


# ---------------------------------------------------------------------------
# Unattached / stdin edge cases
# ---------------------------------------------------------------------------


def test_unattached_cwd_emits_hint_no_write(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from lore_core.scope_resolver import resolve_scope
    assert resolve_scope(lore_env["repo"]) is None, (
        f"sanity: repo {lore_env['repo']} should be unattached but resolve_scope "
        f"returned a scope; LORE_ROOT={os.environ.get('LORE_ROOT')!r}"
    )

    payload = _payload_for(lore_env["repo"])
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)

    assert not (lore_env["wiki_root"] / "plans").exists()
    captured_out = capsys.readouterr().out
    msg = _read_systemMessage(captured_out)
    assert msg is not None, f"no systemMessage in stdout; got: {captured_out!r}"
    assert "/lore:attach" in msg


def test_tty_stdin_emits_friendly_hint(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human ran the hook by hand — never block on stdin; print a hint."""
    _attach(lore_env["lore_root"], lore_env["repo"])
    _patch_stdin(monkeypatch, b"", isatty=True)

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "stdin" in msg.lower()


def test_empty_stdin_silent_no_write(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _attach(lore_env["lore_root"], lore_env["repo"])
    _patch_stdin(monkeypatch, b"")

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    assert not (lore_env["wiki_root"] / "plans").exists()
    assert _read_systemMessage(capsys.readouterr().out) is None


def test_malformed_json_orphan_dumps(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _attach(lore_env["lore_root"], lore_env["repo"])
    _patch_stdin(monkeypatch, b"this is not JSON {")

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "orphan-plans" in msg or "failed" in msg
    orphan_dir = lore_env["cache_home"] / ".cache" / "lore" / "orphan-plans"
    assert orphan_dir.exists()
    dumps = list(orphan_dir.glob("*.json"))
    assert len(dumps) == 1
    assert b"this is not JSON" in dumps[0].read_bytes()


def test_no_plan_in_payload_orphan_dumps(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tool_input has no extractable plan field → orphan dump (don't lose it)."""
    _attach(lore_env["lore_root"], lore_env["repo"])
    payload = {
        "cwd": str(lore_env["repo"]),
        "tool_response": {"approved": True},
        "tool_input": {"id": "abc", "session": "x"},
    }
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    capsys.readouterr()
    orphan_dir = lore_env["cache_home"] / ".cache" / "lore" / "orphan-plans"
    assert orphan_dir.exists()
    assert len(list(orphan_dir.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# Top-level exception → orphan dump
# ---------------------------------------------------------------------------


def test_writer_exception_orphan_dumps(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Make write_plan_note raise — payload must end up in orphan-dump."""
    _attach(lore_env["lore_root"], lore_env["repo"])

    def _boom(**kwargs):
        raise RuntimeError("simulated writer failure")

    monkeypatch.setattr("lore_core.plans.writer.write_plan_note", _boom)

    payload = _payload_for(lore_env["repo"])
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "failed" in msg
    orphan_dir = lore_env["cache_home"] / ".cache" / "lore" / "orphan-plans"
    assert orphan_dir.exists()
    assert len(list(orphan_dir.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# Within-day vs day-boundary update messages
# ---------------------------------------------------------------------------


def test_within_day_update_message_form(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-capture same day with different content: quiet 'updated · N steps'."""
    _attach(lore_env["lore_root"], lore_env["repo"])

    payload = _payload_for(lore_env["repo"])
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))
    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    capsys.readouterr()

    payload["tool_input"]["plan"] = (
        "# Refactor authentication\n\n"
        "## Steps\n\n### Step 1: Updated step\nnew body\n\n"
        "### Step 2: another\nx\n\n### Step 3: another\ny\n\n### Step 4: another\nz\n"
    )
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))
    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)

    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "updated" in msg
    assert "4 steps" in msg
    assert "preserved" not in msg  # within-day form is short


def test_day_boundary_update_message_form(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-capture across a day boundary: long-form preserved-status message."""
    import re

    _attach(lore_env["lore_root"], lore_env["repo"])

    payload = _payload_for(lore_env["repo"])
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))
    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)
    capsys.readouterr()

    plan_file = lore_env["wiki_root"] / "plans" / "refactor-authentication.md"
    text = re.sub(
        r"^last_reviewed:.*$",
        "last_reviewed: 1900-01-01",
        plan_file.read_text(),
        count=1,
        flags=re.MULTILINE,
    )
    plan_file.write_text(text)

    payload["tool_input"]["plan"] = (
        "# Refactor authentication\n\n"
        "## Steps\n\n### Step 1: Updated\nnew\n\n### Step 2: x\nx\n\n"
        "### Step 3: y\ny\n\n### Step 4: z\nz\n"
    )
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))
    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=False)

    msg = _read_systemMessage(capsys.readouterr().out)
    assert msg is not None
    assert "updated" in msg
    assert "preserved" in msg


# ---------------------------------------------------------------------------
# Plain mode (no JSON envelope)
# ---------------------------------------------------------------------------


def test_plain_mode_emits_raw_text(
    lore_env: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _attach(lore_env["lore_root"], lore_env["repo"])
    payload = _payload_for(lore_env["repo"])
    _patch_stdin(monkeypatch, json.dumps(payload).encode("utf-8"))

    cmd_plan_capture(cwd=str(lore_env["repo"]), plain=True)

    text = capsys.readouterr().out
    assert "filed" in text
    assert text.lstrip().startswith("lore:")
