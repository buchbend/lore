"""Tests for wikilink/slug resolution in lore_read MCP tool."""

import json
from pathlib import Path

from lore_mcp.server import handle_read


def _setup_wiki(tmp_path: Path, monkeypatch) -> Path:
    wiki = tmp_path / "wiki" / "private"
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026-04-22-my-session.md").write_text(
        "---\ntype: session\ndescription: Test session\n---\nBody content\n"
    )
    concepts = wiki / "concepts"
    concepts.mkdir()
    (concepts / "some-concept.md").write_text(
        "---\ntype: concept\n---\nConcept body\n"
    )
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return wiki


def _setup_catalog(wiki: Path) -> None:
    catalog = {
        "sections": {
            "sessions": [
                {"name": "2026-04-22-my-session", "path": "sessions/2026-04-22-my-session.md"},
            ],
            "concepts": [
                {"name": "some-concept", "path": "concepts/some-concept.md"},
            ],
        }
    }
    (wiki / "_catalog.json").write_text(json.dumps(catalog))


def test_normal_path_still_works(tmp_path: Path, monkeypatch) -> None:
    _setup_wiki(tmp_path, monkeypatch)
    result = handle_read("sessions/2026-04-22-my-session.md", wiki="private")
    assert "error" not in result
    assert "Body content" in result["content"]


def test_wikilink_resolves_via_catalog(tmp_path: Path, monkeypatch) -> None:
    wiki = _setup_wiki(tmp_path, monkeypatch)
    _setup_catalog(wiki)
    result = handle_read("[[2026-04-22-my-session]]", wiki="private")
    assert "error" not in result
    assert "Body content" in result["content"]


def test_bare_slug_resolves(tmp_path: Path, monkeypatch) -> None:
    wiki = _setup_wiki(tmp_path, monkeypatch)
    _setup_catalog(wiki)
    result = handle_read("some-concept", wiki="private")
    assert "error" not in result
    assert "Concept body" in result["content"]


def test_slug_resolves_via_rglob_fallback(tmp_path: Path, monkeypatch) -> None:
    """Without catalog, falls back to rglob."""
    _setup_wiki(tmp_path, monkeypatch)
    result = handle_read("[[some-concept]]", wiki="private")
    assert "error" not in result
    assert "Concept body" in result["content"]


def test_unknown_slug_returns_error(tmp_path: Path, monkeypatch) -> None:
    wiki = _setup_wiki(tmp_path, monkeypatch)
    _setup_catalog(wiki)
    result = handle_read("[[nonexistent-note]]", wiki="private")
    assert "error" in result
    assert "not found" in result["error"]


def test_team_mode_sharded_resolves(tmp_path: Path, monkeypatch) -> None:
    """Notes in sessions/<user>/ subdir resolve via rglob."""
    wiki = tmp_path / "wiki" / "private"
    sharded = wiki / "sessions" / "alice"
    sharded.mkdir(parents=True)
    (sharded / "2026-04-22-sharded-note.md").write_text(
        "---\ntype: session\n---\nSharded body\n"
    )
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = handle_read("[[2026-04-22-sharded-note]]", wiki="private")
    assert "error" not in result
    assert "Sharded body" in result["content"]
