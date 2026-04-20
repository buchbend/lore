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
    monkeypatch.setattr("lore_cli.hooks._spawn_detached_curator_a", lambda *a, **kw: None)

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

    # Default threshold is 3. Pre-seed ledger with 2 pending entries.
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

    # Adapter returns one more new transcript (total pending will be 3).
    handle = _make_handle(project, transcript_id="new1")
    fake_adapter_factory([handle])

    spawn_calls: list[Path] = []
    monkeypatch.setattr(
        "lore_cli.hooks._spawn_detached_curator_a",
        lambda lore_root: spawn_calls.append(lore_root),
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
        lambda lore_root: spawn_calls.append(lore_root),
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
