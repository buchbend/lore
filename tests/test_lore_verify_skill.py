"""Smoke test for the `/lore:verify` plugin skill — slice 10 of PRD #65."""

from __future__ import annotations

from pathlib import Path

import pytest

import yaml


SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "verify" / "SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return yaml.safe_load(text[4:end]) or {}


def test_skill_file_exists():
    assert SKILL_PATH.is_file(), f"missing {SKILL_PATH}"


def test_skill_name_is_lore_colon_verify():
    """Plugin namespace caveat: SKILL.md `name:` is used verbatim
    per the existing memory note — must include the `lore:` prefix."""
    fm = _frontmatter(SKILL_PATH)
    assert fm.get("name") == "lore:verify"


def test_skill_user_invocable_flag_set():
    fm = _frontmatter(SKILL_PATH)
    assert fm.get("user_invocable") is True


def test_skill_description_present_and_short():
    fm = _frontmatter(SKILL_PATH)
    desc = fm.get("description") or ""
    # Must mention what the command does to be discoverable.
    assert "verdict" in desc.lower() or "stale" in desc.lower()
    # Run-with hint required for plugin discoverability.
    assert "/lore:verify" in desc


def test_skill_body_documents_three_picker_options():
    """confirm / stale / skip must all be referenced in the body."""
    text = SKILL_PATH.read_text().lower()
    assert "confirm" in text
    assert "stale" in text
    assert "skip" in text


def test_skill_calls_lore_verdict_mcp():
    """No new write paths — must wrap the existing MCP tool."""
    text = SKILL_PATH.read_text()
    assert "lore_verdict" in text


def test_plugin_version_bumped_for_skill_addition():
    """Per the lore-plugin-cache-stale memory: any plugin.json change
    needs a version bump or installed caches never re-fetch.

    Cross-checked against pyproject.toml's version rather than a
    hardcoded literal — the release process bumps both in lockstep
    (see test_version_sync.py), so a hardcoded string here just rots
    on every release.
    """
    import json
    import tomllib

    repo_root = SKILL_PATH.parent.parent.parent
    plugin = json.loads((repo_root / ".claude-plugin" / "plugin.json").read_text())
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    assert plugin["version"] == pyproject["project"]["version"]
