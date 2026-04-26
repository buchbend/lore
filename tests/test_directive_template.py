"""Phase B byte-equivalence: hoisting LORE_DIRECTIVE_LINES from a
module-level constant in hooks.py to templates/integration-rules/default.md
must not change a single byte the agent sees.

This test snapshots the pre-hoist constant (frozen here in the test)
and asserts the new template loader returns the same list.
"""

from __future__ import annotations

from lore_cli.hooks import _load_directive_lines

# Pre-hoist snapshot of LORE_DIRECTIVE_LINES from
# `lib/lore_cli/hooks.py:173-179` (commit 7a4da73 baseline).
EXPECTED_PRE_HOIST = [
    "## Directives",
    (
        "- **Vault first.** Unfamiliar project term, concept, decision, or "
        "wikilink? Call `lore_search` (MCP) before asking the user. "
        "Asking about a wikilinked term without searching first is a bug."
    ),
    "",
]


def test_loader_matches_pre_hoist_constant():
    assert _load_directive_lines() == EXPECTED_PRE_HOIST


def test_module_level_attribute_still_resolves():
    """`from lore_cli.hooks import LORE_DIRECTIVE_LINES` must keep working
    via the __getattr__ shim, so external callers don't break."""
    from lore_cli import hooks

    assert hooks.LORE_DIRECTIVE_LINES == EXPECTED_PRE_HOIST
