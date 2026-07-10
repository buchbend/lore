#!/usr/bin/env python3
"""Per-repo epic-merge policy: target branch + deploy gate (peer of
``roadmap_validator.py``).

``/orchestrate-epic`` must resolve two repo facts deterministically at Map
time — never guess them from prose:

- **target_branch** — where the epic branch is cut from and where it lands.
  ``develop`` if that branch exists on the repo's ``origin`` remote, else
  ``main``. The workflow never stands ``develop`` up itself; its absence is a
  deliberate "no deploy pipeline here" signal, so the default is ``main``.
- **deploy_gate** — whether the final epic->target merge needs one human
  confirmation before it lands. True iff the repo's ``AGENTS.md`` carries a
  ``## Epic merge policy`` section containing the line
  ``epic-merge-policy: confirm``. Absent (the default) → fully autonomous.

Standard library + ``git`` only; no GitHub API. ``git ls-remote`` is the
ground truth for "present on the remote"; if there is no ``origin`` or it is
unreachable, that resolves to the safe default (``main``), matching the
skill's documented fallback.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Deploy-gate contract, scoped to its own section so the marker quoted
# elsewhere in AGENTS.md prose can never trip the gate.
_POLICY_HEADING = "## Epic merge policy"
_DEPLOY_GATE_MARKER = "epic-merge-policy: confirm"


@dataclass(frozen=True)
class EpicPolicy:
    """Resolved policy for one repo."""

    target_branch: str
    deploy_gate: bool


def _remote_has_develop(repo_root: Path) -> bool:
    """True iff ``develop`` exists on the ``origin`` remote. Any failure
    (no origin, not a git repo, unreachable) is treated as absent."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "develop"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() != ""


def _section_has_marker(text: str) -> bool:
    """True iff a ``## Epic merge policy`` section contains the deploy-gate
    marker line. Scoping: from the heading until the next ``## `` heading."""
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.casefold() == _POLICY_HEADING.casefold()
            continue
        if in_section and stripped.casefold() == _DEPLOY_GATE_MARKER.casefold():
            return True
    return False


def _read_deploy_gate(repo_root: Path) -> bool:
    try:
        text = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _section_has_marker(text)


def resolve_epic_policy(repo_root: Path) -> EpicPolicy:
    """Resolve the epic-merge policy for the repo rooted at *repo_root*."""
    return EpicPolicy(
        target_branch="develop" if _remote_has_develop(repo_root) else "main",
        deploy_gate=_read_deploy_gate(repo_root),
    )
