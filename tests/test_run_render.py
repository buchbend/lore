import os
from unittest.mock import patch

from lore_cli.run_render import (
    IconSet, pick_icon_set, render_flat_log, render_summary_panel, should_use_color,
)


def test_pick_iconset_default_unicode(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("LORE_ASCII", raising=False)

    class _FakeStdout:
        encoding = "utf-8"
        def isatty(self): return True
    monkeypatch.setattr("sys.stdout", _FakeStdout())
    assert pick_icon_set().kind == "unicode"


def test_pick_iconset_ascii_on_env(monkeypatch):
    monkeypatch.setenv("LORE_ASCII", "1")
    assert pick_icon_set().kind == "ascii"


def test_pick_iconset_ascii_on_non_utf8_encoding(monkeypatch):
    class _FakeStdout:
        encoding = "ascii"
        def isatty(self): return True
    monkeypatch.setattr("sys.stdout", _FakeStdout())
    monkeypatch.delenv("LORE_ASCII", raising=False)
    assert pick_icon_set().kind == "ascii"


def test_should_use_color_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert should_use_color() is False


def test_render_flat_log_basic_unicode():
    records = [
        {"type": "run-start", "ts": "2026-04-20T14:32:05Z", "trigger": "hook"},
        {"type": "transcript-start", "ts": "2026-04-20T14:32:06Z",
         "transcript_id": "t1", "new_turns": 10, "hash_before": "abc1"},
        {"type": "noteworthy", "ts": "2026-04-20T14:32:07Z",
         "transcript_id": "t1", "verdict": True, "reason": "important",
         "tier": "middle", "latency_ms": 842},
        {"type": "run-end", "ts": "2026-04-20T14:32:08Z",
         "duration_ms": 3000, "notes_new": 1, "notes_merged": 0,
         "skipped": 0, "errors": 0},
    ]
    out = render_flat_log(records, icons=IconSet.unicode(), use_color=False)
    assert "▶" in out
    assert "↑" in out
    assert "═" in out
    assert "important" in out


def test_render_flat_log_ascii_fallback():
    records = [
        {"type": "transcript-start", "ts": "2026-04-20T14:32:06Z",
         "transcript_id": "t1", "new_turns": 10, "hash_before": "abc1"},
        {"type": "noteworthy", "ts": "2026-04-20T14:32:07Z",
         "transcript_id": "t1", "verdict": False, "reason": "brief",
         "tier": "middle", "latency_ms": 300},
    ]
    out = render_flat_log(records, icons=IconSet.ascii(), use_color=False)
    assert ">" in out
    assert "x" in out
    assert "▶" not in out
    assert "⊘" not in out


def test_summary_panel_collapses_wikilinks_on_narrow_terminal():
    records = [
        {"type": "run-start", "ts": "2026-04-20T14:32:05Z", "trigger": "hook"},
        {"type": "session-note", "ts": "2026-04-20T14:32:07Z",
         "action": "filed", "wikilink": "[[2026-04-20-very-long-descriptive-note-slug]]"},
        {"type": "run-end", "ts": "2026-04-20T14:32:08Z",
         "duration_ms": 3000, "notes_new": 1, "notes_merged": 0,
         "skipped": 0, "errors": 0},
    ]
    narrow = render_summary_panel(records, term_width=40)
    wide = render_summary_panel(records, term_width=120)
    assert any("..." in line for line in narrow)
    assert any("very-long-descriptive-note-slug" in line for line in wide)
