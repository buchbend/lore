"""Tests for ``canonical_text`` and ``read_hook_stdin`` in lore_core.io.

These two helpers are tiny but load-bearing — ``canonical_text`` is what
keeps the plan-capture hook's source_hash stable across editor round
trips, and ``read_hook_stdin`` is the canonical pattern for any future
stdin-reading hook (SubagentStop, etc.) so the four-case handling lives
in one place.
"""
from __future__ import annotations

import io
import sys

import pytest

from lore_core.io import (
    DEFAULT_HOOK_STDIN_MAX_BYTES,
    canonical_text,
    read_hook_stdin,
)


# ---------------------------------------------------------------------------
# canonical_text
# ---------------------------------------------------------------------------


def test_canonical_text_idempotent() -> None:
    """Applying twice equals applying once — the property the hash relies on."""
    raw = "line one\r\nline two   \r\n\r\n\r\n"
    once = canonical_text(raw)
    assert canonical_text(once) == once


def test_canonical_text_trailing_newline_stable() -> None:
    """Editor adds/removes a trailing newline — hash must not change.

    This is the regression case from the merciless review: Obsidian
    appends a newline on save and naive byte-equality would trip the
    "different content → date-suffix new file" path on every round
    trip.
    """
    a = "Hello\n"
    b = "Hello"
    c = "Hello\n\n\n"
    canon = canonical_text(a)
    assert canonical_text(b) == canon
    assert canonical_text(c) == canon


def test_canonical_text_normalizes_crlf() -> None:
    assert canonical_text("a\r\nb\r\n") == "a\nb\n"


def test_canonical_text_normalizes_lone_cr() -> None:
    assert canonical_text("a\rb\rc") == "a\nb\nc\n"


def test_canonical_text_strips_trailing_whitespace_per_line() -> None:
    assert canonical_text("foo   \nbar\t\nbaz") == "foo\nbar\nbaz\n"


def test_canonical_text_accepts_bytes() -> None:
    assert canonical_text(b"hello\r\nworld") == "hello\nworld\n"


def test_canonical_text_replaces_invalid_utf8() -> None:
    """Untrusted plan text may contain garbage bytes — never raise."""
    raw = b"good\xffbad\n"
    out = canonical_text(raw)
    assert out.endswith("\n")
    assert "good" in out and "bad" in out


def test_canonical_text_empty_returns_single_newline() -> None:
    """Empty input collapses to one newline (not zero) — keeps the hash defined."""
    assert canonical_text("") == "\n"


# ---------------------------------------------------------------------------
# read_hook_stdin
# ---------------------------------------------------------------------------


def _set_stdin(monkeypatch: pytest.MonkeyPatch, payload: bytes, *, isatty: bool = False) -> None:
    """Install a fake sys.stdin whose .buffer.read returns ``payload``."""

    class _FakeBuffer:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                out, self._data = self._data, b""
                return out
            out, self._data = self._data[:n], self._data[n:]
            return out

    class _FakeStdin:
        def __init__(self, data: bytes, tty: bool) -> None:
            self.buffer = _FakeBuffer(data)
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload, isatty))


def test_read_hook_stdin_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_stdin(monkeypatch, b'{"tool_input": {"plan": "x"}}')
    result = read_hook_stdin()
    assert result.outcome == "ok"
    assert result.data == b'{"tool_input": {"plan": "x"}}'
    assert bool(result) is True


def test_read_hook_stdin_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Human ran the hook by hand at a terminal — never block forever."""
    _set_stdin(monkeypatch, b"", isatty=True)
    result = read_hook_stdin()
    assert result.outcome == "tty"
    assert result.data is None
    assert bool(result) is False


def test_read_hook_stdin_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code under race may emit a PostToolUse with empty stdin."""
    _set_stdin(monkeypatch, b"")
    result = read_hook_stdin()
    assert result.outcome == "empty-payload"
    assert result.data is None


def test_read_hook_stdin_oversized_returns_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized payloads are truncated to ``max_bytes`` so orphan-dump still preserves what we got."""
    payload = b"x" * (DEFAULT_HOOK_STDIN_MAX_BYTES + 100)
    _set_stdin(monkeypatch, payload)
    result = read_hook_stdin()
    assert result.outcome == "oversized"
    assert result.data is not None
    assert len(result.data) == DEFAULT_HOOK_STDIN_MAX_BYTES


def test_read_hook_stdin_missing_isatty_treated_as_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detached-subprocess case: no stdin at all → behave as if interactive."""

    class _NoStdin:
        # No .isatty() and no .buffer attribute — getattr will raise.
        pass

    monkeypatch.setattr(sys, "stdin", _NoStdin())
    result = read_hook_stdin()
    assert result.outcome == "tty"
    assert result.data is None


def test_read_hook_stdin_custom_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is configurable — small caps detect oversized correctly."""
    _set_stdin(monkeypatch, b"x" * 50)
    result = read_hook_stdin(max_bytes=10)
    assert result.outcome == "oversized"
    assert result.data is not None
    assert len(result.data) == 10


def test_read_hook_stdin_at_exact_cap_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boundary: exactly ``max_bytes`` is OK, ``max_bytes + 1`` is oversized."""
    _set_stdin(monkeypatch, b"x" * 100)
    result = read_hook_stdin(max_bytes=100)
    assert result.outcome == "ok"
    assert result.data is not None
    assert len(result.data) == 100


def test_hook_stdin_result_truthiness() -> None:
    """``bool(result)`` is True iff there are bytes — convenient for ``if result:`` guards."""
    from lore_core.io import HookStdinResult

    assert bool(HookStdinResult(b"x", "ok")) is True
    assert bool(HookStdinResult(b"", "empty-payload")) is False
    assert bool(HookStdinResult(None, "tty")) is False
