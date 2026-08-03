"""Workflow docs ↔ skill/MCP surface honesty (sub-issue #173).

The migrated conventions/how-to docs under ``docs/`` cite
``lore-workflow:<skill>`` skill names and reference the
``seed-epic → orient → ... → document-epic`` chain by name. Nothing catches
it if a skill is renamed or removed and the docs are not updated to match —
this test does.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "lore-workflow" / "skills"
MCP_SERVER = REPO_ROOT / "lib" / "lore_mcp" / "server.py"
DOCS_FILES = (
    sorted((REPO_ROOT / "docs").rglob("*.md"))
    + [REPO_ROOT / "README.md", REPO_ROOT / "CONTEXT.md"]
)

SKILL_REF_RE = re.compile(r"lore-workflow:([a-z][a-z-]*)")
CHAIN_RE = re.compile(
    r"seed-epic\s*→\s*orient\s*→\s*grilling\s*→\s*to-epic\s*→\s*orchestrate-epic\s*→\s*document-epic"
)


def _shipped_skill_names() -> set[str]:
    return {p.name for p in SKILLS_ROOT.iterdir() if p.is_dir()}


def test_lore_workflow_skill_references_resolve_to_shipped_skills() -> None:
    """Every ``lore-workflow:<name>`` cited in a doc must be a real skill dir.

    ``docs/prd/`` is exempt: PRDs are plans and legitimately cite skills that
    ship later. Drift honesty applies to docs describing the present.
    """
    shipped = _shipped_skill_names()
    failures: list[str] = []
    for path in DOCS_FILES:
        if path.relative_to(REPO_ROOT).parts[:2] == ("docs", "prd"):
            continue
        text = path.read_text(encoding="utf-8")
        for name in SKILL_REF_RE.findall(text):
            if name not in shipped:
                failures.append(
                    f"{path.relative_to(REPO_ROOT)}: cites lore-workflow:{name}, no such skill"
                )
    assert not failures, "Doc/skill drift:\n  " + "\n  ".join(failures)


def test_docs_conventions_chain_matches_readme_chain() -> None:
    """The chain diagram in docs/conventions.md and README.md must match,
    verbatim, so the two don't quietly drift apart.
    """
    conventions = (REPO_ROOT / "docs" / "conventions.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert CHAIN_RE.search(conventions), (
        "docs/conventions.md is missing the canonical chain diagram"
    )
    assert CHAIN_RE.search(readme), "README.md is missing the canonical chain diagram"


def test_context_md_mcp_tool_list_matches_server() -> None:
    """CONTEXT.md's bare ``lore_xxx`` mentions must be exactly the MCP tools
    ``lore_mcp/server.py`` exposes — catches a tool cut from the server but
    still named as live in CONTEXT.md, and a tool the server gained that
    CONTEXT.md never learned about.
    """
    context_md = (REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`(lore_[a-z_]+)`", context_md))

    server_src = MCP_SERVER.read_text(encoding="utf-8")
    exposed = set(re.findall(r'"name":\s*"(lore_[a-z_]+)"', server_src))

    assert documented == exposed, (
        f"CONTEXT.md MCP tool list drifted from {MCP_SERVER.relative_to(REPO_ROOT)} — "
        f"documented but not exposed: {sorted(documented - exposed)}; "
        f"exposed but not documented: {sorted(exposed - documented)}"
    )


def test_no_doc_cites_old_repo_as_authoritative() -> None:
    """Migrated docs must not point at ccatobs/ccat-agent-workflow as the
    source of truth for conventions or tier semantics — that repo is frozen
    and superseded by this migration (PRD 0003).
    """
    migrated = [
        REPO_ROOT / "docs" / "conventions.md",
        REPO_ROOT / "docs" / "model-tiers.md",
        REPO_ROOT / "docs" / "explanation" / "why-tdd-is-enforced.md",
        REPO_ROOT / "docs" / "explanation" / "why-prd-in-repo.md",
        *sorted((REPO_ROOT / "docs" / "how-to").glob("*.md")),
    ]
    failures = [
        str(path.relative_to(REPO_ROOT))
        for path in migrated
        if "ccat-agent-workflow" in path.read_text(encoding="utf-8")
    ]
    assert not failures, "Old-repo reference in migrated docs:\n  " + "\n  ".join(failures)
