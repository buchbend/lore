"""Tests for `lore_core.install.cursor` — per-platform path resolution
+ schema-versioning paths (absent → silent migrate; present-but-old →
replace) + managed-marker preservation on uninstall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_core.install import _helpers, cursor
from lore_core.install._helpers import execute_action
from lore_core.install.base import InstallContext
from lore_core.managed_files import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    SCHEMA_VERSION_KEY,
    write_managed_markdown,
)


@pytest.fixture
def cursor_home(tmp_path, monkeypatch):
    """Fake Linux $HOME with a Cursor config dir at the legacy location.

    Stubs ``resolve_lore_source_root`` to None by default so legacy
    fallback paths are exercised. Tests covering the plugin-packaging
    path override this with a fake source dir.
    """
    monkeypatch.setattr(_helpers.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(
        _helpers.Path, "home", classmethod(lambda cls: tmp_path)
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "rules").mkdir()
    # By default, no source root resolved — tests that want plugin
    # packaging exercised will override this.
    monkeypatch.setattr(cursor, "resolve_lore_source_root", lambda *a, **kw: None)
    return tmp_path


@pytest.fixture
def fake_source_root(tmp_path):
    """Fake lore source-of-truth tree for plugin-packaging tests.

    Layout:
      <root>/skills/sample-skill/SKILL.md
      <root>/lib/lore_core/templates/integration-rules/default.md
      <root>/.claude-plugin/plugin.json   (mirrors real manifest hooks)
    """
    root = tmp_path / "lore-src"
    skills = root / "skills" / "sample-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: lore:sample\ndescription: x\n---\nbody\n")
    rules = root / "lib" / "lore_core" / "templates" / "integration-rules"
    rules.mkdir(parents=True)
    (rules / "default.md").write_text("vault-first directive\n")
    plugin_dir = root / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "name": "lore",
        "description": "test",
        "version": "9.9.9",
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "lore hook session-start"}]}
            ],
            "PostToolUse": [
                {"matcher": "ExitPlanMode",
                 "hooks": [{"type": "command", "command": "lore hook plan-capture"}]}
            ],
        },
        "mcpServers": {"lore": {"command": "lore", "args": ["mcp"]}},
    }))
    return root


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
    import lore_core
    body = (
        Path(lore_core.__file__).resolve().parent
        / "templates"
        / "integration-rules"
        / "default.md"
    ).read_text().rstrip("\n")
    write_managed_markdown(rules_path, body)
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


# ---------------------------------------------------------------------------
# Phase 1 — relative-command silent re-merge + user-customized prompt
# ---------------------------------------------------------------------------


def test_relative_command_silently_remerges_to_absolute(cursor_home, monkeypatch):
    """An entry written by an older lore (command: 'lore') silently
    re-merges to the absolute path — not KIND_REPLACE (no prompt)."""
    monkeypatch.setattr(
        _helpers.shutil, "which",
        lambda name: "/opt/lore-bin/lore" if name == "lore" else None,
    )
    mcp_path = cursor_home / ".cursor" / "mcp.json"
    mcp_path.write_text(json.dumps({
        "mcpServers": {
            "lore": {
                "command": "lore",
                "args": ["mcp"],
                SCHEMA_VERSION_KEY: cursor.SCHEMA_VERSION,
            }
        }
    }))
    actions = cursor.plan(InstallContext())
    merges = [a for a in actions if a.kind == "merge"
              and a.payload.get("key_path") == ["mcpServers", "lore"]]
    replaces = [a for a in actions if a.kind == "replace"]
    assert len(merges) == 1, "relative→absolute migration must be silent merge"
    assert len(replaces) == 0
    assert merges[0].payload["value"]["command"] == "/opt/lore-bin/lore"


def test_user_customized_entry_emits_replace_with_prompt(cursor_home):
    """When a user has customized the entry (wrapper / extra args /
    env vars), differing content gets KIND_REPLACE with a prompt."""
    mcp_path = cursor_home / ".cursor" / "mcp.json"
    mcp_path.write_text(json.dumps({
        "mcpServers": {
            "lore": {
                "command": "/opt/lore-wrapper.sh",
                "args": ["mcp", "--debug"],
                "env": {"LORE_VERBOSE": "1"},
                SCHEMA_VERSION_KEY: cursor.SCHEMA_VERSION,
            }
        }
    }))
    actions = cursor.plan(InstallContext())
    replaces = [a for a in actions if a.kind == "replace"
                and a.payload.get("key_path") == ["mcpServers", "lore"]]
    assert len(replaces) == 1
    assert "user customizations" in replaces[0].payload["reason"]


# ---------------------------------------------------------------------------
# Phase 1 — Cursor plugin packaging
# ---------------------------------------------------------------------------


def test_plan_emits_plugin_dir_actions(cursor_home, fake_source_root, monkeypatch):
    """When source resolves, plan() emits actions to materialize the
    Cursor plugin dir (sentinel, manifest, skills, rules, mcp, hooks)."""
    monkeypatch.setattr(
        cursor, "resolve_lore_source_root",
        lambda *a, **kw: fake_source_root,
    )
    actions = cursor.plan(InstallContext())
    targets = [a.target for a in actions]
    plugin_root = str(cursor_home / ".cursor" / "plugins" / "local" / "lore")
    assert any(t == f"{plugin_root}/.lore-managed" for t in targets)
    assert any(t == f"{plugin_root}/.cursor-plugin/plugin.json" for t in targets)
    assert any(t == f"{plugin_root}/skills" for t in targets)
    assert any(t == f"{plugin_root}/mcp.json" for t in targets)
    assert any(t == f"{plugin_root}/hooks.json" for t in targets)


def test_plugin_manifest_mirrors_claude_manifest_version(
    cursor_home, fake_source_root, monkeypatch
):
    """The generated Cursor plugin manifest carries the Claude
    manifest's version (single source of truth)."""
    monkeypatch.setattr(
        cursor, "resolve_lore_source_root",
        lambda *a, **kw: fake_source_root,
    )
    actions = cursor.plan(InstallContext())
    manifest_action = next(
        a for a in actions
        if a.target.endswith(".cursor-plugin/plugin.json")
    )
    content = json.loads(manifest_action.payload["content"])
    assert content["name"] == "lore"
    assert content["version"] == "9.9.9"


def test_hooks_json_maps_events_to_cursor_schema(fake_source_root):
    """Direct generator test: Claude hook event names → Cursor names."""
    claude_manifest = json.loads(
        (fake_source_root / ".claude-plugin" / "plugin.json").read_text()
    )
    cursor_hooks = _helpers.generate_cursor_hooks_json(claude_manifest)
    assert cursor_hooks["version"] == 1
    events = cursor_hooks["hooks"]
    # Claude's SessionStart → Cursor's sessionStart
    assert "sessionStart" in events
    # PostToolUse with matcher
    assert "postToolUse" in events
    matchers = [h.get("matcher") for h in events["postToolUse"]]
    assert "ExitPlanMode" in matchers


def test_hooks_json_resolves_lore_to_absolute_path(fake_source_root, monkeypatch):
    """Hook commands of the form 'lore <subcmd>' get the lore part
    rewritten with the absolute path."""
    monkeypatch.setattr(
        _helpers.shutil, "which",
        lambda name: "/opt/lore-bin/lore" if name == "lore" else None,
    )
    claude_manifest = json.loads(
        (fake_source_root / ".claude-plugin" / "plugin.json").read_text()
    )
    cursor_hooks = _helpers.generate_cursor_hooks_json(claude_manifest)
    session_start = cursor_hooks["hooks"]["sessionStart"]
    assert all(h["command"].startswith("/opt/lore-bin/lore ") for h in session_start)


def test_skills_copied_as_real_dir(cursor_home, fake_source_root, monkeypatch):
    """After install, the plugin's skills/ is a real dir (not a
    symlink) and the parent has a .lore-managed sentinel."""
    monkeypatch.setattr(
        cursor, "resolve_lore_source_root",
        lambda *a, **kw: fake_source_root,
    )
    for a in cursor.plan(InstallContext()):
        execute_action(a)
    plugin_dir = cursor_home / ".cursor" / "plugins" / "local" / "lore"
    skills_dst = plugin_dir / "skills"
    assert skills_dst.is_dir()
    assert not skills_dst.is_symlink()
    assert (plugin_dir / ".lore-managed").exists()
    assert (skills_dst / "sample-skill" / "SKILL.md").exists()


def test_uninstall_removes_plugin_dir_only_with_sentinel(
    cursor_home, fake_source_root, monkeypatch
):
    """Uninstall removes the plugin dir when the sentinel is present;
    refuses (and leaves contents intact) when sentinel is missing."""
    monkeypatch.setattr(
        cursor, "resolve_lore_source_root",
        lambda *a, **kw: fake_source_root,
    )
    # Install so the plugin dir exists with the sentinel
    for a in cursor.plan(InstallContext()):
        execute_action(a)
    plugin_dir = cursor_home / ".cursor" / "plugins" / "local" / "lore"
    assert plugin_dir.exists()
    # Uninstall removes the tree
    for a in cursor.uninstall_plan(InstallContext()):
        execute_action(a)
    assert not plugin_dir.exists()


def test_uninstall_refuses_without_sentinel(cursor_home):
    """If a user-authored dir collides at the plugin path with no
    sentinel, uninstall must NOT wipe it."""
    plugin_dir = cursor_home / ".cursor" / "plugins" / "local" / "lore"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "user-content.md").write_text("DO NOT DELETE\n")
    actions = cursor.uninstall_plan(InstallContext())
    # Without sentinel, no recursive-delete action emitted at all
    recursives = [a for a in actions if a.payload.get("recursive")]
    assert len(recursives) == 0
    assert (plugin_dir / "user-content.md").exists()


def test_global_mcp_entry_skipped_when_plugin_packages(
    cursor_home, fake_source_root, monkeypatch
):
    """When plugin packaging will run, plan() does NOT add a fresh
    mcpServers.lore to the global mcp.json — only the plugin-local
    one is canonical."""
    monkeypatch.setattr(
        cursor, "resolve_lore_source_root",
        lambda *a, **kw: fake_source_root,
    )
    actions = cursor.plan(InstallContext())
    global_mcp_path = cursor_home / ".cursor" / "mcp.json"
    global_merges = [
        a for a in actions
        if a.kind == "merge" and a.target == str(global_mcp_path)
    ]
    assert len(global_merges) == 0


def test_global_mcp_entry_deleted_when_legacy_present(
    cursor_home, fake_source_root, monkeypatch
):
    """When plugin packaging runs and a legacy global entry exists,
    plan() emits a delete to dedupe."""
    monkeypatch.setattr(
        cursor, "resolve_lore_source_root",
        lambda *a, **kw: fake_source_root,
    )
    global_mcp_path = cursor_home / ".cursor" / "mcp.json"
    global_mcp_path.write_text(json.dumps({
        "mcpServers": {
            "lore": {"command": "lore", "args": ["mcp"]},
            "other": {"command": "other-server", "args": []},
        }
    }))
    actions = cursor.plan(InstallContext())
    deletes = [
        a for a in actions
        if a.kind == "delete"
        and a.target == str(global_mcp_path)
        and a.payload.get("key_path") == ["mcpServers", "lore"]
    ]
    assert len(deletes) == 1


# ---------------------------------------------------------------------------
# Phase 1 — hooks env-var fallback
# ---------------------------------------------------------------------------


def test_resolve_cwd_falls_back_to_cursor_project_dir(monkeypatch, tmp_path):
    """When CLAUDE_PROJECT_DIR is unset and CURSOR_PROJECT_DIR is set,
    _resolve_cwd picks up the Cursor variant (defensive fallback for
    older Cursor versions where the alias hasn't been wired)."""
    from lore_cli.hooks import _resolve_cwd, _resolve_cwd_capture
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(tmp_path))
    assert _resolve_cwd(None) == str(tmp_path)
    assert _resolve_cwd_capture() == tmp_path


def test_resolve_cwd_prefers_claude_project_dir_when_both_set(
    monkeypatch, tmp_path
):
    """If both env vars are set (Cursor's alias case), CLAUDE wins
    because Cursor sets CLAUDE_PROJECT_DIR as the canonical alias."""
    from lore_cli.hooks import _resolve_cwd
    claude_dir = tmp_path / "claude"
    cursor_dir = tmp_path / "cursor"
    claude_dir.mkdir()
    cursor_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_dir))
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(cursor_dir))
    assert _resolve_cwd(None) == str(claude_dir)
