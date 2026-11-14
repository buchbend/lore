"""Tests for `lore_core.install.opencode` (PRD 0022, OpenCode as a host).

Plan against a temporary home, apply each action, read back the plugin
file and the OpenCode config.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
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


def test_event_commands_follow_the_table():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert opencode.event_commands(manifest) == {
        "start": [["hook", "session-start"]],
        "prompt": [["hook", "user-prompt-submit"]],
        "compaction": [["hook", "pre-compact"]],
        "end": [["hook", "capture", "--event", "session-end"], ["hook", "token-usage"]],
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


def test_no_source_root_writes_no_plugin_so_the_next_setup_retries(oc_home, monkeypatch):
    monkeypatch.setattr(opencode, "resolve_lore_source_root", lambda *a, **kw: None)

    actions = opencode.plan(InstallContext())
    _apply(actions)

    assert not opencode.plugin_path().exists()
    assert opencode.installed_plugin_version() is None
    assert any(a.payload.get("check") == "always_advisory" for a in actions)


def test_a_plugin_file_without_the_marker_needs_a_confirmed_replace(oc_home):
    plugin = opencode.plugin_path()
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// the person's own plugin\n")

    replace = [a for a in opencode.plan(InstallContext()) if a.target == str(plugin)]

    assert [a.kind for a in replace] == ["replace"]
    assert replace[0].payload["reason"] == "no lore-managed marker"
    assert replace[0].payload["old_value"] == "// the person's own plugin\n"
    assert execute_action(replace[0]).ok
    assert opencode.installed_plugin_version() == importlib.metadata.version("lore")


_CAPTURE_DRIVER = r"""
import plugin from "./lore.mjs";
import { existsSync, readFileSync } from "node:fs";
const hooks = {};
const agents = [{ id: "build", mode: "primary" }, { id: "general", mode: "subagent" }];
const user = (id, text) => ({ type: "user", id, text });
const msgs = { ses_main: [user("m1", "hi")], ses_sub: [user("s1", "t")] };
let failContext = false;
const ctx = {
  location: { directory: process.argv[2] },
  agent: { list: async () => ({ data: agents }) },
  session: {
    hook: async (name, fn) => { hooks[name] = fn; },
    context: async ({ sessionID }) => {
      if (failContext) throw new Error("boom");
      return msgs[sessionID];
    },
    get: async ({ sessionID }) => ({
      id: sessionID, parentID: sessionID === "ses_sub" ? "ses_main" : undefined,
    }),
  },
  shell: { hook: async () => {} },
  skill: { transform: async () => {} },
};
const cleanup = await plugin.setup(ctx);
const req = (sessionID, agent) => ({ sessionID, agent, system: [] });
const dir = process.env.LORE_OPENCODE_DIR;
const file = (id) => `${dir}/${id}.jsonl`;
const lines = (id) => readFileSync(file(id), "utf8").trim().split("\n").map((l) => JSON.parse(l));
const out = {};
await hooks.context(req("ses_main", "build"));
await hooks.context(req("ses_sub", "general"));
out.beforeIdle = existsSync(file("ses_main"));
await new Promise((r) => setTimeout(r, 300)); // the debounce fires
out.afterIdle = lines("ses_main");
out.sub = lines("ses_sub")[0];
msgs.ses_main.push({ type: "assistant", id: "m2", content: [] }); // next turn
await hooks.context(req("ses_main", "build"));
await cleanup();
await cleanup(); // a second flush writes the same file
out.final = lines("ses_main");
failContext = true;
msgs.ses_main.push({ type: "user", id: "m3", text: "later" });
await cleanup(); // the call fails: nothing changes, nothing throws
out.afterFailure = lines("ses_main").length;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_plugin_writes_session_files_and_captures_on_cleanup(tmp_path):
    import subprocess

    log = tmp_path / "lore.log"
    fake = tmp_path / "lore"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$LORE_HOST $LORE_SESSION_ID $*" >> "{log}"\n'
        f'cat >> "{log}"; echo >> "{log}"\n'
    )
    fake.chmod(0o755)
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    (tmp_path / "lore.mjs").write_text(
        opencode.generate_plugin(manifest, [], lore_bin=str(fake), version="9.9.9")
    )
    (tmp_path / "driver.mjs").write_text(_CAPTURE_DRIVER)
    sessions = tmp_path / "sessions"
    env = {**os.environ, "LORE_OPENCODE_DIR": str(sessions), "LORE_OPENCODE_DEBOUNCE_MS": "50"}

    proc = subprocess.run(
        ["node", "driver.mjs", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env=env,
    )
    out = json.loads(proc.stdout)

    assert out["beforeIdle"] is False
    header, first = out["afterIdle"]
    assert header == {
        "type": "session",
        "host": "opencode",
        "sessionID": "ses_main",
        "directory": str(tmp_path),
        "parentID": None,
    }
    assert first["text"] == "hi"
    assert out["sub"]["parentID"] == "ses_main"
    assert [m["id"] for m in out["final"][1:]] == ["m1", "m2"]
    assert out["afterFailure"] == 3  # header + m1 + m2: the failed read wrote nothing
    # capture and the token record run once per cleanup, for the main session only
    calls = [
        ln
        for ln in log.read_text().splitlines()
        if ln.startswith("opencode ") and ("hook capture" in ln or "token-usage" in ln)
    ]
    assert (
        calls
        == [
            "opencode ses_main hook capture --event session-end",
            "opencode ses_main hook token-usage",
        ]
        * 3
    )
    payload = json.loads(next(ln for ln in log.read_text().splitlines() if ln.startswith("{")))
    assert payload["session_id"] == "ses_main"
    assert payload["transcript_path"] == str(sessions / "ses_main.jsonl")
