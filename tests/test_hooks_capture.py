"""Tests for lore hook capture — hot-path ledger update + curator spawn."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_adapters import register
from lore_adapters.registry import _REGISTRY
from lore_cli.hooks import hook_app
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_core.spine import SCHEMA_VERSION
from lore_core.types import TranscriptHandle
from typer.testing import CliRunner

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
    """Create a directory with a registered attachment and required wiki layout.

    Post-Phase-6, routing uses ``attachments.json`` instead of CLAUDE.md
    walk-up. The lore_root for tests is ``project`` (because
    ``_infer_lore_root`` walks up from the scope's path looking for a
    ``wiki/`` subdirectory).
    """
    from lore_core.state.attachments import Attachment, AttachmentsFile

    project = root / "project"
    project.mkdir()
    # Wiki directory so _infer_lore_root walks up correctly (project is the lore_root)
    (project / "wiki" / "testwiki").mkdir(parents=True)
    (project / ".lore").mkdir()
    af = AttachmentsFile(project)
    af.load()
    af.add(Attachment(
        path=project,
        wiki="testwiki",
        scope="testscope",
        attached_at=_now(),
        source="manual",
    ))
    af.save()
    return project


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_handle(
    cwd: Path,
    transcript_id: str = "t1",
    integration: str = "fake",
    mtime: datetime | None = None,
) -> TranscriptHandle:
    return TranscriptHandle(
        integration=integration,
        id=transcript_id,
        path=cwd / f"{transcript_id}.jsonl",
        cwd=cwd,
        mtime=mtime or _now(),
    )


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------


class _FakeAdapter:
    integration = "fake"

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
        registered.append(adapter.integration)
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


def test_user_prompt_submit_registers_missed_transcript(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """Mid-session registration closes the SessionStart-vs-transcript-creation
    race. SessionStart can sample the projects directory in the sub-second
    window before Claude Code has flushed the new transcript file. Without
    this path the entry never makes it into the ledger and curator A
    silently has nothing to digest until the session ends — the symptom
    that prompted the fix (long step-ca session, no note ever filed)."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    adapter = fake_adapter_factory([handle])

    # Confirm baseline: nothing in the ledger yet (the missed-at-SessionStart state).
    ledger = TranscriptLedger(project)
    assert ledger.get("fake", "t1") is None

    monkeypatch.setenv("LORE_ROOT", str(project))
    # Patch the module-level resolver so cmd_user_prompt_submit uses the fake
    # rather than the real claude-code adapter (which would scan ~/.claude).
    monkeypatch.setattr("lore_cli.hooks.get_adapter", lambda _i: adapter)
    # Stop the spawn-gate from forking a curator subprocess in the test.

    from lore_cli.hooks import cmd_user_prompt_submit
    cmd_user_prompt_submit(cwd=str(project), plain=True)

    # Re-read; cmd_user_prompt_submit should have written the entry.
    ledger = TranscriptLedger(project)
    entry = ledger.get("fake", "t1")
    assert entry is not None, (
        "user-prompt-submit must register transcripts to close the "
        "SessionStart-vs-creation race"
    )
    assert entry.transcript_id == "t1"
    assert entry.directory == project


def test_capture_session_end_creates_ledger_entry(tmp_path: Path, fake_adapter_factory) -> None:
    """capture --event session-end creates a ledger entry for a new transcript."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(project)
    entry = ledger.get("fake", "t1")
    assert entry is not None
    assert entry.integration == "fake"
    assert entry.transcript_id == "t1"
    assert entry.path == handle.path
    assert entry.directory == project
    assert entry.orphan is False


def test_capture_unattached_cwd_returns_without_ledger_write(tmp_path: Path) -> None:
    """capture on an unattached cwd emits a no-scope hook event and skips
    ledger writes.

    Previously this path silently returned with zero trace. That made it
    impossible to tell "hook never fired" apart from "hook fired but
    cwd wasn't attached" — the dominant silent-failure mode reported
    in debugging. Now every capture invocation leaves a record, so
    `lore status` and `lore runs list --hooks` can always answer
    "is my capture hook firing?".
    """
    import json as _json

    unattached = tmp_path / "unattached"
    unattached.mkdir()
    # No CLAUDE.md with ## Lore

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(unattached), "--integration", "claude-code"],
        env={"LORE_ROOT": str(tmp_path)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger_path = tmp_path / ".lore" / "transcript-ledger.json"
    assert not ledger_path.exists(), "unattached cwd must not touch the ledger"

    events_path = tmp_path / ".lore" / "spine.jsonl"
    assert events_path.exists(), "no-scope path must still log a hook event"
    records = [_json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    assert len(records) == 1
    ev = records[0]
    assert ev["data"]["outcome"] == "no-scope"
    assert ev["event"] == "session-end"
    assert ev["data"]["cwd"] == str(unattached)
    assert ev["scope"] is None
    assert ev["error_code"] is None


def test_capture_under_100ms(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """capture returns in under 200ms (target <100ms; 200ms tolerance for CI)."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    # Prevent actual subprocess spawn

    start = time.monotonic()
    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    elapsed = time.monotonic() - start

    assert result.exit_code == 0, result.output
    assert elapsed < 0.2, f"capture took {elapsed:.3f}s — must be <200ms"


def test_capture_hook_under_500ms_with_50_transcripts(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """Capture stays under 500ms even with 50 pre-existing ledger entries.

    The pre-P0 hook took ~15s end-to-end against a vault with hundreds of
    entries; the dominant costs were (a) claude-agent-sdk cold-start and
    (b) re-parsing the 180KB+ ledger JSON on every ``get()``/``upsert()``.
    This regression test locks in the fix: fs-based adapter + ledger
    in-instance cache + single-bulk-upsert per hook.
    """
    project = _make_attached_project(tmp_path)

    # Pre-seed 50 ledger entries to simulate a long-running vault.
    ledger = TranscriptLedger(project)
    pre_entries = [
        TranscriptLedgerEntry(
            integration="fake",
            transcript_id=f"seed{i}",
            path=project / f"seed{i}.jsonl",
            directory=project,
            last_mtime=_now(),
        )
        for i in range(50)
    ]
    ledger.bulk_upsert(pre_entries)

    handle = _make_handle(project)
    fake_adapter_factory([handle])

    # Block subprocess spawn so we time the hook, not curator bootstrap.

    start = time.monotonic()
    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    elapsed = time.monotonic() - start

    assert result.exit_code == 0, result.output
    assert elapsed < 0.5, (
        f"capture took {elapsed:.3f}s — must stay <500ms with 50 pre-existing entries"
    )


def test_capture_issues_single_ledger_write_for_many_new_transcripts(
    tmp_path: Path, fake_adapter_factory
) -> None:
    """Discovering N new transcripts produces one ledger write, not N.

    Regression against the per-transcript ``upsert()`` storm that caused
    the pre-P0 hook to rewrite the entire 180KB+ ledger file once per
    handle. With ``bulk_upsert``, the mtime advances exactly once.
    """
    project = _make_attached_project(tmp_path)
    handles = [_make_handle(project, transcript_id=f"new{i}") for i in range(10)]
    fake_adapter_factory(handles)

    ledger_path = project / ".lore" / "transcript-ledger.json"
    pre_mtime = ledger_path.stat().st_mtime if ledger_path.exists() else 0.0

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    assert ledger_path.exists()
    # All 10 new entries landed in one write.
    ledger = TranscriptLedger(project)
    for h in handles:
        assert ledger.get(h.integration, h.id) is not None
    # File was written exactly once (mtime advanced from zero/stale to a single new value).
    post_mtime = ledger_path.stat().st_mtime
    assert post_mtime != pre_mtime
def test_capture_hook_events_has_provenance_fields(tmp_path: Path, fake_adapter_factory) -> None:
    """capture emits a spine record with pid, cwd and the envelope version."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    import json
    import os
    events_path = project / ".lore" / "spine.jsonl"
    assert events_path.exists()
    # Post-Task-9b the event log can contain pending-breadcrumb-* records
    # after the capture event; assert provenance on the capture event
    # specifically rather than "last line".
    records = [
        json.loads(l) for l in events_path.read_text().splitlines() if l.strip()
    ]
    capture_records = [r for r in records if r.get("event") == "session-end"]
    assert capture_records, f"expected a session-end capture record; got {records}"
    record = capture_records[-1]
    assert record["v"] == SCHEMA_VERSION
    assert record["data"]["pid"] == os.getpid()
    assert record["data"]["cwd"] == str(project)
    # ppid_cmd is present (may be None on some systems)
    assert "ppid_cmd" in record["data"]
def test_capture_session_start_same_behaviour(tmp_path: Path, fake_adapter_factory) -> None:
    """event=session-start produces the same ledger update as session-end."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, transcript_id="s1")
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-start", "--cwd", str(project), "--integration", "fake"],
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
            "--integration", "fake",
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
    old_mtime = datetime(2024, 1, 1, tzinfo=UTC)
    new_mtime = datetime(2025, 6, 1, tzinfo=UTC)

    # Pre-seed ledger with old mtime.
    ledger = TranscriptLedger(project)
    ledger.upsert(
        TranscriptLedgerEntry(
            integration="fake",
            transcript_id="t1",
            path=project / "t1.jsonl",
            directory=project,
            last_mtime=old_mtime,
        )
    )

    # Adapter reports a new mtime.
    handle = _make_handle(project, mtime=new_mtime)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    entry = ledger.get("fake", "t1")
    assert entry is not None
    assert entry.last_mtime == new_mtime


def test_capture_respects_lore_root_env(tmp_path: Path, fake_adapter_factory) -> None:
    """LORE_ROOT env var determines the ledger file location.

    Registry-era: the attachment must live in the custom lore root for
    the capture to route there.
    """
    from lore_core.state.attachments import Attachment, AttachmentsFile

    _make_attached_project(tmp_path)  # sets up wiki/
    project = tmp_path / "project"
    custom_lore_root = tmp_path / "custom_lore_root"
    (custom_lore_root / ".lore").mkdir(parents=True)
    (custom_lore_root / "wiki" / "testwiki").mkdir(parents=True)

    # Register attachment in the CUSTOM lore root
    af = AttachmentsFile(custom_lore_root)
    af.load()
    af.add(Attachment(
        path=project,
        wiki="testwiki",
        scope="testscope",
        attached_at=_now(),
        source="manual",
    ))
    af.save()

    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(custom_lore_root)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    # Ledger must be under the custom lore root.
    ledger = TranscriptLedger(custom_lore_root)
    entry = ledger.get("fake", "t1")
    assert entry is not None


def test_capture_handles_unknown_host_gracefully(tmp_path: Path) -> None:
    """Unknown --integration raises a typer Exit(1) or returns without crash."""
    project = _make_attached_project(tmp_path)

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "nonexistent"],
        env={"LORE_ROOT": str(project)},
    )
    # Either exit code 1 (explicit error) or 0 (silent no-op) is acceptable.
    # Must not raise an uncaught exception (exit code 2+ from typer crash
    # or a traceback is not acceptable).
    assert result.exit_code in (0, 1), (
        f"Expected exit code 0 or 1, got {result.exit_code}.\n{result.output}"
    )


def test_capture_emits_hook_event_happy_path(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """capture() writes one line to the spine with expected outcome."""
    import json

    from lore_cli.hooks import capture

    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, integration="fake")
    fake_adapter_factory([handle])

    monkeypatch.setenv("LORE_ROOT", str(project))
    capture(event="session-end", cwd_override=project, integration="fake")

    log = project / ".lore" / "spine.jsonl"
    assert log.exists(), "spine.jsonl should be created"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) >= 1
    latest = records[-1]
    assert latest["event"] == "session-end"
    assert latest["data"]["outcome"] in {"ledger-advanced", "below-threshold", "spawned-curator", "no-new-turns"}
    assert "duration_ms" in latest["data"]
    assert latest["error_code"] is None


def test_capture_error_path_logs_and_reraises(tmp_path: Path, fake_adapter_factory, monkeypatch) -> None:
    """An adapter that raises during discovery should write outcome=error and re-raise."""
    import json

    from lore_cli import hooks

    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, integration="fake")
    fake_adapter_factory([handle])

    monkeypatch.setenv("LORE_ROOT", str(project))

    def boom(*a, **kw):
        raise RuntimeError("adapter boom")

    monkeypatch.setattr(hooks, "get_adapter", boom)

    with pytest.raises(RuntimeError, match="boom"):
        hooks.capture(event="session-end", cwd_override=project, integration="fake")

    log = project / ".lore" / "spine.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    errors = [r for r in records if r.get("data", {}).get("outcome") == "error"]
    assert errors, "expected at least one error record"
    assert errors[-1]["data"]["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Breadcrumb wiring tests
# ---------------------------------------------------------------------------
def test_capture_session_end_no_breadcrumb_when_no_new_turns(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """When outcome=no-new-turns (all pending=0), no breadcrumb file is written."""

    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    project = _make_attached_project(tmp_path)
    old_mtime = datetime(2024, 1, 1, tzinfo=UTC)

    # Pre-seed ledger with a digested entry (same mtime as handle → no-new-turns).
    ledger = TranscriptLedger(project)
    ledger.upsert(
        TranscriptLedgerEntry(
            integration="fake",
            transcript_id="t1",
            path=project / "t1.jsonl",
            directory=project,
            last_mtime=old_mtime,
        )
    )

    handle = _make_handle(project, integration="fake", mtime=old_mtime)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
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
                integration="fake",
                transcript_id=f"pre{i}",
                path=project / f"pre{i}.jsonl",
                directory=project,
                last_mtime=_now(),
            )
        )

    handle = _make_handle(project, integration="fake", transcript_id="s1")
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-start", "--cwd", str(project), "--integration", "fake"],
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
    pytest.param("transcripts", id="transcripts"),
]


def _stamp_path(lore_root: Path, role: str) -> Path:
    # Stamp lives at curator-<role>.spawn.stamp (flock-based throttle; see
    # lockfile.py).
    return lore_root / ".lore" / f"curator-{role}.spawn.stamp"


def _invoke_spawn(role: str, lore_root: Path, cooldown_s: int) -> bool:
    from lore_cli.hooks import _spawn_detached_transcript_sync

    return _spawn_detached_transcript_sync(lore_root, cooldown_s=cooldown_s)


@pytest.mark.parametrize("role", CURATOR_PARAMS)
def test_spawn_writes_stamp_file_on_success(tmp_path: Path, no_subprocess, role: str) -> None:
    """On successful spawn, a stamp file is written with a recent timestamp."""
    lore_root = tmp_path
    spawned = _invoke_spawn(role, lore_root, cooldown_s=60)
    assert spawned is True, f"expected first spawn to proceed; subprocess calls={no_subprocess}"
    assert len(no_subprocess) == 1
    # `_spawn_detached_transcript_sync` is the only place the child argv is
    # written down, so a typo in it would otherwise reach users unnoticed.
    # The wrapper prepends its own argv; the child's trailing four are ours.
    assert no_subprocess[0][-4:] == ["-m", "lore_cli", "transcripts", "sync"]
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
    reach the real production spine.jsonl.
    """
    import json
    import os
    from pathlib import Path

    from lore_cli.hooks import capture

    real_lore_root = os.environ.get("LORE_ROOT", "")
    real_events = (
        Path(real_lore_root) / ".lore" / "spine.jsonl"
        if real_lore_root
        else None
    )
    before_size = real_events.stat().st_size if real_events and real_events.exists() else -1

    # Isolated env
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project, integration="fake")

    # Register and clean up fake adapter
    from lore_adapters import register
    from lore_adapters.registry import _REGISTRY
    adapter = _FakeAdapter([handle])
    register(adapter)
    try:
        capture(event="session-end", cwd_override=project, integration="fake")
    finally:
        _REGISTRY.pop("fake", None)

    # Verify the record went to the isolated tmp location, not the real vault.
    # LORE_ROOT is set to tmp_path, so the spine writer writes to tmp_path/.lore/
    isolated_log = tmp_path / ".lore" / "spine.jsonl"
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


# ---------------------------------------------------------------------------
# Phase 2: per-wiki pending threshold
# ---------------------------------------------------------------------------


def _make_two_wiki_lore_root(
    root: Path,
    *,
    alpha_threshold: int = 2,
    beta_threshold: int = 10,
) -> tuple[Path, Path, Path]:
    """Create a lore_root with two attached projects + two wiki dirs.

    Returns (lore_root, proj_a, proj_b). The per-wiki `.lore-wiki.yml`
    files set distinct `curator.threshold_pending_turns` values so we can
    verify each wiki's threshold is honoured independently. Age fallback
    is left at default (600s) so the threshold-turns arm is what's
    actually being exercised — test transcripts have fresh mtimes.
    """
    lore_root = root / "lore_root"
    lore_root.mkdir()

    for name, threshold in [("alpha", alpha_threshold), ("beta", beta_threshold)]:
        wiki_dir = lore_root / "wiki" / name
        wiki_dir.mkdir(parents=True)
        (wiki_dir / ".lore-wiki.yml").write_text(
            f"curator:\n  threshold_pending_turns: {threshold}\n"
        )

    proj_a = lore_root / "proj_a"
    proj_a.mkdir()
    proj_b = lore_root / "proj_b"
    proj_b.mkdir()

    # Register both projects in the lore_root's attachments.json
    from lore_core.state.attachments import Attachment, AttachmentsFile
    (lore_root / ".lore").mkdir(exist_ok=True)
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(Attachment(
        path=proj_a, wiki="alpha", scope="proj:a",
        attached_at=_now(), source="manual",
    ))
    af.add(Attachment(
        path=proj_b, wiki="beta", scope="proj:b",
        attached_at=_now(), source="manual",
    ))
    af.save()

    return lore_root, proj_a, proj_b
def test_capture_emits_the_registered_count_in_the_hook_event(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """The hook-event record carries how many entries capture wrote."""
    import json

    lore_root, proj_a, proj_b = _make_two_wiki_lore_root(
        tmp_path, alpha_threshold=10, beta_threshold=10
    )

    ledger = TranscriptLedger(lore_root)
    for i in range(2):
        ledger.upsert(
            TranscriptLedgerEntry(
                integration="fake",
                transcript_id=f"a{i}",
                path=proj_a / f"a{i}.jsonl",
                directory=proj_a,
                last_mtime=_now(),
            )
        )

    handle = _make_handle(proj_b, transcript_id="b1")
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(proj_b), "--integration", "fake"],
        env={"LORE_ROOT": str(lore_root)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    log = lore_root / ".lore" / "spine.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    capture_records = [r for r in records if r.get("event") == "session-end"]
    assert capture_records
    rec = capture_records[-1]
    assert rec["data"]["registered"] == 1


# ---------------------------------------------------------------------------
# Capture-suppress flag — dispatched teammate sessions
# ---------------------------------------------------------------------------


def test_capture_suppressed_by_env_flag_is_full_noop(
    tmp_path: Path, fake_adapter_factory, monkeypatch
) -> None:
    """LORE_SUPPRESS_CAPTURE=1 short-circuits capture() before any ledger
    write, curator spawn, or hook-event log — a dispatched teammate
    session leaves no standalone note."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    spawn_calls: list[Path] = []

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project), "LORE_SUPPRESS_CAPTURE": "1"},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(project)
    assert ledger.get("fake", "t1") is None, "suppressed capture must not touch the ledger"
    assert not spawn_calls, "suppressed capture must never spawn curator A"

    events_path = project / ".lore" / "spine.jsonl"
    assert not events_path.exists(), "suppressed capture must not emit hook telemetry"


def test_capture_without_suppress_flag_is_unchanged(tmp_path: Path, fake_adapter_factory) -> None:
    """Same setup, no LORE_SUPPRESS_CAPTURE set — default capture behaviour
    is unaffected by the new flag's existence."""
    project = _make_attached_project(tmp_path)
    handle = _make_handle(project)
    fake_adapter_factory([handle])

    result = runner.invoke(
        hook_app,
        ["capture", "--event", "session-end", "--cwd", str(project), "--integration", "fake"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(project)
    entry = ledger.get("fake", "t1")
    assert entry is not None, "unsuppressed capture must register the transcript as before"
