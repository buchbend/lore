"""Tests for ``lore_core.verdicts_sidecar`` — slice 6 of PRD #65."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from lore_core.verdicts_sidecar import (
    _sidecar_path,
    clear_confirmed,
    get_confirmed,
    set_confirmed,
)


def test_get_confirmed_missing_returns_none(tmp_path):
    assert get_confirmed(tmp_path, "alice", "concepts/n.md") is None


def test_set_then_get(tmp_path):
    when = date(2026, 5, 8)
    set_confirmed(tmp_path, "alice", "concepts/n.md", when)
    assert get_confirmed(tmp_path, "alice", "concepts/n.md") == when


def test_set_default_today(tmp_path):
    written = set_confirmed(tmp_path, "alice", "n.md")
    assert written == date.today()


def test_round_trip_persisted_to_disk(tmp_path):
    set_confirmed(tmp_path, "alice", "concepts/n.md", date(2026, 5, 8))
    p = _sidecar_path(tmp_path, "alice")
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["confirmed"]["concepts/n.md"] == "2026-05-08"


def test_clear_confirmed_removes_entry(tmp_path):
    set_confirmed(tmp_path, "alice", "concepts/n.md", date(2026, 5, 8))
    assert clear_confirmed(tmp_path, "alice", "concepts/n.md") is True
    assert get_confirmed(tmp_path, "alice", "concepts/n.md") is None


def test_clear_confirmed_missing_returns_false(tmp_path):
    assert clear_confirmed(tmp_path, "alice", "concepts/missing.md") is False


def test_handle_isolation(tmp_path):
    set_confirmed(tmp_path, "alice", "n.md", date(2026, 5, 8))
    set_confirmed(tmp_path, "bob", "n.md", date(2026, 5, 1))
    assert get_confirmed(tmp_path, "alice", "n.md") == date(2026, 5, 8)
    assert get_confirmed(tmp_path, "bob", "n.md") == date(2026, 5, 1)
    # Bob's sidecar untouched by Alice's writes.
    bob_path = _sidecar_path(tmp_path, "bob")
    raw = json.loads(bob_path.read_text())
    assert "confirmed" in raw
    assert "n.md" in raw["confirmed"]


def test_invalid_handle_rejected(tmp_path):
    with pytest.raises(ValueError):
        set_confirmed(tmp_path, "../etc/passwd", "n.md")
    with pytest.raises(ValueError):
        get_confirmed(tmp_path, "", "n.md")
    with pytest.raises(ValueError):
        set_confirmed(tmp_path, "a/b", "n.md")


def test_atomic_write_no_partial_file(tmp_path, monkeypatch):
    """Simulate an interrupted write: replace fails between tmp + commit.

    A partial-file leaves either the prior file intact or no file at
    all — never half-written JSON the next read would choke on.
    """
    set_confirmed(tmp_path, "alice", "n.md", date(2026, 5, 1))
    original = json.loads(_sidecar_path(tmp_path, "alice").read_text())

    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated crash mid-replace")

    with patch("lore_core.verdicts_sidecar.os.replace", side_effect=boom):
        with pytest.raises(OSError):
            set_confirmed(tmp_path, "alice", "n.md", date(2026, 5, 8))

    # Old contents survive verbatim — the replace was the failing step
    # so the prior file was never touched.
    after = json.loads(_sidecar_path(tmp_path, "alice").read_text())
    assert after == original


def test_malformed_sidecar_treated_as_empty(tmp_path):
    p = _sidecar_path(tmp_path, "alice")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{")
    assert get_confirmed(tmp_path, "alice", "n.md") is None
    # Setting after a malformed file recovers cleanly.
    set_confirmed(tmp_path, "alice", "n.md", date(2026, 5, 8))
    assert get_confirmed(tmp_path, "alice", "n.md") == date(2026, 5, 8)


def test_unparseable_date_treated_as_none(tmp_path):
    p = _sidecar_path(tmp_path, "alice")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"confirmed": {"n.md": "not-a-date"}}))
    assert get_confirmed(tmp_path, "alice", "n.md") is None
