"""Repo workflow-onboarding scaffold (PRD 0003 / sub-issue #171).

Ported from ccat-agent-workflow's `scripts/ccat_workflow_init.py`, which used
to be a separate `ccat-workflow-init` onboarding entry point. Lore has exactly
one onboarding command per repo (`lore attach`); this module supplies the
step that command calls, it is never a subcommand of its own.

Idempotent: safe to re-run. Creates only what is missing; never overwrites
existing files. Stdlib-only, no GitHub I/O.

What it does:
1. Migrate CLAUDE.md -> AGENTS.md (if CLAUDE.md exists and isn't already the
   shim), then replace CLAUDE.md with a one-line `@AGENTS.md` import shim.
2. Create docs/prd/index.md and docs/adr/index.md (toctree stubs) if absent.
3. Ensure docs/index.md exists and wires both prd/index and adr/index into
   its first toctree block, idempotently.

Deliberately out of scope (per PRD 0003, a sibling slice, #170): the
.claude/settings.json permissions allowlist and hook wiring. This module
never touches settings.json.
"""

from __future__ import annotations

from pathlib import Path

from lore_workflow.prd_docs import wire_toctree

# The one-line shim that CLAUDE.md becomes after migration.
CLAUDE_MD_SHIM = "@AGENTS.md\n"
SHIM_SENTINEL = "@AGENTS.md"

_ADR_INDEX_STUB = """\
# Architecture Decision Records

Decision records in MADR-lite form
(Context · Decision · Consequences / Trade-offs · Alternatives considered).

```{toctree}
:maxdepth: 1

```
"""

_PRD_INDEX_STUB = """\
# Product Requirements Documents

PRDs are the source of truth for decisions. Each PRD lives at
`docs/prd/NNNN-kebab.md` (MyST Markdown) and is linked from its GitHub epic.

```{toctree}
:maxdepth: 1

```
"""

_DOCS_INDEX_STUB = """\
# Documentation

```{toctree}
:maxdepth: 2

adr/index
prd/index
```
"""


def _migrate_claude_md(target: Path) -> bool:
    """Move CLAUDE.md content into AGENTS.md; replace CLAUDE.md with the shim.

    Idempotency: if CLAUDE.md is already the shim, do nothing. If AGENTS.md
    already exists, never overwrite it (migration already happened, or the
    user authored it). Returns whether anything changed.
    """
    claude_md = target / "CLAUDE.md"
    agents_md = target / "AGENTS.md"

    if claude_md.exists():
        current = claude_md.read_text(encoding="utf-8")
        already_shim = current.strip() == SHIM_SENTINEL
    else:
        current = ""
        already_shim = False

    if already_shim:
        if not agents_md.exists():
            agents_md.write_text("", encoding="utf-8")
            return True
        return False

    changed = False
    if not agents_md.exists():
        agents_md.write_text(current, encoding="utf-8")
        changed = True
    if not claude_md.exists() or claude_md.read_text(encoding="utf-8") != CLAUDE_MD_SHIM:
        claude_md.write_text(CLAUDE_MD_SHIM, encoding="utf-8")
        changed = True
    return changed


def _create_index_stub(index_path: Path, stub: str) -> bool:
    if index_path.exists():
        return False
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(stub, encoding="utf-8")
    return True


def _wire_docs_index(target: Path) -> bool:
    """Ensure docs/index.md exists and references both prd/index and adr/index."""
    docs_index = target / "docs" / "index.md"
    changed = _create_index_stub(docs_index, _DOCS_INDEX_STUB)
    if changed:
        return True  # Stub already carries both entries.

    before = docs_index.read_text(encoding="utf-8")
    wire_toctree(docs_index, "adr/index", stub=_DOCS_INDEX_STUB)
    wire_toctree(docs_index, "prd/index", stub=_DOCS_INDEX_STUB)
    return docs_index.read_text(encoding="utf-8") != before


def scaffold(target: Path) -> bool:
    """Onboard the repo at *target* into the workflow conventions.

    Returns whether anything was written (for state recording / caller
    messaging) — a second call on an unchanged repo returns False.
    """
    target = Path(target).resolve()

    changed = _migrate_claude_md(target)
    changed |= _create_index_stub(target / "docs" / "adr" / "index.md", _ADR_INDEX_STUB)
    changed |= _create_index_stub(target / "docs" / "prd" / "index.md", _PRD_INDEX_STUB)
    changed |= _wire_docs_index(target)
    return changed
