"""Tests for the ``_scope_renames.txt`` append-only log (Phase 8)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lore_core.state.scope_renames import (
    RenameEvent,
    append_rename,
    log_path,
    read_log,
)


def test_log_path_under_lore_root(tmp_path):
    assert log_path(tmp_path) == tmp_path / "_scope_renames.txt"


def test_append_creates_file(tmp_path):
    path = append_rename(
        tmp_path, "ccat:data-center", "ccat:dc",
        timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        host="myhost",
    )
    assert path.exists()
    text = path.read_text()
    assert "ccat:data-center" in text
    assert "ccat:dc" in text
    assert "myhost" in text
    # Tab-separated on a single line + trailing newline.
    line = text.splitlines()[0]
    parts = line.split("\t")
    assert len(parts) == 4


def test_append_is_additive(tmp_path):
    append_rename(tmp_path, "a", "b", host="h1")
    append_rename(tmp_path, "c", "d", host="h2")
    events = read_log(tmp_path)
    assert len(events) == 2
    assert events[0].old_scope == "a"
    assert events[0].new_scope == "b"
    assert events[1].old_scope == "c"
    assert events[1].new_scope == "d"


def test_read_log_empty_when_file_missing(tmp_path):
    assert read_log(tmp_path) == []


def test_read_log_skips_malformed_lines(tmp_path):
    log = log_path(tmp_path)
    log.write_text(
        "2026-05-01T12:00:00Z\told\tnew\thost1\n"
        "garbage line without tabs\n"
        "\n"  # blank
        "2026-05-02T12:00:00Z\told2\tnew2\thost2\n"
    )
    events = read_log(tmp_path)
    assert len(events) == 2
    assert events[0].old_scope == "old"
    assert events[1].old_scope == "old2"


def test_append_failure_is_swallowed(tmp_path):
    """If the log can't be written, the call returns the path without raising."""
    target = tmp_path / "notadir"
    target.mkdir()
    target.chmod(0o400)  # read-only directory
    try:
        # Should not raise even when disk write fails.
        path = append_rename(target, "a", "b")
        assert path == target / "_scope_renames.txt"
    finally:
        target.chmod(0o755)
