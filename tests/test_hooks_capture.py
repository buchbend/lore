"""Tests for lore hook capture — hot-path ledger update + curator spawn."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from lore_adapters import register
from lore_adapters.registry import _REGISTRY
from lore_cli.hooks import hook_app, _spawn_detached_curator_a
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_core.types import TranscriptHandle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LORE_BLOCK = """\
# Project

## Lore

<!-- managed by /lore:attach -->

- wiki: testwiki
- scope: testscope
- backend: none
"""


def _make_attached_project(root: Path) -> Path:
    """Create a directory with an attached CLAUDE.md and required wiki layout."""
    project = root / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(LORE_BLOCK)
    # wiki directory so _infer_lore_root walks up correctly
    (project / "wiki" / "testwiki").mkdir(parents=True)
    return project


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_handle(
    cwd: Path,
    transcript_id: str = "t1",
    host: str = "fake",
    mtime: datetime | None = None,
) -> TranscriptHandle:
    return TranscriptHandle(
        host=host,
        id=transcript_id,
        path=cwd / f"{transcript_id}.jsonl",
        cwd=cwd,
        mtime=mtime or _now(),
    )


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------


class _FakeAdapter:
    host = "fake"

    def __init__(self, handles: list[TranscriptHandle]) -> None:
        self._handles = handles

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return self._handles

    def read_slice_after_hash(self, *a, **kw):
        yield from ()

    def read_slice(self, *a, **kw):
        yield from ()

    def is_complete(self, h: TranscriptHandle) -> bool:
        return True


@pytest.fixture()
def fake_adapter_factory():
    """Fixture factory; registers adapter and cleans up."""

    registered: list[str] = []

    def make(handles: list[TranscriptHandle]) -> _FakeAdapter:
        adapter = _FakeAdapter(handles)
        register(adapter)
        registered.append(adapter.host)
        return adapter

    yield make

    for host in registered:
        _REGISTRY.pop(host, None)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

runner = CliRunner()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_capture_session_end_creates_ledger_entry(tmp_path: Path, fake_adapter_factory) -> None:
    """capture --event session-end creates a ledger entry for a new transcript."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(project)
    entry = ledger.get("fake", "t1")
    assert entry is not None
    assert entry.host == "fake"
    assert entry.transcript_id == "t1"
    assert entry.path == handle.path
    assert entry.directory == project
    assert entry.digested_hash is None


def test_capture_unattached_cwd_returns_without_ledger_write(tmp_path: Path) -> None:
    """capture on an unattached cwd silently no-ops — no ledger file created."""
    unattached = tmp_path / "unattached"
    unattached.mkdir()
    # No CLAUDE.md with ## Lore

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(unattached), "--host", "claude-code"],
        env={"LORE_ROOT": str(tmp_path)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger_path = tmp_path / ".lore" / "transcript-ledger.json"
    assert not ledger_path.exists()


def test_capture_under_100ms(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """capture returns in under 200ms (target <100ms; 200ms tolerance for CI)."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    # Prevent actual subprocess spawn
    monkeypatch.setattr("lore_cli.hooks._spawn_detached_curator_a", lambda *a, **kw: True)

    start = time.monotonic()
    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    elapsed = time.monotonic() - start

    assert result.exit_code == 0, result.output
    assert elapsed < 0.2, f"capture took {elapsed:.3f}s — must be <200ms"


def test_capture_spawns_when_threshold_exceeded(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """capture calls _spawn_detached_curator_a when pending >= threshold."""
    project = _make_attached_project(tmp_path)

    # Default threshold is 10. Pre-seed ledger with 9 pending entries.
    ledger = TranscriptLedger(project)
    for i in range(9):
        ledger.upsert(
            TranscriptLedgerEntry(
                host="fake",
                transcript_id=f"pre{i}",
                path=project / f"pre{i}.jsonl",
                directory=project,
                digested_hash=None,
                digested_index_hint=None,
                synthesised_hash=None,
                last_mtime=_now(),
                curator_a_run=None,
                noteworthy=None,
                session_note=None,
            )
        )

    # Adapter returns one more new transcript (total pending will be 3).
    handle = _make_handle(project, transcript_id="new1")
    fake_adapter_factory([handle])

    spawn_calls: list[Path] = []
    monkeypatch.setattr(
        "lore_cli.hooks._spawn_detached_curator_a",
        lambda lore_root, **kw: (spawn_calls.append(lore_root), True)[-1],
    )

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert len(spawn_calls) == 1, "Expected _spawn_detached_curator_a to be called once"
    assert spawn_calls[0] == project


def test_capture_hook_events_has_provenance_fields(tmp_path: Path, fake_adapter_factory) -> None:
    """capture emits hook-events record with pid, cwd, schema_version=2."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    import json
    import os
    events_path = project / ".lore" / "hook-events.jsonl"
    assert events_path.exists()
    record = json.loads(events_path.read_text().splitlines()[-1])
    assert record["schema_version"] == 2
    assert record["pid"] == os.getpid()
    assert record["cwd"] == str(project)
    # ppid_cmd is present (may be None on some systems)
    assert "ppid_cmd" in record


def test_capture_does_not_spawn_when_under_threshold(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """capture does NOT spawn when pending < threshold (default 3)."""
    project = _make_attached_project(tmp_path)

    # Only 1 pending transcript — below threshold of 3.
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    spawn_calls: list[Path] = []
    monkeypatch.setattr(
        "lore_cli.hooks._spawn_detached_curator_a",
        lambda lore_root, **kw: (spawn_calls.append(lore_root), True)[-1],
    )

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert len(spawn_calls) == 0, "Expected no spawn call below threshold"


def test_capture_session_start_same_behaviour(tmp_path: Path, fake_adapter_factory) -> None:
    """event=session-start produces the same ledger update as session-end."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, transcript_id="s1")
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-start", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(project)
    entry = ledger.get("fake", "s1")
    assert entry is not None
    assert entry.transcript_id == "s1"


def test_capture_explicit_transcript_path_filters_handles(
    tmp_path: Path, fake_adapter_factory
) -> None:
    """When --transcript is given, only that path's handle lands in the ledger."""
    project = _make_attached_project(tmp_path)
    h1 = _make_handle(project, transcript_id="tx1")
    h2 = _make_handle(project, transcript_id="tx2")
    h3 = _make_handle(project, transcript_id="tx3")
    fake_adapter_factory([h1, h2, h3])

    result = runner.invoke(
        hook_app,
        [
            "capture",
            "--event", "session-end",
            "--cwd", str(project),
            "--host", "fake",
            "--transcript", str(h2.path),
        ],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(project)
    assert ledger.get("fake", "tx1") is None
    assert ledger.get("fake", "tx2") is not None
    assert ledger.get("fake", "tx3") is None


def test_capture_existing_entry_updates_mtime_when_changed(
    tmp_path: Path, fake_adapter_factory
) -> None:
    """If ledger already has an entry and mtime changed, it's updated."""
    project = _make_attached_project(tmp_path)
    old_mtime = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_mtime = datetime(2025, 6, 1, tzinfo=timezone.utc)

    # Pre-seed ledger with old mtime.
    ledger = TranscriptLedger(project)
    ledger.upsert(
        TranscriptLedgerEntry(
            host="fake",
            transcript_id="t1",
            path=project / "t1.jsonl",
            directory=project,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=old_mtime,
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )

    # Adapter reports a new mtime.
    handle = _make_handle(project, mtime=new_mtime)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    entry = ledger.get("fake", "t1")
    assert entry is not None
    assert entry.last_mtime == new_mtime


def test_capture_respects_lore_root_env(tmp_path: Path, fake_adapter_factory) -> None:
    """LORE_ROOT env var determines the ledger file location."""
    project = _make_attached_project(tmp_path)
    custom_lore_root = tmp_path / "custom_lore_root"
    custom_lore_root.mkdir()

    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(custom_lore_root)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    # Ledger must be under the custom lore root.
    ledger = TranscriptLedger(custom_lore_root)
    entry = ledger.get("fake", "t1")
    assert entry is not None


def test_capture_handles_unknown_host_gracefully(tmp_path: Path) -> None:
    """Unknown --host raises a typer Exit(1) or returns without crash."""
    project = _make_attached_project(tmp_path)

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "nonexistent"],
        env={"LORE_ROOT": str(project)},
    )
    # Either exit code 1 (explicit error) or 0 (silent no-op) is acceptable.
    # Must not raise an uncaught exception (exit code 2+ from typer crash
    # or a traceback is not acceptable).
    assert result.exit_code in (0, 1), (
        f"Expected exit code 0 or 1, got {result.exit_code}.\n{result.output}"
    )


def test_capture_emits_hook_event_happy_path(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """capture() writes one line to hook-events.jsonl with expected outcome."""
    import json
    from lore_cli.hooks import capture

    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, host="fake")
    fake_adapter_factory([handle])

    monkeypatch.setenv("LORE_ROOT", str(project))
    capture(event="session-end", cwd_override=project, host="fake")

    log = project / ".lore" / "hook-events.jsonl"
    assert log.exists(), "hook-events.jsonl should be created"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) >= 1
    latest = records[-1]
    assert latest["event"] == "session-end"
    assert latest["outcome"] in {"ledger-advanced", "below-threshold", "spawned-curator", "no-new-turns"}
    assert "duration_ms" in latest
    assert latest["error"] is None


def test_capture_error_path_logs_and_reraises(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """An adapter that raises during discovery should write outcome=error and re-raise."""
    import json
    from lore_cli import hooks

    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, host="fake")
    fake_adapter_factory([handle])

    monkeypatch.setenv("LORE_ROOT", str(project))

    def boom(*a, **kw):
        raise RuntimeError("adapter boom")

    monkeypatch.setattr(hooks, "get_adapter", boom)

    with pytest.raises(RuntimeError, match="boom"):
        hooks.capture(event="session-end", cwd_override=project, host="fake")

    log = project / ".lore" / "hook-events.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    errors = [r for r in records if r["outcome"] == "error"]
    assert errors, "expected at least one error record"
    assert errors[-1]["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Breadcrumb wiring tests
# ---------------------------------------------------------------------------


def test_capture_session_end_writes_breadcrumb_when_below_threshold(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """capture --event session-end with pending transcripts writes pending-breadcrumb.txt.

    Uses the CLI runner (like the other tests) so typer default resolution works.
    Pre-seeds ledger with entries that have digested_hash=None (pending).
    """
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    project = _make_attached_project(tmp_path)

    # Pre-seed ledger with 2 pending entries (no digested_hash) to produce below-threshold.
    ledger = TranscriptLedger(project)
    for i in range(2):
        ledger.upsert(
            TranscriptLedgerEntry(
                host="fake",
                transcript_id=f"pre{i}",
                path=project / f"pre{i}.jsonl",
                directory=project,
                digested_hash=None,
                digested_index_hint=None,
                synthesised_hash=None,
                last_mtime=_now(),
                curator_a_run=None,
                noteworthy=None,
                session_note=None,
            )
        )

    # Use the CLI runner so typer option defaults are resolved correctly.
    handle = _make_handle(project, host="fake")
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    crumb_path = project / ".lore" / "pending-breadcrumb.txt"
    assert crumb_path.exists(), "pending-breadcrumb.txt should be created"
    content = crumb_path.read_text()
    assert "below threshold" in content or "curator spawned" in content


def test_capture_session_end_no_breadcrumb_when_no_new_turns(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """When outcome=no-new-turns (all pending=0), no breadcrumb file is written."""
    from datetime import timezone
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    project = _make_attached_project(tmp_path)
    old_mtime = datetime(2024, 1, 1, tzinfo=timezone.utc)

    # Pre-seed ledger with a digested entry (same mtime as handle → no-new-turns).
    ledger = TranscriptLedger(project)
    ledger.upsert(
        TranscriptLedgerEntry(
            host="fake",
            transcript_id="t1",
            path=project / "t1.jsonl",
            directory=project,
            digested_hash="abc",  # already digested
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=old_mtime,
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )

    handle = _make_handle(project, host="fake", mtime=old_mtime)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    crumb_path = project / ".lore" / "pending-breadcrumb.txt"
    # no-new-turns → no breadcrumb written
    assert not crumb_path.exists(), "no breadcrumb expected when outcome=no-new-turns"


def test_capture_session_start_no_breadcrumb(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """capture --event session-start does NOT write a pending breadcrumb."""
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    project = _make_attached_project(tmp_path)

    # Pre-seed with 2 pending entries so there's a breadcrumb-worthy outcome.
    ledger = TranscriptLedger(project)
    for i in range(2):
        ledger.upsert(
            TranscriptLedgerEntry(
                host="fake",
                transcript_id=f"pre{i}",
                path=project / f"pre{i}.jsonl",
                directory=project,
                digested_hash=None,
                digested_index_hint=None,
                synthesised_hash=None,
                last_mtime=_now(),
                curator_a_run=None,
                noteworthy=None,
                session_note=None,
            )
        )

    handle = _make_handle(project, host="fake", transcript_id="s1")
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-start", "--cwd", str(project), "--host", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    crumb_path = project / ".lore" / "pending-breadcrumb.txt"
    assert not crumb_path.exists(), "session-start should not write a breadcrumb"


# ---------------------------------------------------------------------------
# Spawn-cooldown tests (issue #17)
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_subprocess(monkeypatch):
    """Intercept subprocess.Popen so spawn functions do not actually fork."""
    import subprocess as _subprocess

    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, cmd, **kw):
            calls.append(cmd)

    monkeypatch.setattr(_subprocess, "Popen", _FakePopen)
    return calls


CURATOR_PARAMS = [
    pytest.param("a", id="curator-a"),
    pytest.param("b", id="curator-b"),
]


def _stamp_path(lore_root: Path, role: str) -> Path:
    # Post-Task-2: stamp moved from last-curator-<role>-spawn to
    # curator-<role>.spawn.stamp (flock-based throttle; see lockfile.py).
    return lore_root / ".lore" / f"curator-{role}.spawn.stamp"


def _invoke_spawn(role: str, lore_root: Path, cooldown_s: int) -> bool:
    from lore_cli.hooks import (
        _spawn_detached_curator_a,
        _spawn_detached_curator_b,
    )
    if role == "a":
        return _spawn_detached_curator_a(lore_root, cooldown_s=cooldown_s)
    return _spawn_detached_curator_b(lore_root, "testwiki", cooldown_s=cooldown_s)


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_writes_stamp_file_on_success(tmp_path: Path, no_subprocess, role: str) -> None:
    """On successful spawn, a stamp file is written with a recent timestamp."""
    lore_root = tmp_path
    spawned = _invoke_spawn(role, lore_root, cooldown_s=60)
    assert spawned is True, f"expected first spawn to proceed; subprocess calls={no_subprocess}"
    assert len(no_subprocess) == 1
    stamp = _stamp_path(lore_root, role)
    assert stamp.exists(), f"stamp file {stamp} should be created"
    now = time.time()
    written = float(stamp.read_text().strip())
    assert abs(now - written) < 5.0, "stamp should be close to current time"


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_skipped_within_cooldown_window(
    tmp_path: Path, no_subprocess, role: str
) -> None:
    """A stamp file written 'recently' blocks a new spawn within the cooldown."""
    lore_root = tmp_path
    stamp = _stamp_path(lore_root, role)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{time.time() - 5.0:.6f}")  # 5s ago

    spawned = _invoke_spawn(role, lore_root, cooldown_s=60)
    assert spawned is False, "expected spawn to be skipped during cooldown"
    assert len(no_subprocess) == 0, "no subprocess should be spawned"


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_proceeds_after_cooldown_elapsed(
    tmp_path: Path, no_subprocess, role: str
) -> None:
    """A stamp file older than cooldown_s allows a new spawn."""
    lore_root = tmp_path
    stamp = _stamp_path(lore_root, role)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{time.time() - 120.0:.6f}")  # 120s ago
    old_value = stamp.read_text()

    spawned = _invoke_spawn(role, lore_root, cooldown_s=60)
    assert spawned is True
    assert len(no_subprocess) == 1
    new_value = stamp.read_text()
    assert new_value != old_value, "stamp should be refreshed on spawn"


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_proceeds_when_stamp_missing(
    tmp_path: Path, no_subprocess, role: str
) -> None:
    """Missing stamp file → treat as cooldown satisfied → spawn proceeds."""
    lore_root = tmp_path
    assert not _stamp_path(lore_root, role).exists()

    spawned = _invoke_spawn(role, lore_root, cooldown_s=60)
    assert spawned is True
    assert len(no_subprocess) == 1


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_robust_to_corrupt_stamp(
    tmp_path: Path, no_subprocess, role: str
) -> None:
    """An unreadable/corrupt stamp file should not prevent a spawn."""
    lore_root = tmp_path
    stamp = _stamp_path(lore_root, role)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("not-a-number\nabc")

    spawned = _invoke_spawn(role, lore_root, cooldown_s=60)
    assert spawned is True
    assert len(no_subprocess) == 1


def test_capture_does_not_write_to_real_lore_root(tmp_path, monkeypatch) -> None:
    """Regression: capture() must not leak records to the user's real vault.

    Verifies that when LORE_ROOT is monkeypatched to tmp_path, no writes
    reach the real production hook-events.jsonl.
    """
    import json
    import os
    from pathlib import Path
    from lore_cli.hooks import capture

    real_lore_root = os.environ.get("LORE_ROOT", "")
    real_events = (
        Path(real_lore_root) / ".lore" / "hook-events.jsonl"
        if real_lore_root
        else None
    )
    before_size = real_events.stat().st_size if real_events and real_events.exists() else -1

    # Isolated env
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, host="fake")

    # Register and clean up fake adapter
    from lore_adapters import register
    from lore_adapters.registry import _REGISTRY
    adapter = _FakeAdapter([handle])
    register(adapter)
    try:
        capture(event="session-end", cwd_override=project, host="fake")
    finally:
        _REGISTRY.pop("fake", None)

    # Verify the record went to the isolated tmp location, not the real vault.
    # LORE_ROOT is set to tmp_path, so HookEventLogger writes to tmp_path/.lore/
    isolated_log = tmp_path / ".lore" / "hook-events.jsonl"
    assert isolated_log.exists(), "capture() should write to the isolated tmp_path"
    records = [json.loads(line) for line in isolated_log.read_text().splitlines()]
    assert len(records) >= 1, "expected at least one record in isolated log"

    after_size = real_events.stat().st_size if real_events and real_events.exists() else -1
    assert after_size == before_size, (
        f"capture() leaked records to real LORE_ROOT={real_lore_root}! "
        f"size changed from {before_size} to {after_size}"
    )


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_uses_atomic_rename(
    tmp_path: Path, no_subprocess, monkeypatch, role: str
) -> None:
    """Stamp file is written via a tmp path + os.replace (atomic rename)."""
    lore_root = tmp_path
    replace_calls: list[tuple[str, str]] = []
    real_replace = __import__("os").replace

    def tracking_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", tracking_replace)

    _invoke_spawn(role, lore_root, cooldown_s=60)
    stamp = _stamp_path(lore_root, role)
    # exactly one os.replace targeting our stamp file
    matching = [c for c in replace_calls if c[1] == str(stamp)]
    assert matching, f"expected os.replace onto {stamp}, got {replace_calls}"
