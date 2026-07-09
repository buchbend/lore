"""Model-tier resolution: semantic tier -> concrete model, per host.

Skills/prompts should never name a concrete model — they name a
*semantic* tier (how much capability a step needs) and the harness
maps that to whatever model actually runs. See ``docs/model-tiers.md``
for the full rules (ordinal/collapse, fallback, cheap reservation),
ported from ccat-agent-workflow's ``MODEL-TIERS.md``.

``resolve_tier`` is the single entry point: detect the current host
(unless given), look up the tier in ``TABLE`` for that host, and let a
user config override (``.lore/config.yml`` -> ``tiers.overrides``) win
if present. Unknown tier or unknown host raises ``TierResolutionError``
loudly rather than silently falling back.
"""

from __future__ import annotations

import os
from pathlib import Path

from lore_core.tiers.table import TABLE, TIER_ORDER


class TierResolutionError(Exception):
    """Raised when a tier or host can't be resolved — never silent-fallback."""


def detect_host() -> str:
    """Detect which host is currently running lore, via env-var signals.

    Mirrors the CWD-resolution precedence already used elsewhere in lore
    (``$CLAUDE_PROJECT_DIR`` / ``$CURSOR_PROJECT_DIR``, see
    ``lore_cli.hooks``). Raises loudly rather than guessing when no
    known host announces itself — silent defaulting would resolve
    tiers to the wrong model without any signal that happened.
    """
    if any(
        os.environ.get(var)
        for var in ("CLAUDECODE", "CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID", "CLAUDE_CODE_EXECPATH")
    ):
        return "claude"
    if os.environ.get("CURSOR_PROJECT_DIR"):
        return "cursor"
    raise TierResolutionError(
        "unable to detect host: no known host env var is set "
        "(CLAUDECODE, CLAUDE_PROJECT_DIR, CURSOR_PROJECT_DIR, ...)"
    )


def resolve_tier(tier: str, host: str | None = None, lore_root: Path | None = None) -> str:
    """Resolve a semantic tier to a concrete model for the given/detected host.

    ``lore_root`` lets callers pin the vault to read overrides from
    (mainly for tests); defaults to :func:`lore_core.config.get_lore_root`.
    """
    if tier not in TIER_ORDER:
        raise TierResolutionError(f"unknown tier {tier!r}; known tiers: {', '.join(TIER_ORDER)}")
    resolved_host = host or detect_host()
    if resolved_host not in TABLE:
        raise TierResolutionError(
            f"unknown host {resolved_host!r}; known hosts: {', '.join(sorted(TABLE))}"
        )

    from lore_core.config import get_lore_root
    from lore_core.root_config import load_root_config

    root = lore_root if lore_root is not None else get_lore_root()
    cfg = load_root_config(root)
    override = cfg.tiers.overrides.get(resolved_host, {}).get(tier)
    if override:
        return override
    return TABLE[resolved_host][tier]
