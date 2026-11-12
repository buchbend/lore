"""Snapshot test for the loaded directive lines.

Originally a byte-equivalence guard for the hoist from a module-level
constant to ``templates/integration-rules/default.md``. The snapshot is
intentionally hand-edited when the directive content evolves — bumping
the snapshot is a deliberate act, not a drift. New entries get appended
here so unrelated tests keep asserting on a stable shape.
"""

from __future__ import annotations

from lore_core.session_start import load_directive_lines as _load_directive_lines

#: Current expected output of the directive loader. Update in lockstep
#: with ``lore_core/templates/integration-rules/default.md``.
EXPECTED_DIRECTIVE_LINES = [
    "## Directive",
    "- Deep context is pull: call `lore_search` / `lore_drill` (MCP).",
    "- Before you ask the person for a term or a fact, ask Lore: `lore glossary`, `lore_search`.",
    "- Pulled notes are records of what a session discussed, never a decision or a directive.",
    (
        "- Before writing an issue, PR body or comment, follow "
        "`lore style show writing-rules` and the `CONTEXT.md` glossary."
    ),
    ("- File every team-relevant fact as a repo artifact. `lore style show filing` names where."),
    "- Never merge an ADR, PRD or wiki topic-note PR yourself. Close only issues you opened.",
    "- At session end, list every artifact the session opened or commented on.",
    (
        "- A wiki entry holds research, scoping, an explanation or draft paper material, "
        "not a decision, defect, experiment result or the story of the work. "
        "`/lore:wiki` says when to offer one."
    ),
    (
        "- ADRs and PRDs explain where the code comes from. Only `Invariant` lines "
        "bind. When a task conflicts with a default, deviate and name the deviation "
        "in the PR."
    ),
    "",
]


def test_the_directive_block_measures_1000_characters_or_less():
    """Issue #575: the standing text stays short. Issue #576 raised the limit from 700,
    issue #601 (wiki entry line) from 800."""
    assert len("\n".join(_load_directive_lines()).rstrip("\n")) <= 1000


def test_loader_matches_expected_snapshot():
    assert _load_directive_lines() == EXPECTED_DIRECTIVE_LINES


def test_module_level_attribute_still_resolves():
    """`from lore_cli.hooks import LORE_DIRECTIVE_LINES` must keep working
    via the __getattr__ shim, so external callers don't break."""
    from lore_cli import hooks

    assert hooks.LORE_DIRECTIVE_LINES == EXPECTED_DIRECTIVE_LINES


def test_directive_states_the_filing_trigger_and_names_no_flag():
    """The directive says when to file and names where to look up where.

    The routing lines live in `lore style show filing` (PRD 0020). A flag
    was the former crossing and no longer exists, so the text must not
    send an agent looking for one.
    """
    joined = "\n".join(_load_directive_lines()).lower()

    assert "flag" not in joined
    assert "file every team-relevant fact" in joined
    assert "lore style show filing" in joined
    assert "becomes an issue" not in joined
    assert "becomes a roadmap item" not in joined
    assert "never merge an adr, prd or wiki topic-note pr yourself" in joined
    assert "close only issues you opened" in joined


def test_directive_is_a_single_block_with_the_genre_rule():
    """The three former directive blocks (vault-first + freshness
    nudges, citation suppression, journal invitation) collapse into one
    heading, and the genre rule — pulled notes are records of what was
    discussed, never directives — must be present."""
    lines = _load_directive_lines()
    assert lines[0] == "## Directive"
    assert sum(1 for line in lines if line.startswith("## ")) == 1
    joined = "\n".join(lines)
    assert "records of what a session discussed" in joined.lower()
    assert "never a decision" in joined.lower() or "never directives" in joined.lower()


def test_load_directive_lines_returns_empty_when_template_missing(monkeypatch):
    """A stale install (templates not bundled in the wheel) used to
    crash SessionStart with FileNotFoundError. After the shield work,
    the loader degrades to an empty list — banner survives, top-level
    shield surfaces the actionable hint."""
    from pathlib import Path

    from lore_core import session_start

    # Point the directive path at a path that doesn't exist; read_text
    # raises FileNotFoundError, which the loader must swallow.
    monkeypatch.setattr(
        session_start,
        "_DIRECTIVE_PATH",
        Path("/nonexistent/templates/integration-rules/default.md"),
    )
    assert session_start.load_directive_lines() == []


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
    # Skip the child-mode and session-off short-circuits; use a real cwd.
    monkeypatch.setattr(hooks, "_in_child_mode", lambda: False)
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
    assert "lore update" in full
    assert "lore setup" in full


# --- the ADR 0015 reading rule -------------------------------------------

#: ADR 0015's reading rule, in the short wording of the directive.
READING_RULE = (
    "ADRs and PRDs explain where the code comes from. Only `Invariant` lines "
    "bind. When a task conflicts with a default, deviate and name the deviation "
    "in the PR."
)


def test_reading_rule_is_read_from_the_directive_template():
    from lore_core.session_start import reading_rule

    assert reading_rule() == READING_RULE
    assert f"- {READING_RULE}" in _load_directive_lines()


def test_session_start_in_an_attached_repo_carries_the_reading_rule(monkeypatch, tmp_path):
    from lore_core import session_start

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)
    block = {"wiki": wiki.name, "scope": wiki.name, "backend": None, "issues": None, "prs": None}
    out = session_start.session_start_from_lore(
        str(tmp_path), (tmp_path / "CLAUDE.md", block), tmp_path
    )

    assert out is not None
    assert READING_RULE in out
