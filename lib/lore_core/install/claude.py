"""Claude Code installer module.

Strategy: subprocess `claude plugin install lore@lore` (which wires
hooks/skills/agents/MCP via the manifest in `.claude-plugin/plugin.json`)
plus an optional permissions-merge into `~/.claude/settings.json`.

If `lore` is not on PATH (i.e. the user ran `lore install` from a
fresh clone without first running `pipx install lore`), bootstrap
the Python install via the pipx → uv → pip cascade lifted from
install.sh.

Phase D entry point. Phase C had this as an empty stub.
"""

from __future__ import annotations

from lore_core.install import _helpers
from lore_core.install.base import (
    KIND_CHECK,
    KIND_MERGE,
    KIND_RUN,
    Action,
    InstallContext,
    LegacyArtifact,
)

SCHEMA_VERSION = "1"

_PERMISSION_RULES = (
    "Bash(lore *)",
    # Read on cache + LORE_ROOT/** is added at install-flow time
    # because LORE_ROOT may differ per user; the caller passes the
    # exact strings via `recommended_permissions()`.
)


def recommended_permissions(lore_root: str | None = None) -> list[str]:
    """Permission rules the user is asked to add for friction-free use."""
    rules = list(_PERMISSION_RULES)
    rules.append("Read(~/.cache/lore/**)")
    if lore_root:
        rules.append(f"Read({lore_root}/**)")
    return rules


def plan(ctx: InstallContext) -> list[Action]:
    """Return Actions to install Lore for Claude Code."""
    actions: list[Action] = []

    # 1. Bootstrap: install the lore CLI if it's not already on PATH.
    on_path, _msg = _helpers.check_lore_on_path()
    if not on_path:
        try:
            installer, argv = _helpers.install_self_via(target=ctx.lore_repo)
        except RuntimeError as e:
            actions.append(
                Action(
                    kind=KIND_CHECK,
                    description="lore CLI must be installed",
                    target="lore CLI",
                    summary=str(e),
                    payload={
                        "check": "lore_on_path",
                        "fail_message": str(e),
                    },
                )
            )
        else:
            actions.append(
                Action(
                    kind=KIND_RUN,
                    description=f"Install lore CLI via {installer}",
                    target=f"{installer} (Python install)",
                    summary=f"{installer} install of lore "
                    + ("(editable)" if ctx.lore_repo else "(from PyPI)"),
                    payload={
                        "argv": argv,
                        "timeout": 120,
                        "fallback_message": (
                            "If this fails, install lore manually: "
                            f"{' '.join(argv)}"
                        ),
                    },
                )
            )

    # 2. Wire the plugin via Claude Code's own installer (manifest
    #    at .claude-plugin/plugin.json declares hooks + mcpServers;
    #    skills/agents auto-discover).
    actions.append(
        Action(
            kind=KIND_RUN,
            description="Register the Lore plugin with Claude Code",
            target="claude plugin",
            summary="claude plugin install lore@lore",
            payload={
                "argv": ["claude", "plugin", "install", "lore@lore"],
                "timeout": 60,
                "fallback_message": (
                    "If `claude` isn't on your PATH, run: "
                    "claude plugin install lore@lore"
                ),
            },
        )
    )

    # 3. Verify lore is reachable post-install.
    actions.append(
        Action(
            kind=KIND_CHECK,
            description="Verify lore CLI is callable",
            target="lore CLI",
            summary="shutil.which('lore') returns non-None",
            payload={"check": "lore_on_path"},
        )
    )

    return actions


def uninstall_plan(ctx: InstallContext) -> list[Action]:
    """Actions to remove the Lore plugin from Claude Code.

    Symmetric to install: the inverse of `claude plugin install
    lore@lore` is `claude plugin uninstall lore@lore`. The
    permissions-merge action (if it ran during install) is reversed
    by removing those exact rules from settings.json.
    """
    return [
        Action(
            kind=KIND_RUN,
            description="Unregister the Lore plugin from Claude Code",
            target="claude plugin",
            summary="claude plugin uninstall lore@lore",
            payload={
                "argv": ["claude", "plugin", "uninstall", "lore@lore"],
                "timeout": 60,
                "fallback_message": (
                    "If `claude` isn't on your PATH, run: "
                    "claude plugin uninstall lore@lore"
                ),
            },
        ),
    ]


def detect_legacy(ctx: InstallContext) -> list[LegacyArtifact]:
    """Surface install.sh-era artifacts for Claude Code."""
    return _helpers.detect_install_sh_artifacts(lore_repo=ctx.lore_repo)
