"""Tests for `lore news` — session + background drain events.

`lore news` is a deprecated thin alias (#195): `lore status` absorbs its
role as the health dashboard's "news" section. The command keeps working
during the deprecation window — it prints a pointer to stderr, then
delegates to its original behavior.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.drain import DrainStore
from typer.testing import CliRunner

runner = CliRunner()


def _lore_root(tmp_path: Path, monkeypatch) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    return lore_root


def test_news_empty(tmp_path: Path, monkeypatch) -> None:
    from lore_cli.news_cmd import app

    _lore_root(tmp_path, monkeypatch)
    result = runner.invoke(app, ["--session", "s1"])
    assert result.exit_code == 0
    assert "no news" in result.stdout.lower()


def test_news_shows_session_drain_events(tmp_path: Path, monkeypatch) -> None:
    from lore_cli.news_cmd import app

    lore_root = _lore_root(tmp_path, monkeypatch)
    DrainStore(lore_root, "s1").emit("note-filed", wiki="private", wikilink="[[test]]")
    result = runner.invoke(app, ["--session", "s1"])
    assert result.exit_code == 0
    assert "new note" in result.stdout


# ---------------------------------------------------------------------------
# Deprecation — thin alias pointing at `lore status` (#195)
# ---------------------------------------------------------------------------


def test_news_deprecation_pointer_and_delegation(tmp_path: Path, monkeypatch) -> None:
    """`lore news` prints a pointer to `lore status` on stderr, then still
    shows session news — the deprecation window keeps it functional."""
    from lore_cli.news_cmd import app

    lore_root = _lore_root(tmp_path, monkeypatch)
    DrainStore(lore_root, "s1").emit("note-filed", wiki="private", wikilink="[[test]]")
    result = runner.invoke(app, ["--session", "s1"])
    assert "deprecated" in result.stderr
    assert "lore status" in result.stderr
    assert "new note" in result.stdout  # delegation: old behavior intact


def test_news_latest_subcommand_also_prints_pointer(tmp_path: Path, monkeypatch) -> None:
    """The pointer fires for the `latest` subcommand too, not just the default."""
    from lore_cli.news_cmd import app

    _lore_root(tmp_path, monkeypatch)
    result = runner.invoke(app, ["latest"])
    assert "deprecated" in result.stderr
