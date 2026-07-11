"""Tests for the CLI-layer janitor composer (issue #190).

``lore_core.janitor`` doesn't know about crash logs (they live under the
global ``$LORE_CACHE``, not a specific ``lore_root``) or drain orphan
pruning (owned by ``lore_cli.drain_cmd``) — this composer wires the core
sweep together with those two lore_cli-only families for the opportunistic
entry points (hook fire, curator run end).
"""

from __future__ import annotations

import json
from pathlib import Path

from lore_cli._janitor_entry import run_opportunistic_janitor
from lore_core.spine import SpineWriter


def test_composer_runs_core_sweep_and_crash_purge(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr("lore_cli._crash_log._is_dev_invocation", lambda: False)
    SpineWriter(tmp_path).emit(source="hook", event="e")

    from lore_cli._crash_log import write_crash

    try:
        raise RuntimeError("old")
    except RuntimeError as exc:
        old_crash = write_crash("SessionStart", exc)
    import os
    import time

    os.utime(old_crash, (time.time() - 999 * 86400, time.time() - 999 * 86400))

    run_opportunistic_janitor(tmp_path)

    assert not old_crash.exists()
    from lore_core.janitor import read_janitor_status

    assert read_janitor_status(tmp_path) is not None


def test_composer_prunes_drain_orphans(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    target = tmp_path / ".lore" / "drain" / "_system.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    gone = tmp_path / "gone.md"
    target.write_text(json.dumps({"event": "note-filed", "data": {"path": str(gone)}}) + "\n")

    run_opportunistic_janitor(tmp_path)

    lines = [x for x in target.read_text().splitlines() if x.strip()]
    assert lines == []


def test_composer_never_raises(tmp_path: Path, monkeypatch):
    """Best-effort like every other opportunistic hook-path call."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))

    def boom(*_a, **_kw):
        raise RuntimeError("boom")

    import lore_core.janitor as janitor_mod

    monkeypatch.setattr(janitor_mod, "run_janitor", boom)
    run_opportunistic_janitor(tmp_path)  # must not raise
