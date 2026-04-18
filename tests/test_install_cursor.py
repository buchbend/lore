"""Tests for `lore_core.install.cursor` — per-platform path resolution
+ schema-versioning paths (absent → silent migrate; present-but-old →
replace) + managed-marker preservation on uninstall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_core.install import _helpers, cursor
from lore_core.install._helpers import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    SCHEMA_VERSION_KEY,
    execute_action,
)
from lore_core.install.base import InstallContext


@pytest.fixture
def cursor_home(tmp_path, monkeypatch):
    """Fake Linux $HOME with a Cursor config dir at the legacy location."""
    monkeypatch.setattr(_helpers.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(
        _helpers.Path, "home", classmethod(lambda cls: tmp_path)
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "rules").mkdir()
    return tmp_path


def test_plan_fresh_install_emits_merge_new_check(cursor_home):
    actions = cursor.plan(InstallContext())
    kinds = [a.kind for a in actions]
    assert "merge" in kinds
    assert "new" in kinds
    assert "check" in kinds


def test_plan_present_same_schema_emits_only_check(cursor_home):
    """Already at SCHEMA_VERSION → no merge, no new, just check."""
    mcp_path = cursor_home / ".cursor" / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "lore": _helpers.lore_mcp_entry(cursor.SCHEMA_VERSION),
                }
            }
        )
    )
    rules_path = cursor_home / ".cursor" / "rules" / "lore.md"
    body = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "host-rules"
        / "default.md"
    ).read_text().rstrip("\n")
    _helpers.write_managed_markdown(rules_path, body)
    actions = cursor.plan(InstallContext())
    # No merge, no new — only the trailing check
    kinds = [a.kind for a in actions]
    assert "merge" not in kinds
    assert "new" not in kinds
    assert "check" in kinds


def test_plan_absent_schema_silent_migrate_via_merge(cursor_home):
    """User-authored mcpServers.lore (no _lore_schema_version) → merge,
    not replace. The whole point of the absent-vs-present distinction
    flagged by the merciless reviewer."""
    mcp_path = cursor_home / ".cursor" / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "lore": {
                        "command": "lore",
                        "args": ["mcp"],
                        # NO _lore_schema_version
                    }
                }
            }
        )
    )
    actions = cursor.plan(InstallContext())
    merge_actions = [a for a in actions if a.kind == "merge"]
    replace_actions = [a for a in actions if a.kind == "replace"]
    assert len(merge_actions) == 1, "absent-version case must emit merge, not replace"
    assert len(replace_actions) == 0


def test_plan_present_old_schema_emits_replace(cursor_home):
    """Schema present but older → replace with explicit prompt
    semantics (kind=replace), per the schema-bump path."""
    mcp_path = cursor_home / ".cursor" / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "lore": {
                        "command": "lore",
                        "args": ["mcp"],
                        SCHEMA_VERSION_KEY: "0.5",
                    }
                }
            }
        )
    )
    actions = cursor.plan(InstallContext())
    replace_actions = [a for a in actions if a.kind == "replace"]
    assert len(replace_actions) == 1
    assert "0.5" in replace_actions[0].summary
    assert cursor.SCHEMA_VERSION in replace_actions[0].summary


def test_plan_existing_rules_with_user_content_replaces(cursor_home):
    """Rules file exists without managed markers → replace with prompt."""
    rules_path = cursor_home / ".cursor" / "rules" / "lore.md"
    rules_path.write_text("# my own lore rules\nfoo bar\n")
    actions = cursor.plan(InstallContext())
    replace_actions = [a for a in actions if a.kind == "replace"]
    # The rules file replace; the mcp merge is also there since fresh
    assert any(
        "lore.md" in a.target for a in replace_actions
    ), "user-authored rules file must be flagged for replace"


def test_uninstall_round_trip_preserves_user_content_outside_markers(cursor_home):
    """Apply install → user appends content below managed block →
    uninstall preserves the appended content."""
    actions = cursor.plan(InstallContext())
    for a in actions:
        execute_action(a)
    rules_path = cursor_home / ".cursor" / "rules" / "lore.md"
    assert rules_path.exists()
    # User appends content
    rules_path.write_text(
        rules_path.read_text() + "\n# my own additions below\nfoo\n"
    )
    # Uninstall
    for a in cursor.uninstall_plan(InstallContext()):
        execute_action(a)
    # The lore-managed block is gone but user content remains
    final = rules_path.read_text() if rules_path.exists() else ""
    assert "my own additions" in final
    assert MANAGED_BLOCK_START not in final
    assert MANAGED_BLOCK_END not in final


def test_uninstall_round_trip_preserves_other_mcp_servers(cursor_home):
    """Other mcpServers entries must survive lore uninstall."""
    mcp_path = cursor_home / ".cursor" / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "other-server", "args": []},
                }
            }
        )
    )
    for a in cursor.plan(InstallContext()):
        execute_action(a)
    assert "lore" in json.loads(mcp_path.read_text())["mcpServers"]
    for a in cursor.uninstall_plan(InstallContext()):
        execute_action(a)
    final = json.loads(mcp_path.read_text())
    assert "lore" not in final["mcpServers"]
    assert "other" in final["mcpServers"]
