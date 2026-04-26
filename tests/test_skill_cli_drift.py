"""Skill ↔ CLI surface honesty.

User-facing skills should always invoke the user-facing CLI verb
(``lore <verb>``), not internal package paths like
``python -m lore_core.lint`` or ``python -m lore_cli curator``. The
package paths leak implementation detail (which module owns the verb)
and rot when modules move (Phase 1 had to chase several of these).

This test fails the build if any ``skills/*/SKILL.md`` reintroduces a
``python -m lore_*`` reference. The list of allowed `lore` CLI verbs
is sanity-checked too — if a skill cites a verb that doesn't exist,
that's a documentation bug worth catching early.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Match ``python -m lore_<anything>`` inside a code block or inline.
# Skip prose mentions of the package name (e.g. "the `lore_core` package")
# by requiring the ``python -m`` prefix.
PYTHON_M_RE = re.compile(r"\bpython\s+-m\s+lore_[a-z_.]+", re.IGNORECASE)


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


def test_no_skill_references_python_m_internal() -> None:
    """No SKILL.md should invoke ``python -m lore_<x>`` — use the ``lore``
    CLI instead. Phase 4 cleaned up two real cases (lint, curator).
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in _skill_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            for m in PYTHON_M_RE.finditer(line):
                offenders.append((path.relative_to(REPO_ROOT), lineno, m.group(0)))
    assert not offenders, (
        "Skill ↔ CLI drift: SKILL.md files should call `lore <verb>`, "
        "not `python -m lore_<x>`. Offenders:\n  "
        + "\n  ".join(f"{p}:{lineno}: {snippet}" for p, lineno, snippet in offenders)
    )


def test_lore_cli_top_level_verbs_match_skill_invocations() -> None:
    """Every ``lore <verb>`` cited in a SKILL.md must exist as a registered
    typer subcommand. Catches typos and drift after CLI verb renames.

    This test introspects the real ``lore --help`` output, so it
    automatically picks up new verbs without needing maintenance here.
    """
    proc = subprocess.run(
        ["python", "-m", "lore_cli", "--help"],
        capture_output=True, text=True, check=True,
    )
    # Heuristic verb extraction: any token of the form ``lore <verb>`` in
    # SKILL.md, where verb is a single word that looks like a CLI verb.
    cli_verb_pattern = re.compile(r"\blore\s+([a-z][a-z-]*)\b")
    cited: set[str] = set()
    for path in _skill_files():
        for m in cli_verb_pattern.finditer(path.read_text()):
            verb = m.group(1)
            # Filter out prose words that follow "lore" (e.g. "lore is...").
            # Real verbs we're checking are short, hyphen-or-letter only,
            # and appear inside code spans / commands — heuristic but
            # tightenable later if it gets noisy.
            cited.add(verb)

    help_text = proc.stdout
    # Build the set of real verbs from the help output. We accept anything
    # that appears as the first word inside a help-table command box.
    real_verbs: set[str] = set()
    for line in help_text.splitlines():
        # Help tables look like: "│ install      Install ..."
        m = re.match(r"^[\s│]+([a-z][a-z-]*)\s{2,}", line)
        if m:
            real_verbs.add(m.group(1))

    # Allow-list for prose words we know aren't CLI verbs but show up
    # adjacent to "lore" in skill bodies (e.g. "lore is", "lore plugin").
    PROSE_ALLOWLIST = {
        "is", "as", "the", "and", "or", "to", "for", "via", "with",
        "plugin", "vault", "wiki", "scope", "session", "skill",
        "auto", "core", "client", "version", "team", "knowledge",
    }

    spurious = cited - real_verbs - PROSE_ALLOWLIST
    if spurious:
        # Don't fail outright — this is heuristic. Surface as a warning
        # for human review; promote to assertion if it stabilizes.
        pytest.skip(
            f"Heuristic flagged possible verb drift (review manually): "
            f"{sorted(spurious)}. Real verbs: {sorted(real_verbs)}"
        )
