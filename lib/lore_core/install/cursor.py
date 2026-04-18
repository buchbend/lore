"""Cursor installer module.

Two file mutations + one check:

  1. Merge `mcpServers.lore` into `<cursor-config-dir>/mcp.json`
     with `_lore_schema_version: "1"` for future migrations.
  2. Write `<cursor-rules-dir>/lore.md` with the vault-first directive
     wrapped in `<!-- lore-managed-start -->` … `<!-- lore-managed-end -->`
     markers so user-appended content survives uninstall.
  3. Verify `lore` is on PATH (Cursor's MCP client subprocess-spawns
     the server via that name).

Per-platform path resolution lives in `_helpers.cursor_config_dir()`
and `cursor_rules_dir()`; macOS + Linux supported in v1, Windows
refused.
"""

from __future__ import annotations

from pathlib import Path

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

SCHEMA_VERSION = "1"


def _read_directive_body() -> str:
    """Pull the canonical vault-first directive out of templates/host-rules/default.md."""
    # Resolve the template at call time, not import time, so tests
    # can monkeypatch the directive path.
    base = Path(__file__).resolve().parent.parent.parent.parent
    return (base / "templates" / "host-rules" / "default.md").read_text().rstrip("\n")


def plan(ctx: InstallContext) -> list[Action]:
    """Return Actions to install Lore for Cursor."""
    actions: list[Action] = []

    config_dir = _helpers.cursor_config_dir()
    rules_dir = _helpers.cursor_rules_dir()
    mcp_path = config_dir / "mcp.json"
    rules_path = rules_dir / "lore.md"

    # 1. Merge MCP server entry. Distinguish absent-version (legacy
    #    or user-authored — silent migrate) from present-but-old
    #    (true schema bump — replace with prompt).
    existing = _read_existing_lore_entry(mcp_path)
    new_value = _helpers.lore_mcp_entry(SCHEMA_VERSION)
    if existing is None:
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
    elif existing.get(_helpers.SCHEMA_VERSION_KEY) is None:
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
    elif existing.get(_helpers.SCHEMA_VERSION_KEY) != SCHEMA_VERSION:
        # Present-but-old schema — true bump, needs explicit prompt.
        actions.append(
            Action(
                kind=KIND_REPLACE,
                description="Upgrade mcpServers.lore schema",
                target=str(mcp_path),
                summary=(
                    f"replace mcpServers.lore "
                    f"({existing.get(_helpers.SCHEMA_VERSION_KEY)} "
                    f"→ {SCHEMA_VERSION})"
                ),
                payload={
                    "path": str(mcp_path),
                    "key_path": ["mcpServers", "lore"],
                    "old_value": existing,
                    "new_value": new_value,
                    "reason": (
                        f"_lore_schema_version "
                        f"{existing.get(_helpers.SCHEMA_VERSION_KEY)} "
                        f"→ {SCHEMA_VERSION}"
                    ),
                },
            )
        )
    # else: same schema_version — no action needed (the upgrade case
    # — the dispatcher's `lore install upgrade` reports it as a
    # no-op).

    # 2. Write the rules file (managed-marker wrapped). If the file
    #    has no managed markers, treat as user-authored — the
    #    dispatcher will refuse to clobber without --force.
    body = _read_directive_body()
    full_content = (
        f"{_helpers.MANAGED_BLOCK_START}\n{body}\n{_helpers.MANAGED_BLOCK_END}\n"
    )
    if not rules_path.exists():
        actions.append(
            Action(
                kind=KIND_NEW,
                description="Write Lore directive to Cursor rules",
                target=str(rules_path),
                summary=f"new file (~{len(body.splitlines())} lines, "
                "vault-first directive)",
                payload={
                    "path": str(rules_path),
                    "content": full_content,
                },
            )
        )
    else:
        existing_managed = _helpers.managed_block_content(rules_path)
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
        elif _helpers.content_hash(existing_managed) != _helpers.content_hash(body):
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
    if rules_path.exists() and _helpers.managed_block_content(rules_path) is not None:
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
    return actions


def detect_legacy(ctx: InstallContext) -> list[LegacyArtifact]:
    """Cursor never had install.sh artifacts — return empty."""
    return []
