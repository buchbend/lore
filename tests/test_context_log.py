"""/lore:context — pure cache read of the session context log.

The context log is PID-scoped: SessionStart overwrites it with a
timestamp header. /lore:context reads the file verbatim — no live I/O.
"""

from __future__ import annotations

from pathlib import Path

from lore_cli.hooks import _context_log


def _seed_cache(tmp_path: Path, pid: int, body: str) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True)
    cache = sessions / f"{pid}.md"
    cache.write_text(body)
    return cache


def _set_env(monkeypatch, cache_dir: Path) -> None:
    monkeypatch.setenv("LORE_CACHE", str(cache_dir))


def test_context_log_returns_cached_body(tmp_path: Path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    _seed_cache(cache_dir, pid=99999, body="── SessionStart 14:32 ──\nlore: active\n")
    _set_env(monkeypatch, cache_dir)
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: 99999)

    out = _context_log()
    assert "SessionStart 14:32" in out
    assert "lore: active" in out


def test_context_log_handles_missing_cache(tmp_path: Path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _set_env(monkeypatch, cache_dir)
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: 99999)

    out = _context_log()
    assert "no context log" in out.lower()


def test_context_log_falls_back_to_legacy(tmp_path: Path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "last-session-start.md").write_text("legacy body\n")
    _set_env(monkeypatch, cache_dir)
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: None)

    out = _context_log()
    assert "legacy" in out.lower()
    assert "legacy body" in out
