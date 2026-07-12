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

The ``_disable_dev_filter`` fixture is autouse: ``write_crash`` skips
writes when invoked from ``pytest`` or ``--dry-run`` argv (so test
fakes and local debug runs don't pollute the user's real crash dir),
but the tests below need the write path to actually fire — they
explicitly cover that path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_dev_filter(monkeypatch):
    """Bypass the pytest/--dry-run skip in write_crash for this test module.

    The skip exists so simulated test failures don't land in
    ``~/.cache/lore/crashes/`` and inflate ``lore doctor``'s count.
    But these tests are exactly the ones that exercise the write
    path, so they need to opt out.
    """
    monkeypatch.setattr("lore_cli._crash_log._is_dev_invocation", lambda: False)


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


def test_write_crash_skipped_when_pytest_in_argv(tmp_path, monkeypatch):
    """pytest test runs intentionally simulate hook failures (see
    ``test_directive_template.py``). Their tracebacks must NOT land in
    the user's real ``~/.cache/lore/crashes/`` and inflate
    ``lore doctor``'s reported crash count.
    """
    # Override the autouse fixture for THIS test only — we want the
    # dev-invocation filter active here.
    monkeypatch.setattr("lore_cli._crash_log._is_dev_invocation", lambda: True)
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import write_crash

    try:
        raise RuntimeError("simulated test failure")
    except RuntimeError as exc:
        path = write_crash("SessionStart", exc)
    assert path is None
    crash_dir = tmp_path / "crashes"
    assert not crash_dir.exists() or not list(crash_dir.iterdir())


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


# ---------------------------------------------------------------------------
# #190 — explicit retention: crash logs no longer accumulate forever.
# ---------------------------------------------------------------------------


def test_purge_old_crashes_deletes_past_window(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import purge_old_crashes, write_crash

    try:
        raise RuntimeError("old")
    except RuntimeError as exc:
        old_path = write_crash("SessionStart", exc)
    import os
    import time

    os.utime(old_path, (time.time() - 40 * 86400, time.time() - 40 * 86400))

    deleted, failed = purge_old_crashes(30, lore_root=tmp_path)
    assert deleted == 1
    assert failed == 0
    assert not old_path.exists()


def test_purge_old_crashes_keeps_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import purge_old_crashes, write_crash

    try:
        raise RuntimeError("recent")
    except RuntimeError as exc:
        recent_path = write_crash("SessionStart", exc)

    deleted, failed = purge_old_crashes(30, lore_root=tmp_path)
    assert deleted == 0
    assert recent_path.exists()


def test_purge_old_crashes_emits_janitor_spine_event(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import purge_old_crashes, write_crash
    from lore_core.spine import read_spine, validate_envelope

    try:
        raise RuntimeError("old")
    except RuntimeError as exc:
        old_path = write_crash("SessionStart", exc)
    import os
    import time

    os.utime(old_path, (time.time() - 40 * 86400, time.time() - 40 * 86400))

    purge_old_crashes(30, lore_root=tmp_path)
    events = [r for r in read_spine(tmp_path, source="janitor") if r["event"] == "retention-delete"]
    assert events
    assert events[0]["data"]["family"] == "crash-log"
    validate_envelope(events[0])


def test_purge_old_crashes_failure_emits_warn_event(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import purge_old_crashes, write_crash
    from lore_core.spine import read_spine

    try:
        raise RuntimeError("old")
    except RuntimeError as exc:
        old_path = write_crash("SessionStart", exc)
    import os
    import time

    os.utime(old_path, (time.time() - 40 * 86400, time.time() - 40 * 86400))

    real_unlink = Path.unlink

    def bad_unlink(self, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", bad_unlink)
    deleted, failed = purge_old_crashes(30, lore_root=tmp_path)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    assert failed == 1
    failures = [
        r for r in read_spine(tmp_path, source="janitor") if r["event"] == "retention-delete-failed"
    ]
    assert failures
    assert failures[0]["level"] == "warn"
    assert failures[0]["data"]["family"] == "crash-log"


def test_purge_old_crashes_without_lore_root_still_deletes(tmp_path, monkeypatch):
    """No lore_root -> best-effort deletion, no spine emission (nowhere to write it)."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_cli._crash_log import purge_old_crashes, write_crash

    try:
        raise RuntimeError("old")
    except RuntimeError as exc:
        old_path = write_crash("SessionStart", exc)
    import os
    import time

    os.utime(old_path, (time.time() - 40 * 86400, time.time() - 40 * 86400))

    deleted, failed = purge_old_crashes(30)
    assert deleted == 1
    assert not old_path.exists()


def test_shield_writes_crash_log_and_includes_path_in_banner(tmp_path, monkeypatch, capsys):
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


def test_main_backstop_human_caller_writes_log_but_no_envelope(tmp_path, monkeypatch, capsys):
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
