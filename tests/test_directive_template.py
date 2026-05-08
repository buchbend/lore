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
    "### Lore freshness — in-passing nudge",
    "",
    (
        "When you cite or rely on a note that Lore returned with "
        "`freshness.status: \"stale-candidate\"`, end your turn with a "
        "one-line check that quotes the specific claim you used:"
    ),
    "",
    "`(› [[<note>]] said \"<claim>\" — <reason>; still current?)`",
    "",
    "Rules:",
    "- Once per note per session. Track via session-local memory; do not retry.",
    (
        "- Silence semantics: if the user types past the nudge without "
        "responding, do nothing. Never auto-write a verdict."
    ),
    (
        "- If the user replies with a verdict (\"yes still good\" / "
        "\"no, stale because X\" / \"split it — first part is stale\"), "
        "call the `lore_verdict` MCP tool with the appropriate arguments "
        "before continuing your substantive answer."
    ),
    (
        "- On a \"split it\" verdict, offer to edit the note: move the "
        "fresh content out, mark the rest stale via `lore_verdict`. The "
        "user confirms before any write."
    ),
    "",
    "### Lore freshness — dynamic escalation",
    "",
    (
        "If a retrieved note returns `freshness.status: \"confirmed\"` "
        "but you observe a *concrete claim-vs-claim contradiction* — "
        "claim A in this note vs claim B in another retrieved hit, or "
        "vs a fact the user just stated this session — treat the note "
        "as `stale-candidate` for this turn and emit the in-passing "
        "nudge."
    ),
    "",
    "Do NOT escalate on:",
    "- Silence (the note doesn't mention X).",
    "- Topic mismatch (the note discusses an adjacent thing).",
    "- Vibe-level disagreement without two stated claims to compare.",
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
