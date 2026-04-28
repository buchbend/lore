"""Regression tests for the hook crash logger + top-level backstop.

The shield in ``lore_cli.hooks._shield_hook`` only catches exceptions
raised inside the hook function body. Earlier failures (module import,
typer parameter resolution, anything during Click dispatch) escape it
and surface to Claude Code as ``Failed with non-blocking status code:
<rich-rendered traceback>`` — exactly the noise this work removes.

These tests cover three layers:

1. ``write_crash`` persists a usable file and degrades silently when
   the cache is unwritable.
2. ``_shield_hook`` calls ``write_crash`` and the path appears in the
   banner the user sees.
3. ``__main__.main()``'s top-level except writes a crash file AND
   emits a hook-shaped JSON envelope when argv is a hook call (so
   Claude Code shows a banner, not a traceback).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_write_crash_persists_traceback(tmp_path, monkeypatch):
    """Happy path: a real exception → a file with the traceback in it."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import write_crash

    try:
        raise RuntimeError("simulated boom")
    except RuntimeError as exc:
        path = write_crash("SessionStart", exc)

    assert path is not None
    assert path.exists()
    body = path.read_text()
    assert "RuntimeError" in body
    assert "simulated boom" in body
    assert "event: SessionStart" in body
    # Timestamped under the configured cache root.
    assert path.parent == tmp_path / "crashes"
    assert path.name.endswith("-SessionStart.log")


def test_write_crash_returns_none_on_unwritable_cache(tmp_path, monkeypatch):
    """If cache is unwritable, write_crash must return None — never raise.

    The crash logger runs from inside an exception handler; raising
    would mask the original failure with a different one.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    # Point LORE_CACHE at a path that can't be turned into a dir.
    monkeypatch.setenv("LORE_CACHE", str(blocker))
    from lore_cli._crash_log import write_crash

    try:
        raise ValueError("boom")
    except ValueError as exc:
        path = write_crash("SessionStart", exc)
    assert path is None


def test_recent_crashes_returns_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import recent_crashes, write_crash

    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        p1 = write_crash("SessionStart", exc)
    try:
        raise RuntimeError("second")
    except RuntimeError as exc:
        p2 = write_crash("PreCompact", exc)
    # Force ordering by mtime even on coarse-resolution filesystems.
    import os
    import time
    os.utime(p1, (time.time() - 60, time.time() - 60))

    recent = recent_crashes(within_days=7)
    assert recent[0] == p2
    assert p1 in recent


def test_recent_crashes_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "does-not-exist"))
    from lore_cli._crash_log import recent_crashes

    assert recent_crashes() == []


def test_shield_writes_crash_log_and_includes_path_in_banner(
    tmp_path, monkeypatch, capsys
):
    """End-to-end: shield catches an unexpected exception, writes a
    crash file, and the file path appears in the user-facing banner."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli import hooks

    def _boom(*_args, **_kwargs):
        raise RuntimeError("inside _session_start")

    monkeypatch.setattr(hooks, "_session_start", _boom)
    monkeypatch.setattr(hooks, "_in_curator_mode", lambda: False)
    monkeypatch.setattr(hooks, "_session_off_all", lambda: False)
    monkeypatch.setattr(hooks, "_read_hook_payload", lambda: {})
    monkeypatch.setattr(hooks, "_resolve_cwd", lambda _explicit: str(tmp_path))

    hooks.cmd_session_start(cwd=str(tmp_path), plain=False, probe=True)

    out = capsys.readouterr().out
    envelope = json.loads(out)
    full = envelope["hookSpecificOutput"]["additionalContext"]
    assert "Full traceback:" in full

    # The crash file must exist and contain the original traceback.
    crash_dir = tmp_path / "crashes"
    files = list(crash_dir.glob("*-SessionStart.log"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "RuntimeError" in body
    assert "inside _session_start" in body


def test_main_backstop_catches_pre_dispatch_exception(tmp_path, monkeypatch, capsys):
    """An exception that escapes typer dispatch (i.e. not caught by the
    per-hook shield) must still produce a JSON envelope + crash log
    when argv is a hook call. Simulate by patching the hook subapp to
    raise a generic Exception during dispatch — main()'s fast path for
    `lore hook ...` (issue #27 perf fix) calls hook_app directly."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli import __main__ as cli_main
    from lore_cli import hooks as hooks_mod

    def _broken_hook_app(*_args, **_kwargs):
        raise RuntimeError("simulated typer dispatch failure")

    monkeypatch.setattr(hooks_mod, "hook_app", _broken_hook_app)

    code = cli_main.main(["hook", "session-start"])
    # Exit 0 so Claude Code suppresses the "non-blocking status code" panel.
    assert code == 0

    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert "systemMessage" in envelope
    assert "lore SessionStart hook failed" in envelope["systemMessage"]

    # And the traceback must be on disk for the maintainer.
    crash_dir = tmp_path / "crashes"
    files = list(crash_dir.glob("*-SessionStart.log"))
    assert len(files) == 1
    assert "simulated typer dispatch failure" in files[0].read_text()


def test_main_backstop_human_caller_writes_log_but_no_envelope(
    tmp_path, monkeypatch, capsys
):
    """For non-hook callers (e.g. a developer running `lore search foo`
    that crashes), main() should write the log + a terse stderr line
    instead of a JSON envelope."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli import __main__ as cli_main

    def _broken_app(*_args, **_kwargs):
        raise RuntimeError("human-facing failure")

    # Pre-seed the lazy `_app` singleton so main()'s slow path uses our
    # broken stub instead of building the real CLI. (Patching `app`
    # directly is silent: the slow path reads from `_app`, not `app`.)
    monkeypatch.setattr(cli_main, "_app", _broken_app)

    code = cli_main.main(["search", "foo"])
    assert code == 1

    captured = capsys.readouterr()
    assert captured.out == ""  # no JSON envelope on stdout
    assert "lore: unexpected error" in captured.err
    assert "RuntimeError" in captured.err

    files = list((tmp_path / "crashes").glob("*-main.log"))
    assert len(files) == 1


def test_doctor_check_recent_crashes_advisory(tmp_path, monkeypatch):
    """The doctor check is advisory — present crashes flip ok=False
    but do not fail the install."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli.doctor_cmd import _check_recent_crashes

    # Empty cache → green.
    ok, msg = _check_recent_crashes(str(tmp_path))
    assert ok is True
    assert "no hook crashes" in msg

    # Drop a crash log in place; check should now report it.
    from lore_cli._crash_log import write_crash
    try:
        raise RuntimeError("bang")
    except RuntimeError as exc:
        write_crash("SessionStart", exc)

    ok, msg = _check_recent_crashes(str(tmp_path))
    assert ok is False
    assert "1 hook crash" in msg
    assert "SessionStart" in msg
