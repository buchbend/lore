"""Tests for `lore_core.install.opencode` (PRD 0022, OpenCode as a host).

Plan against a temporary home, apply each action, read back the plugin
file and the OpenCode config.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import pytest
from lore_core.install import _helpers, opencode
from lore_core.install._helpers import execute_action
from lore_core.install.base import InstallContext

REPO = Path(__file__).resolve().parents[1]
FAKE_LORE = "/opt/fake/bin/lore"


@pytest.fixture
def oc_home(tmp_path, monkeypatch):
    """Fake Linux $HOME with an OpenCode config dir; skills from a fake source."""
    monkeypatch.setattr(_helpers.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(_helpers.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        _helpers.shutil, "which", lambda name: FAKE_LORE if name == "lore" else None
    )
    src = tmp_path / "lore-src"
    for name in ("build", "tdd"):
        d = src / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: lore:{name}\ndescription: Do {name}.\n---\n# {name}\nbody\n"
        )
    (src / ".claude-plugin").mkdir()
    (src / ".claude-plugin" / "plugin.json").write_text(
        (REPO / ".claude-plugin" / "plugin.json").read_text()
    )
    monkeypatch.setattr(opencode, "resolve_lore_source_root", lambda *a, **kw: src)
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    return tmp_path


def _apply(actions):
    for a in actions:
        res = execute_action(a)
        assert res.ok or a.kind == "check", (a, res)


def _config(home: Path) -> dict:
    return json.loads((home / ".config" / "opencode" / "opencode.json").read_text())


def test_install_writes_plugin_with_marker_and_version_and_mcp_server(oc_home):
    _apply(opencode.plan(InstallContext()))

    plugin = (oc_home / ".config" / "opencode" / "plugins" / "lore.js").read_text()
    assert "lore-managed" in plugin
    assert importlib.metadata.version("lore") in plugin
    assert 'id: "lore"' in plugin
    assert opencode.installed_plugin_version() == importlib.metadata.version("lore")

    assert _config(oc_home)["mcp"]["servers"]["lore"] == {
        "type": "local",
        "command": [FAKE_LORE, "mcp"],
    }


JSONC = """\
// my OpenCode config
{
  "$schema": "https://opencode.ai/config.json", // keep me
  /* other servers */
  "mcp": {
    "servers": {
      "other": {"type": "local", "command": ["other-mcp"]},
    },
  },
  "commands": {
    "mine": {"template": "do mine"}
  }
}
"""


def test_merge_keeps_comments_and_other_entries_in_opencode_jsonc(oc_home):
    cfg = oc_home / ".config" / "opencode" / "opencode.jsonc"
    cfg.write_text(JSONC)

    _apply(opencode.plan(InstallContext()))

    text = cfg.read_text()
    for comment in ("// my OpenCode config", "// keep me", "/* other servers */"):
        assert comment in text
    assert not (oc_home / ".config" / "opencode" / "opencode.json").exists()
    from lore_core.jsonc import loads

    data = loads(text)
    assert data["mcp"]["servers"]["other"] == {"type": "local", "command": ["other-mcp"]}
    assert data["mcp"]["servers"]["lore"]["command"] == [FAKE_LORE, "mcp"]
    assert data["commands"]["mine"] == {"template": "do mine"}


def test_install_adds_one_command_per_skill_that_uses_the_skill(oc_home):
    _apply(opencode.plan(InstallContext()))

    commands = _config(oc_home)["commands"]
    assert set(commands) == {"lore:build", "lore:tdd"}
    assert "lore:build" in commands["lore:build"]["template"]
    assert commands["lore:build"]["description"] == "Do build."


def test_uninstall_removes_plugin_and_lore_entries_and_keeps_the_rest(oc_home):
    cfg = oc_home / ".config" / "opencode" / "opencode.jsonc"
    cfg.write_text(JSONC)
    _apply(opencode.plan(InstallContext()))

    _apply(opencode.uninstall_plan(InstallContext()))

    assert not (oc_home / ".config" / "opencode" / "plugins" / "lore.js").exists()
    text = cfg.read_text()
    assert "// keep me" in text
    from lore_core.jsonc import loads

    data = loads(text)
    assert "lore" not in data["mcp"]["servers"]
    assert "other" in data["mcp"]["servers"]
    assert set(data["commands"]) == {"mine"}


def test_uninstall_leaves_a_plugin_file_lore_did_not_write(oc_home):
    plugin = oc_home / ".config" / "opencode" / "plugins" / "lore.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// the person's own plugin\n")

    _apply(opencode.uninstall_plan(InstallContext()))

    assert plugin.exists()


def test_every_manifest_hook_event_has_an_opencode_table_row():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    missing = set(manifest["hooks"]) - set(opencode.EVENT_TABLE)
    assert not missing, f"add an OpenCode event table row for {sorted(missing)}"


def test_event_commands_follow_the_table_and_leave_capture_to_613():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert opencode.event_commands(manifest) == {
        "start": [["hook", "session-start"]],
        "prompt": [["hook", "user-prompt-submit"]],
        "compaction": [["hook", "pre-compact"]],
    }


_DRIVER = r"""
import plugin from "./lore.mjs";
const hooks = {}, shell = {}, transforms = [];
const agents = [{ id: "build", mode: "primary" }, { id: "general", mode: "subagent" }];
const ctx = {
  location: { directory: process.argv[2] },
  agent: { list: async () => ({ data: agents }) },
  session: { hook: async (name, fn) => { hooks[name] = fn; } },
  shell: { hook: async (name, fn) => { shell[name] = fn; } },
  skill: { transform: async (fn) => { transforms.push(fn); } },
};
await plugin.setup(ctx);
const req = (sessionID, agent) => ({ sessionID, agent, system: [{ type: "text", text: "base" }] });
const out = {};
await hooks.prompt({ sessionID: "ses_main", prompt: { text: "hi" } });
const first = req("ses_main", "build"); await hooks.context(first); out.first = first.system;
const second = req("ses_main", "build"); await hooks.context(second); out.second = second.system;
await hooks.prompt({ sessionID: "ses_sub", prompt: { text: "task" } });
const sub = req("ses_sub", "general"); await hooks.context(sub); out.sub = sub.system;
const comp = req("ses_main", "build"); await hooks.compaction(comp); out.compaction = comp.system;
const s = { command: "ls", env: {} }; await shell["create.before"](s); out.shellEnv = s.env;
const added = [];
for (const t of transforms) t({ get: () => undefined, add: (k) => added.push(k) });
out.skills = added;
out.id = plugin.id;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_generated_plugin_runs_under_node_against_a_fake_ctx(tmp_path):
    import subprocess

    log = tmp_path / "lore.log"
    fake = tmp_path / "lore"
    fake.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        f'echo "$LORE_HOST $LORE_SESSION_ID $*" >> "{log}"\n'
        'echo "OUT-$2"\n'
    )
    fake.chmod(0o755)
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: lore:build\n---\n# Build\n")
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    skills = [{"id": "lore:build", "name": "lore:build", "description": "d", "path": str(skill)}]
    (tmp_path / "lore.mjs").write_text(
        opencode.generate_plugin(manifest, skills, lore_bin=str(fake), version="9.9.9")
    )
    (tmp_path / "driver.mjs").write_text(_DRIVER)

    proc = subprocess.run(
        ["node", "driver.mjs", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    out = json.loads(proc.stdout)

    text = lambda parts: [p["text"] for p in parts]  # noqa: E731
    assert out["id"] == "lore"
    assert text(out["first"]) == ["base", "OUT-session-start", "OUT-user-prompt-submit"]
    assert text(out["second"]) == ["base", "OUT-session-start"]
    assert text(out["sub"]) == ["base"]
    assert text(out["compaction"]) == ["base", "OUT-pre-compact"]
    assert out["shellEnv"] == {"LORE_HOST": "opencode", "LORE_SESSION_ID": "ses_main"}
    assert out["skills"] == [{**skills[0], "content": "# Build\n"}]
    assert log.read_text().splitlines() == [
        f"opencode ses_main hook session-start --plain --cwd {tmp_path}",
        f"opencode ses_main hook user-prompt-submit --plain --cwd {tmp_path}",
        f"opencode ses_main hook pre-compact --plain --cwd {tmp_path}",
    ]
