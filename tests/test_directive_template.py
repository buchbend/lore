"""Snapshot test for the loaded directive lines.

Originally a byte-equivalence guard for the hoist from a module-level
constant to ``templates/integration-rules/default.md``. The snapshot is
intentionally hand-edited when the directive content evolves — bumping
the snapshot is a deliberate act, not a drift. New entries get appended
here so unrelated tests keep asserting on a stable shape.
"""

from __future__ import annotations

from lore_cli.hooks import _load_directive_lines

#: Current expected output of the directive loader. Update in lockstep
#: with ``lore_core/templates/integration-rules/default.md``.
EXPECTED_DIRECTIVE_LINES = [
    "## Directives",
    (
        "- **Vault first.** Unfamiliar project term, concept, decision, or "
        "wikilink? Call `lore_search` (MCP) before asking the user. "
        "Asking about a wikilinked term without searching first is a bug."
    ),
    "",
]


def test_loader_matches_expected_snapshot():
    assert _load_directive_lines() == EXPECTED_DIRECTIVE_LINES


def test_module_level_attribute_still_resolves():
    """`from lore_cli.hooks import LORE_DIRECTIVE_LINES` must keep working
    via the __getattr__ shim, so external callers don't break."""
    from lore_cli import hooks

    assert hooks.LORE_DIRECTIVE_LINES == EXPECTED_DIRECTIVE_LINES


def test_load_directive_lines_returns_empty_when_template_missing(monkeypatch):
    """A stale install (templates not bundled in the wheel) used to
    crash SessionStart with FileNotFoundError. After the shield work,
    the loader degrades to an empty list — banner survives, top-level
    shield surfaces the actionable hint."""
    from pathlib import Path

    from lore_cli import hooks

    # Point the directive path at a path that doesn't exist; read_text
    # raises FileNotFoundError, which the loader must swallow.
    monkeypatch.setattr(
        hooks, "_DIRECTIVE_PATH", Path("/nonexistent/templates/integration-rules/default.md")
    )
    assert hooks._load_directive_lines() == []


def test_session_start_shield_catches_unexpected_exception(monkeypatch, capsys):
    """The top-level shield must convert a hook crash into a friendly
    `systemMessage` envelope and exit cleanly — Claude Code never sees
    a Python traceback for a hook failure."""
    import json

    from lore_cli import hooks

    # Force the inner work to blow up in a way no local try/except is
    # set up to handle.
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated catastrophic failure deep in _session_start")

    monkeypatch.setattr(hooks, "_session_start", _boom)
    # Skip the curator-mode and session-off short-circuits; use a real cwd.
    monkeypatch.setattr(hooks, "_in_curator_mode", lambda: False)
    monkeypatch.setattr(hooks, "_session_off_all", lambda: False)
    monkeypatch.setattr(hooks, "_read_hook_payload", lambda: {})
    monkeypatch.setattr(hooks, "_resolve_cwd", lambda _explicit: "/tmp")

    # Invoke the wrapped command directly. The shield must not re-raise.
    hooks.cmd_session_start(cwd="/tmp", plain=False, probe=True)

    out = capsys.readouterr().out
    envelope = json.loads(out)
    # SessionStart envelope splits a one-liner banner (`systemMessage`,
    # shown in the transcript) from the full body (`additionalContext`,
    # injected for the agent). Both should name the failure; the full
    # body carries the actionable fix hints.
    assert "systemMessage" in envelope
    assert "lore SessionStart hook failed" in envelope["systemMessage"]
    assert "RuntimeError" in envelope["systemMessage"]

    full = envelope["hookSpecificOutput"]["additionalContext"]
    assert "RuntimeError" in full
    # Actionable hints are the whole point of the shield.
    assert "lore install --upgrade" in full
    assert "lore doctor" in full
