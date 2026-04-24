"""Tests for subprocess log capture in _spawn_detached and _open_proc_log."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def lore_root(tmp_path: Path) -> Path:
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    return tmp_path


def test_open_proc_log_creates_dir_and_file(lore_root: Path) -> None:
    from lore_cli.hooks import _open_proc_log

    fd = _open_proc_log(lore_root, "a")
    assert fd is not None
    try:
        log = lore_root / ".lore" / "proc" / "a.log"
        assert log.exists()
    finally:
        os.close(fd)


def test_open_proc_log_rotates_previous(lore_root: Path) -> None:
    from lore_cli.hooks import _open_proc_log

    proc_dir = lore_root / ".lore" / "proc"
    proc_dir.mkdir(parents=True)
    current = proc_dir / "a.log"
    current.write_text("first run output")

    fd = _open_proc_log(lore_root, "a")
    assert fd is not None
    os.close(fd)

    prev = proc_dir / "a.log.1"
    assert prev.exists()
    assert prev.read_text() == "first run output"
    assert current.stat().st_size == 0


def test_open_proc_log_returns_none_on_unwritable(tmp_path: Path) -> None:
    from lore_cli.hooks import _open_proc_log

    bad_root = tmp_path / "nonexistent" / "deep"
    with patch("os.open", side_effect=OSError("mocked")):
        fd = _open_proc_log(bad_root, "a")
    assert fd is None


def test_spawn_detached_writes_to_log(lore_root: Path) -> None:
    """Verify that _spawn_detached passes the log fd to Popen."""
    from lore_cli.hooks import _spawn_detached

    captured_kwargs: dict = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured_kwargs.update(kwargs)

    with patch("subprocess.Popen", FakePopen):
        with patch("lore_cli.hooks._stamp_within_cooldown", return_value=False):
            result = _spawn_detached(
                lore_root, "a",
                ["echo", "test"],
                cooldown_s=0,
            )

    assert result is True
    assert captured_kwargs["stdout"] is not None
    assert captured_kwargs["stdout"] != -1  # not DEVNULL (-1 on some platforms)
    assert captured_kwargs["stderr"] is not None
    assert captured_kwargs["stdout"] == captured_kwargs["stderr"]

    log = lore_root / ".lore" / "proc" / "a.log"
    assert log.exists()


def test_spawn_detached_falls_back_to_devnull(lore_root: Path) -> None:
    """If log file can't be opened, should still spawn with DEVNULL."""
    import subprocess
    from lore_cli.hooks import _spawn_detached

    captured_kwargs: dict = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured_kwargs.update(kwargs)

    with patch("subprocess.Popen", FakePopen):
        with patch("lore_cli.hooks._stamp_within_cooldown", return_value=False):
            with patch("lore_cli.hooks._open_proc_log", return_value=None):
                result = _spawn_detached(
                    lore_root, "a",
                    ["echo", "test"],
                    cooldown_s=0,
                )

    assert result is True
    assert captured_kwargs["stdout"] == subprocess.DEVNULL
    assert captured_kwargs["stderr"] == subprocess.DEVNULL


def test_open_proc_log_multi_generation(lore_root: Path) -> None:
    """Multiple rotations should keep up to `keep` generations."""
    from lore_cli.hooks import _open_proc_log

    proc_dir = lore_root / ".lore" / "proc"
    proc_dir.mkdir(parents=True)

    # Write 4 generations of logs
    for i in range(4):
        current = proc_dir / "a.log"
        current.write_text(f"run {i}")
        fd = _open_proc_log(lore_root, "a", keep=3)
        assert fd is not None
        os.close(fd)

    # After 4 rotations with keep=3: .log (empty), .log.1 = "run 3",
    # .log.2 = "run 2", .log.3 = "run 1". "run 0" was evicted.
    assert (proc_dir / "a.log").stat().st_size == 0
    assert (proc_dir / "a.log.1").read_text() == "run 3"
    assert (proc_dir / "a.log.2").read_text() == "run 2"
    assert (proc_dir / "a.log.3").read_text() == "run 1"
    assert not (proc_dir / "a.log.4").exists()


def test_open_proc_log_default_keep_is_3(lore_root: Path) -> None:
    """Default keep parameter should be 3."""
    from lore_cli.hooks import _open_proc_log

    proc_dir = lore_root / ".lore" / "proc"
    proc_dir.mkdir(parents=True)

    for i in range(5):
        (proc_dir / "b.log").write_text(f"run {i}")
        fd = _open_proc_log(lore_root, "b")
        assert fd is not None
        os.close(fd)

    assert (proc_dir / "b.log.3").exists()
    assert not (proc_dir / "b.log.4").exists()
