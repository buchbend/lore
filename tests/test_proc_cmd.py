"""Tests for `lore proc` CLI commands."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    return tmp_path


@pytest.fixture()
def proc_dir(lore_root: Path) -> Path:
    d = lore_root / ".lore" / "proc"
    d.mkdir()
    return d


def _get_app():
    from lore_cli.proc_cmd import app
    return app


def test_list_no_logs(lore_root: Path) -> None:
    result = runner.invoke(_get_app(), ["list"])
    assert result.exit_code == 0
    assert "No subprocess logs" in result.output


def test_list_with_logs(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("some output\n")
    (proc_dir / "b.log").write_text("")

    result = runner.invoke(_get_app(), ["list"])
    assert result.exit_code == 0
    assert "a" in result.output
    assert "b" in result.output


def test_list_detects_errors(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text(
        "Traceback (most recent call last):\n"
        "  File \"test.py\", line 1\n"
        "ImportError: no module named foo\n"
    )
    result = runner.invoke(_get_app(), ["list"])
    assert result.exit_code == 0
    assert "errors" in result.output


def test_list_empty_is_not_error(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("")
    result = runner.invoke(_get_app(), ["list"])
    assert result.exit_code == 0
    assert "errors" not in result.output


def test_show_prints_content(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("hello world\nline 2\n")
    result = runner.invoke(_get_app(), ["show", "a"])
    assert result.exit_code == 0
    assert "hello world" in result.output
    assert "line 2" in result.output


def test_show_prev(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("current")
    (proc_dir / "a.log.1").write_text("previous run")
    result = runner.invoke(_get_app(), ["show", "a", "--prev"])
    assert result.exit_code == 0
    assert "previous run" in result.output


def test_show_empty_log(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("")
    result = runner.invoke(_get_app(), ["show", "a"])
    assert result.exit_code == 0
    assert "empty" in result.output


def test_show_missing_log(lore_root: Path) -> None:
    result = runner.invoke(_get_app(), ["show", "a"])
    assert result.exit_code == 0
    assert "No" in result.output


def test_show_unknown_role(lore_root: Path) -> None:
    result = runner.invoke(_get_app(), ["show", "z"])
    assert result.exit_code == 1
    assert "Unknown role" in result.output


def test_show_lines_limit(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("\n".join(f"line {i}" for i in range(20)))
    result = runner.invoke(_get_app(), ["show", "a", "--lines", "3"])
    assert result.exit_code == 0
    assert "line 17" in result.output
    assert "line 19" in result.output
    assert "line 0" not in result.output


# ---------------------------------------------------------------------------
# Phase 2d: metadata sidecar + --gen flag
# ---------------------------------------------------------------------------


def test_list_shows_exit_code_and_duration(proc_dir: Path) -> None:
    import json
    (proc_dir / "a.log").write_text("some output\n")
    (proc_dir / "a.meta.json").write_text(json.dumps({
        "pid": 12345, "start_ts": 1000.0, "end_ts": 1042.5,
        "exit_code": 0, "cmd": ["lore", "curator", "run"],
    }))
    result = runner.invoke(_get_app(), ["list"])
    assert result.exit_code == 0
    assert "0" in result.output  # exit code
    assert "42" in result.output  # ~42s duration


def test_show_prints_metadata_header(proc_dir: Path) -> None:
    import json
    (proc_dir / "a.log").write_text("curator output\n")
    (proc_dir / "a.meta.json").write_text(json.dumps({
        "pid": 99, "start_ts": 1000.0, "end_ts": 1010.0,
        "exit_code": 1, "cmd": ["lore", "curator", "run"],
    }))
    result = runner.invoke(_get_app(), ["show", "a"])
    assert result.exit_code == 0
    assert "pid=99" in result.output
    assert "exit=1" in result.output
    assert "curator output" in result.output


def test_show_gen_flag(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("current")
    (proc_dir / "a.log.1").write_text("gen 1")
    (proc_dir / "a.log.2").write_text("gen 2")
    result = runner.invoke(_get_app(), ["show", "a", "--gen", "2"])
    assert result.exit_code == 0
    assert "gen 2" in result.output


def test_show_prev_is_gen_1(proc_dir: Path) -> None:
    (proc_dir / "a.log").write_text("current")
    (proc_dir / "a.log.1").write_text("gen 1 content")
    result = runner.invoke(_get_app(), ["show", "a", "--prev"])
    assert result.exit_code == 0
    assert "gen 1 content" in result.output
