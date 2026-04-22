"""Tests for the UserPromptSubmit heartbeat hook."""

from __future__ import annotations

import time
from pathlib import Path

from lore_core.drain import DrainStore, SYSTEM_SESSION
from lore_core.wiki_config import HeartbeatConfig, WikiConfig

from lore_cli.hooks import _heartbeat, _write_stamp


def _make_heartbeat_stamp(lore_root: Path) -> Path:
    stamp = lore_root / ".lore" / "curator-heartbeat.spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    return stamp


def _emit(lore_root: Path, event: str, wiki: str = "private", **data) -> None:
    store = DrainStore(lore_root, SYSTEM_SESSION)
    store.emit(event=event, wiki=wiki, **data)


def _pid_cursor_path(lore_root: Path, pid: int) -> Path:
    return lore_root / ".lore" / "drain" / f"heartbeat-{pid}.cursor"


def test_returns_none_within_cooldown(tmp_path: Path) -> None:
    stamp = _make_heartbeat_stamp(tmp_path)
    _write_stamp(stamp)
    _emit(tmp_path, "note-filed", wikilink="[[test-note]]")

    cfg = WikiConfig()
    sys_msg, ctx = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg is None
    assert ctx is None


def test_returns_summary_when_new_events(tmp_path: Path) -> None:
    _emit(tmp_path, "note-filed", wikilink="[[test-note]]")

    cfg = WikiConfig()
    sys_msg, ctx = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg is not None
    assert "note" in sys_msg.lower() or "filed" in sys_msg.lower()


def test_returns_none_when_no_events(tmp_path: Path) -> None:
    (tmp_path / ".lore" / "drain").mkdir(parents=True, exist_ok=True)
    cfg = WikiConfig()
    sys_msg, ctx = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg is None
    assert ctx is None


def test_cursor_advances_no_double_surface(tmp_path: Path) -> None:
    _emit(tmp_path, "note-filed", wikilink="[[test-note]]")

    cfg = WikiConfig()
    sys_msg1, _ = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg1 is not None

    # Expire the cooldown stamp so heartbeat runs again.
    stamp = _make_heartbeat_stamp(tmp_path)
    stamp.write_text("0")

    sys_msg2, _ = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg2 is None


def test_push_context_true_returns_additional_context(tmp_path: Path) -> None:
    _emit(tmp_path, "note-filed", wikilink="[[new-note]]")

    cfg = WikiConfig()
    cfg.heartbeat.push_context = True
    sys_msg, ctx = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg is not None
    assert ctx is not None
    assert "[[new-note]]" in ctx


def test_push_context_false_no_additional_context(tmp_path: Path) -> None:
    _emit(tmp_path, "note-filed", wikilink="[[new-note]]")

    cfg = WikiConfig()
    cfg.heartbeat.push_context = False
    sys_msg, ctx = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg is not None
    assert ctx is None


def test_heartbeat_disabled(tmp_path: Path) -> None:
    _emit(tmp_path, "note-filed", wikilink="[[test-note]]")

    cfg = WikiConfig()
    cfg.heartbeat.enabled = False
    sys_msg, ctx = _heartbeat(tmp_path, tmp_path, cfg, pid=99999)
    assert sys_msg is None
    assert ctx is None
