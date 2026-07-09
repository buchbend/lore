"""The tier table itself — one column per registered host.

Claude Code's column is authoritative (ported from ccat-agent-workflow's
MODEL-TIERS.md). Other hosts are PROVISIONAL best-guess seeds — nobody
has exercised the plugin there yet; update as each host gets real
mileage. See ``docs/model-tiers.md``.
"""

from __future__ import annotations

TIER_ORDER: tuple[str, ...] = ("frontier", "strong", "mid", "cheap")

TABLE: dict[str, dict[str, str]] = {
    "claude": {
        "frontier": "claude-opus-4-8",
        "strong": "claude-opus-4-8",
        "mid": "claude-sonnet-5",
        "cheap": "claude-haiku-4-5",
    },
    # provisional — no mileage on this host yet, best guess only.
    "cursor": {
        "frontier": "claude-opus-4-8",
        "strong": "claude-opus-4-8",
        "mid": "claude-sonnet-5",
        "cheap": "claude-haiku-4-5",
    },
}
