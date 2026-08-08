"""Manifest-to-CLI wiring guard for the hook dispatcher.

`.claude-plugin/plugin.json` tells Claude Code to shell out to
`lore hook <subcommand>` for every event. Typer registers those
subcommands as a side effect of the `@hook_app.command(...)` decorator,
so dropping a decorator removes the CLI entry point while leaving a
perfectly importable function behind — no ImportError, no warning.

That failure mode already shipped once: a refactor deleted
`@hook_app.command("user-prompt-submit")` along with an adjacent code
block, and every existing test kept passing because they all call
`cmd_user_prompt_submit()` directly instead of through the CLI. The hook
failed at runtime with `No such command 'user-prompt-submit'`.

This test walks the manifest instead of naming subcommands, so a new
event added to the manifest is covered the moment it lands.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from lore_cli.hooks import hook_app

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"


def _manifest_hook_subcommands() -> set[str]:
    """Every `<sub>` in a `lore hook <sub>` command string in the manifest."""
    manifest = json.loads(PLUGIN_MANIFEST.read_text())
    subcommands: set[str] = set()
    for matchers in manifest.get("hooks", {}).values():
        for matcher in matchers:
            for hook in matcher.get("hooks", []):
                parts = shlex.split(hook.get("command", ""))
                if parts[:2] == ["lore", "hook"] and len(parts) > 2:
                    subcommands.add(parts[2])
    return subcommands


def _registered_subcommands() -> set[str]:
    import typer.main

    return {cmd.name for cmd in typer.main.get_command(hook_app).commands.values()}


def test_manifest_hooks_are_registered_cli_commands() -> None:
    missing = _manifest_hook_subcommands() - _registered_subcommands()
    assert not missing, (
        f"plugin.json invokes `lore hook {sorted(missing)}` but the "
        f"subcommand is not registered on hook_app — most likely a missing "
        f"@hook_app.command(...) decorator."
    )


def test_manifest_declares_at_least_one_hook() -> None:
    # Guards the walker itself: a manifest-shape change that silently
    # yields an empty set would make the test above vacuously pass.
    assert _manifest_hook_subcommands()
