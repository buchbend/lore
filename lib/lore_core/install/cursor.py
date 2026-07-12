"""Cursor installer module.

Two file mutations + one check:

  1. Merge `mcpServers.lore` into `<cursor-config-dir>/mcp.json`
     with `_lore_schema_version: "1"` for future migrations.
  2. Write `<cursor-rules-dir>/lore.md` with the ambient SessionStart
     directive wrapped in `<!-- lore-managed-start -->` …
     `<!-- lore-managed-end -->` markers so user-appended content
     survives uninstall.
  3. Verify `lore` is on PATH (Cursor's MCP client subprocess-spawns
     the server via that name).

Per-platform path resolution lives in `_helpers.cursor_config_dir()`
and `cursor_rules_dir()`; macOS + Linux supported in v1, Windows
refused.
"""

from __future__ import annotations

from pathlib import Path

from lore_core import managed_files
from lore_core.install import _helpers
from lore_core.install.base import (
    KIND_CHECK,
    KIND_DELETE,
    KIND_MERGE,
    KIND_NEW,
    KIND_REPLACE,
    Action,
    InstallContext,
    LegacyArtifact,
)
from lore_core.source_root import read_claude_manifest, resolve_lore_source_root

SCHEMA_VERSION = "1"


def _read_directive_body() -> str:
    """Pull the canonical SessionStart directive out of the bundled templates.

    Resolved at call time (not import time) so tests can monkeypatch the
    directive path. Delegates the directory lookup to
    ``lore_core.templates.templates_dir`` so all callers stay consistent.
    """
    from lore_core.templates import templates_dir
    return (templates_dir() / "integration-rules" / "default.md").read_text().rstrip("\n")


def plan(ctx: InstallContext) -> list[Action]:
    """Return Actions to install Lore for Cursor.

    When the lore source-of-truth resolves (dev install / Claude plugin
    cache available), Cursor 2.5+ plugin packaging is the canonical
    install state — the global ``~/.cursor/mcp.json`` entry would be a
    duplicate registration, so we skip it (and clean up any stale
    legacy entry). The global rules file at ``~/.cursor/rules/lore.md``
    stays in both modes since it serves a separate purpose (always-on
    directive vs plugin-scoped activation).
    """
    actions: list[Action] = []

    config_dir = _helpers.cursor_config_dir()
    rules_dir = _helpers.cursor_rules_dir()
    mcp_path = config_dir / "mcp.json"
    rules_path = rules_dir / "lore.md"
    source_root = resolve_lore_source_root(ctx.lore_repo)
    plugin_will_install = source_root is not None

    # 1. Global mcp.json — only when plugin packaging won't run (legacy
    #    fallback). Otherwise plugin-local mcp.json is canonical and we
    #    actively dedupe by emitting a delete for any pre-existing
    #    legacy global entry.
    existing = _read_existing_lore_entry(mcp_path)
    new_value = _helpers.lore_mcp_entry(SCHEMA_VERSION)
    if plugin_will_install:
        if existing is not None:
            actions.append(
                Action(
                    kind=KIND_DELETE,
                    description=(
                        "Remove legacy global mcpServers.lore "
                        "(plugin-local now wins)"
                    ),
                    target=str(mcp_path),
                    summary="dedupe: plugin-local mcp.json supersedes",
                    payload={
                        "path": str(mcp_path),
                        "key_path": ["mcpServers", "lore"],
                    },
                )
            )
    elif existing is None:
        actions.append(
            Action(
                kind=KIND_MERGE,
                description="Add Lore MCP server to Cursor's mcp.json",
                target=str(mcp_path),
                summary="add mcpServers.lore (1 entry)",
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                    "value": new_value,
                    "schema_version": SCHEMA_VERSION,
                },
            )
        )
    elif existing.get(managed_files.SCHEMA_VERSION_KEY) is None:
        # Absent schema version — legacy or user-authored. Migrate
        # in place silently (kind=merge, no extra prompt).
        actions.append(
            Action(
                kind=KIND_MERGE,
                description="Migrate existing mcpServers.lore to schema v1",
                target=str(mcp_path),
                summary="adopt mcpServers.lore (no _lore_schema_version found)",
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                    "value": new_value,
                    "schema_version": SCHEMA_VERSION,
                },
            )
        )
    elif existing.get(managed_files.SCHEMA_VERSION_KEY) != SCHEMA_VERSION:
        # Present-but-old schema — true bump, needs explicit prompt.
        actions.append(
            Action(
                kind=KIND_REPLACE,
                description="Upgrade mcpServers.lore schema",
                target=str(mcp_path),
                summary=(
                    f"replace mcpServers.lore "
                    f"({existing.get(managed_files.SCHEMA_VERSION_KEY)} "
                    f"→ {SCHEMA_VERSION})"
                ),
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                    "old_value": existing,
                    "new_value": new_value,
                    "reason": (
                        f"_lore_schema_version "
                        f"{existing.get(managed_files.SCHEMA_VERSION_KEY)} "
                        f"→ {SCHEMA_VERSION}"
                    ),
                },
            )
        )
    elif existing != new_value and _is_lore_managed_entry(existing):
        # Schema matches but content drifted — silent re-merge.
        # This catches the relative-→-absolute path migration: an
        # entry written by an older lore had `command: "lore"` but
        # Cursor's GUI subprocess inherits a minimal PATH and can't
        # find pipx installs by bare name. Refresh silently because
        # the user didn't change anything; we fixed a bug.
        actions.append(
            Action(
                kind=KIND_MERGE,
                description="Refresh mcpServers.lore (lore-managed re-resolve)",
                target=str(mcp_path),
                summary="refresh mcpServers.lore (resolved abs path / args)",
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                    "value": new_value,
                    "schema_version": SCHEMA_VERSION,
                },
            )
        )
    elif existing != new_value:
        # Schema matches, content differs, and the entry is not
        # recognizably lore-managed — user has customized the entry
        # (wrapper script, custom args, env vars). Prompt before
        # clobbering.
        actions.append(
            Action(
                kind=KIND_REPLACE,
                description="Replace user-customized mcpServers.lore",
                target=str(mcp_path),
                summary="user-customized entry differs from canonical",
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                    "old_value": existing,
                    "new_value": new_value,
                    "reason": "entry has user customizations (wrapper / args / env)",
                },
            )
        )
    # else: existing == new_value — no action needed.

    # 2. Write the rules file (managed-marker wrapped). If the file
    #    has no managed markers, treat as user-authored — the
    #    dispatcher will refuse to clobber without --force.
    body = _read_directive_body()
    full_content = (
        f"{managed_files.MANAGED_BLOCK_START}\n{body}\n{managed_files.MANAGED_BLOCK_END}\n"
    )
    if not rules_path.exists():
        actions.append(
            Action(
                kind=KIND_NEW,
                description="Write Lore directive to Cursor rules",
                target=str(rules_path),
                summary=f"new file (~{len(body.splitlines())} lines, "
                "SessionStart directive)",
                payload={
                    "path": str(rules_path),
                    "content": full_content,
                },
            )
        )
    else:
        existing_managed = managed_files.managed_block_content(rules_path)
        if existing_managed is None:
            # File exists with no managed markers — user-authored
            actions.append(
                Action(
                    kind=KIND_REPLACE,
                    description="Replace user-authored Lore rules file",
                    target=str(rules_path),
                    summary="existing file has no lore-managed markers",
                    payload={
                        "path": str(rules_path),
                        "content": full_content,
                        "key_path": [],  # whole file
                        "old_value": rules_path.read_text(),
                        "new_value": full_content,
                        "reason": "no <!-- lore-managed-start --> marker found",
                    },
                )
            )
        elif managed_files.content_hash(existing_managed) != managed_files.content_hash(body):
            # Markers present, content drifted from the canonical
            # template — replace the managed block (preserving any
            # user content outside the markers).
            actions.append(
                Action(
                    kind=KIND_NEW,
                    description="Update Lore directive in Cursor rules",
                    target=str(rules_path),
                    summary="content hash mismatch — refresh managed block",
                    payload={
                        "path": str(rules_path),
                        "content": full_content,
                    },
                )
            )
        # else: hashes match — no action

    # 3. Verify lore is on PATH (Cursor's MCP client needs it).
    actions.append(
        Action(
            kind=KIND_CHECK,
            description="Verify lore CLI is reachable for the MCP server",
            target="lore CLI",
            summary="shutil.which('lore') returns non-None",
            payload={"check": "lore_on_path"},
        )
    )

    # 4. Cursor 2.5+ plugin packaging — bundle skills/rules/hooks/MCP
    #    into ~/.cursor/plugins/local/lore/ so Cursor lists "lore" as
    #    a first-class plugin and uninstall is one rmtree.
    actions.extend(_plan_plugin_packaging(ctx))

    return actions


def _plan_plugin_packaging(ctx: InstallContext) -> list[Action]:
    """Emit the Actions that materialize ~/.cursor/plugins/local/lore/.

    Returns an empty list (with a CHECK that surfaces the issue) if the
    lore source-of-truth can't be resolved — Cursor users who installed
    via pipx without `--lore-repo` and without Claude Code's plugin
    cache on disk fall into this bucket.
    """
    actions: list[Action] = []
    plugin_dir = _helpers.cursor_plugin_dir()
    source_root = resolve_lore_source_root(ctx.lore_repo)
    if source_root is None:
        actions.append(
            Action(
                kind=KIND_CHECK,
                description=(
                    "Skipping Cursor plugin packaging — lore source not "
                    "resolved"
                ),
                target=str(plugin_dir),
                summary=(
                    "no skills/ + .claude-plugin/ found in lore_repo, "
                    "editable install, or Claude plugin cache"
                ),
                payload={
                    "check": "always_advisory",
                    "fail_message": (
                        "Cursor plugin dir not created. Pass --lore-repo "
                        "or install lore from source to bundle skills."
                    ),
                },
                on_failure="continue",
            )
        )
        return actions

    claude_manifest = read_claude_manifest(source_root)
    cursor_manifest = _helpers.generate_cursor_plugin_manifest(claude_manifest)
    cursor_hooks = _helpers.generate_cursor_hooks_json(claude_manifest)
    plugin_mcp = {
        "mcpServers": {"lore": _helpers.lore_mcp_entry(SCHEMA_VERSION)}
    }

    sentinel_path = plugin_dir / managed_files.PLUGIN_SENTINEL
    manifest_path = plugin_dir / ".cursor-plugin" / "plugin.json"
    skills_dst = plugin_dir / "skills"
    rules_dst = plugin_dir / "rules"
    plugin_mcp_path = plugin_dir / "mcp.json"
    plugin_hooks_path = plugin_dir / "hooks.json"

    # 4a. Plugin sentinel — must exist before any copy_from runs (the
    #     copy helper checks for it on the parent dir).
    actions.append(
        Action(
            kind=KIND_NEW,
            description="Mark plugin dir as lore-managed",
            target=str(sentinel_path),
            summary=f"sentinel {managed_files.PLUGIN_SENTINEL} (provenance for uninstall)",
            payload={
                "path": str(sentinel_path),
                "content": (
                    f"# lore-managed Cursor plugin\n"
                    f"# Removing this file disables uninstall safety.\n"
                ),
            },
        )
    )

    # 4b. Plugin manifest (Cursor's smaller schema).
    import json as _json
    actions.append(
        Action(
            kind=KIND_NEW,
            description="Write Cursor plugin manifest",
            target=str(manifest_path),
            summary=f"version {cursor_manifest.get('version', '?')}",
            payload={
                "path": str(manifest_path),
                "content": _json.dumps(cursor_manifest, indent=2) + "\n",
            },
        )
    )

    # 4c. Skills tree — copy from source-of-truth into plugin dir.
    actions.append(
        Action(
            kind=KIND_NEW,
            description="Copy skills tree into Cursor plugin",
            target=str(skills_dst),
            summary=f"copy {source_root / 'skills'} → {skills_dst}",
            payload={
                "path": str(skills_dst),
                "copy_from": str(source_root / "skills"),
            },
        )
    )

    # 4d. Rules tree — SessionStart directive in markdown form.
    rules_src = source_root / "lib" / "lore_core" / "templates" / "integration-rules"
    if rules_src.is_dir():
        actions.append(
            Action(
                kind=KIND_NEW,
                description="Copy rules tree into Cursor plugin",
                target=str(rules_dst),
                summary=f"copy {rules_src} → {rules_dst}",
                payload={
                    "path": str(rules_dst),
                    "copy_from": str(rules_src),
                },
            )
        )

    # 4e. Plugin-local mcp.json (Cursor's per-plugin MCP discovery).
    actions.append(
        Action(
            kind=KIND_NEW,
            description="Write plugin-local Cursor MCP config",
            target=str(plugin_mcp_path),
            summary="mcpServers.lore (abs path)",
            payload={
                "path": str(plugin_mcp_path),
                "content": _json.dumps(plugin_mcp, indent=2) + "\n",
            },
        )
    )

    # 4f. Plugin-local hooks.json — Cursor 1.7+ schema, generated from
    #     the Claude manifest's hooks block.
    actions.append(
        Action(
            kind=KIND_NEW,
            description="Write plugin-local Cursor hooks config",
            target=str(plugin_hooks_path),
            summary=f"{len(cursor_hooks.get('hooks') or {})} events mapped",
            payload={
                "path": str(plugin_hooks_path),
                "content": _json.dumps(cursor_hooks, indent=2) + "\n",
            },
        )
    )

    # (Global mcp.json dedupe is handled in plan() above — done at the
    # top-level so the gating logic is in one place.)

    return actions


def _read_existing_lore_entry(mcp_path: Path) -> dict | None:
    """Return the current mcpServers.lore block, or None if absent."""
    if not mcp_path.exists():
        return None
    import json

    try:
        data = json.loads(mcp_path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict):
        return None
    entry = servers.get("lore")
    return entry if isinstance(entry, dict) else None


def _is_lore_managed_entry(existing: dict) -> bool:
    """True if `existing` looks like an entry a previous lore install wrote.

    Used to decide whether content drift (e.g. relative `command: "lore"`
    upgraded to an absolute path) can be silently re-merged or whether
    the user has customized the entry and a prompt is required.

    Recognizable shape:
      - `args == ["mcp"]` (lore CLI's MCP subcommand, never customized)
      - `command` is either the bare string "lore" or a path whose
        basename is "lore" (catches abs paths from any pipx/uv/pip prefix)
      - no extra keys beyond {command, args, _lore_schema_version}
    """
    if existing.get("args") != ["mcp"]:
        return False
    cmd = existing.get("command", "")
    if not isinstance(cmd, str):
        return False
    if cmd != "lore" and Path(cmd).name != "lore":
        return False
    allowed = {"command", "args", managed_files.SCHEMA_VERSION_KEY}
    return set(existing.keys()) <= allowed


def uninstall_plan(ctx: InstallContext) -> list[Action]:
    """Actions to remove Lore from Cursor.

    Symmetric to install: remove the mcpServers.lore key (preserving
    other servers and any user-touched fields outside the managed
    range) and remove the managed block from the rules file
    (preserving any user-appended content outside the markers).
    """
    config_dir = _helpers.cursor_config_dir()
    rules_dir = _helpers.cursor_rules_dir()
    mcp_path = config_dir / "mcp.json"
    rules_path = rules_dir / "lore.md"

    actions: list[Action] = []
    if mcp_path.exists() and _read_existing_lore_entry(mcp_path) is not None:
        actions.append(
            Action(
                kind=KIND_DELETE,
                description="Remove Lore MCP server from Cursor's mcp.json",
                target=str(mcp_path),
                summary="remove mcpServers.lore",
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                },
            )
        )
    if rules_path.exists() and managed_files.managed_block_content(rules_path) is not None:
        actions.append(
            Action(
                kind=KIND_DELETE,
                description="Remove Lore directive from Cursor rules",
                target=str(rules_path),
                summary="remove lore-managed block (preserves user content outside)",
                payload={
                    "path": str(rules_path),
                    "key_path": None,  # whole-file or managed-block removal
                },
            )
        )

    plugin_dir = _helpers.cursor_plugin_dir()
    sentinel = plugin_dir / managed_files.PLUGIN_SENTINEL
    if plugin_dir.exists() and sentinel.exists():
        actions.append(
            Action(
                kind=KIND_DELETE,
                description="Remove Lore Cursor plugin directory",
                target=str(plugin_dir),
                summary=f"rmtree (gated on {managed_files.PLUGIN_SENTINEL} sentinel)",
                payload={
                    "path": str(plugin_dir),
                    "recursive": True,
                },
            )
        )

    return actions


def detect_legacy(ctx: InstallContext) -> list[LegacyArtifact]:
    """Cursor never had install.sh artifacts — return empty."""
    return []
