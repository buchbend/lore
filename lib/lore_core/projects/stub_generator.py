"""Compose + write a project note from harness file contents.

Output path: ``wiki/<wiki>/projects/<repo-slug>.md``.

Contracts pinned in the implementation plan:

* **No HTML markers** for auto-section preservation. Regeneration is
  by canonical heading text — the four sections at fixed positions
  (``## Overview``, ``## Conventions``, ``## Architecture``, ``## Key
  decisions``) are owned by the generator; everything else (including
  user-added headings between them) is preserved verbatim.
* **No ``## Active plans`` section in the body.** SessionStart's
  ``_active_plans_for_repo`` is the live channel; an in-body section
  would become a permanent stale snapshot the moment the next plan
  landed (``lore lint`` only regenerates ``_catalog.json`` /
  ``_index.txt``, not project-note bodies).
* **Idempotent** on re-stub: ``was_new=False``, content under canonical
  headings refreshed in place, user content elsewhere preserved.
* **Frontmatter via ``yaml.safe_dump``** (consistent with plan writer).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Iterable

import yaml

from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter, strip_frontmatter

from .harness_parser import HarnessSections, parse_harness_files

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StubResult:
    """Outcome of one ``stub_project_note`` call."""

    path: Path
    was_new: bool
    sections_written: list[str]


CANONICAL_SECTIONS: tuple[str, ...] = (
    "Overview",
    "Conventions",
    "Architecture",
    "Key decisions",
)


# Files we look for in the repo root. Order matters only for documentation —
# extraction is independent per file.
_HARNESS_FILES = (
    ("README.md", "readme"),
    ("CLAUDE.md", "claude_md"),
    ("AGENTS.md", "agents_md"),
    (".cursorrules", "cursorrules"),
    (".github/copilot-instructions.md", "copilot_instructions"),
    ("pyproject.toml", "pyproject_text"),
    ("package.json", "package_json_text"),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stub_project_note(
    *,
    wiki_root: Path,
    repo_root: Path,
    repo_slug: str,
    scope: str | None = None,
    today: _date | None = None,
) -> StubResult:
    """Compose and write the project note for ``repo_slug`` into ``wiki_root``.

    ``wiki_root`` is the *wiki* root (e.g. ``$LORE_ROOT/wiki/private``),
    not the LORE_ROOT itself.

    Idempotent on re-call: if the note exists, its canonical-heading
    sections refresh; user content under any other heading is
    preserved.
    """
    today = today or _date.today()

    # Read whichever harness files are present in the repo.
    sources = _read_harness_sources(repo_root)
    sections = parse_harness_files(
        fallback_repo_slug=repo_slug,
        **sources,
    )

    target_path = projects_dir(wiki_root) / f"{_safe_slug(repo_slug)}.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        return _refresh_in_place(
            target_path=target_path,
            repo_slug=repo_slug,
            scope=scope,
            sections=sections,
            today=today,
        )

    return _file_fresh(
        target_path=target_path,
        repo_slug=repo_slug,
        scope=scope,
        sections=sections,
        today=today,
    )


def projects_dir(wiki_root: Path) -> Path:
    return wiki_root / "projects"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_harness_sources(repo_root: Path) -> dict[str, str]:
    """Read each known harness file (if present) and return the kwargs dict."""
    out: dict[str, str] = {}
    for relpath, kwarg in _HARNESS_FILES:
        path = repo_root / relpath
        if path.exists() and path.is_file():
            try:
                out[kwarg] = path.read_text()
            except OSError:
                continue
    return out


def _safe_slug(slug: str) -> str:
    """Take the basename of an ``org/name`` slug for filesystem safety."""
    if "/" in slug:
        return slug.rsplit("/", 1)[1]
    return slug


def _file_fresh(
    *,
    target_path: Path,
    repo_slug: str,
    scope: str | None,
    sections: HarnessSections,
    today: _date,
) -> StubResult:
    fm = _build_fresh_frontmatter(
        repo_slug=repo_slug,
        scope=scope,
        sections=sections,
        today=today,
    )
    body, written = _render_body(repo_slug=repo_slug, sections=sections)
    text = _render_markdown(fm, body)
    atomic_write_text(target_path, text)
    return StubResult(
        path=target_path,
        was_new=True,
        sections_written=written,
    )


def _refresh_in_place(
    *,
    target_path: Path,
    repo_slug: str,
    scope: str | None,
    sections: HarnessSections,
    today: _date,
) -> StubResult:
    existing_text = target_path.read_text()
    existing_fm = parse_frontmatter(existing_text)
    existing_body = strip_frontmatter(existing_text)

    # Build new section bodies; merge into the existing body by canonical heading.
    new_section_bodies = _section_bodies_map(sections)
    merged_body = _merge_canonical_sections(existing_body, new_section_bodies)

    # Frontmatter: refresh last_reviewed + system fields; preserve user
    # additions (description if user edited it, custom tags, scope).
    fm = dict(existing_fm)
    fm["schema_version"] = 2
    fm["type"] = "project"
    fm["repo"] = repo_slug
    fm["last_reviewed"] = today.isoformat()
    if scope is not None:
        fm.setdefault("scope", scope)
    # Description: only fill if absent — preserves user edits.
    if not fm.get("description"):
        fm["description"] = sections.description
    fm.setdefault("created", today.isoformat())
    fm.setdefault("tags", ["project"])

    text = _render_markdown(fm, merged_body)
    atomic_write_text(target_path, text)
    return StubResult(
        path=target_path,
        was_new=False,
        sections_written=[s for s in CANONICAL_SECTIONS if new_section_bodies.get(s)],
    )


def _build_fresh_frontmatter(
    *,
    repo_slug: str,
    scope: str | None,
    sections: HarnessSections,
    today: _date,
) -> dict:
    fm: dict = {
        "schema_version": 2,
        "type": "project",
        "repo": repo_slug,
        "created": today.isoformat(),
        "last_reviewed": today.isoformat(),
        "description": sections.description,
        "tags": ["project"],
    }
    if scope is not None:
        fm["scope"] = scope
    return fm


def _render_body(
    *, repo_slug: str, sections: HarnessSections
) -> tuple[str, list[str]]:
    """Render fresh project-note body. Returns ``(body, written_section_names)``."""
    section_bodies = _section_bodies_map(sections)
    written: list[str] = []
    parts: list[str] = []
    parts.append(f"# Project: {_display_name(repo_slug)}")
    parts.append("")
    for heading in CANONICAL_SECTIONS:
        body = section_bodies.get(heading, "")
        parts.append(f"## {heading}")
        parts.append("")
        if body:
            parts.append(body.strip())
            written.append(heading)
        else:
            parts.append(_placeholder_for(heading))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n", written


def _section_bodies_map(sections: HarnessSections) -> dict[str, str]:
    """Map canonical heading → rendered body string (empty for missing sections)."""
    return {
        "Overview": sections.overview,
        "Conventions": sections.conventions,
        "Architecture": sections.architecture,
        "Key decisions": "",  # populated by curator passes (Phase 4+); empty for v1
    }


def _placeholder_for(heading: str) -> str:
    if heading == "Architecture":
        return "_No architecture document detected._"
    if heading == "Key decisions":
        return "_No decision notes tagged with this repo yet._"
    if heading == "Overview":
        return "_No README found in the attached repo._"
    if heading == "Conventions":
        return "_No CLAUDE.md / AGENTS.md / .cursorrules detected._"
    return ""


def _merge_canonical_sections(
    existing_body: str, new_section_bodies: dict[str, str]
) -> str:
    """Replace the body of each canonical heading; preserve everything else.

    Walks the existing body line by line. When a canonical-heading line
    is encountered, captures the lines up to the next ATX heading (any
    level) and substitutes the rendered new body. Non-canonical
    sections (and any free-form intro before the first heading) flow
    through untouched.

    If a canonical section is *missing* from the existing body, it is
    appended at the end.
    """
    lines = existing_body.split("\n")
    out_lines: list[str] = []
    i = 0
    seen_canonical: set[str] = set()

    while i < len(lines):
        line = lines[i]
        canonical_name = _canonical_heading_name(line)
        # Only the FIRST occurrence of a canonical heading is regenerated.
        # Duplicates (user accidentally pasted ``## Overview`` twice) are
        # preserved verbatim as user content — otherwise the second pass
        # of a re-stub would emit two regenerated bodies for the same
        # heading.
        if canonical_name is not None and canonical_name not in seen_canonical:
            seen_canonical.add(canonical_name)
            # Emit the heading, blank line, fresh content, blank line.
            out_lines.append(f"## {canonical_name}")
            out_lines.append("")
            new_body = new_section_bodies.get(canonical_name, "")
            if new_body.strip():
                out_lines.append(new_body.strip())
            else:
                out_lines.append(_placeholder_for(canonical_name))
            out_lines.append("")
            # Skip the existing body of this section: walk until next ATX heading.
            i += 1
            while i < len(lines):
                m_line = lines[i]
                if _is_atx_heading(m_line):
                    break
                i += 1
            continue
        out_lines.append(line)
        i += 1

    # Append any canonical sections that didn't appear in the existing body.
    for heading in CANONICAL_SECTIONS:
        if heading in seen_canonical:
            continue
        if out_lines and out_lines[-1] != "":
            out_lines.append("")
        out_lines.append(f"## {heading}")
        out_lines.append("")
        new_body = new_section_bodies.get(heading, "")
        if new_body.strip():
            out_lines.append(new_body.strip())
        else:
            out_lines.append(_placeholder_for(heading))
        out_lines.append("")

    return "\n".join(out_lines).rstrip() + "\n"


def _is_atx_heading(line: str) -> bool:
    return len(line) > 0 and line.lstrip().startswith("#") and " " in line.lstrip()


def _canonical_heading_name(line: str) -> str | None:
    """Return the canonical section name for ``## <name>`` lines (level 2 only)."""
    stripped = line.strip()
    if not stripped.startswith("## "):
        return None
    name = stripped[3:].strip()
    for canonical in CANONICAL_SECTIONS:
        if name.lower() == canonical.lower():
            return canonical
    return None


def _display_name(repo_slug: str) -> str:
    """Drop the org prefix for display, keep the rest as-is."""
    if "/" in repo_slug:
        return repo_slug.rsplit("/", 1)[1]
    return repo_slug


def _render_markdown(fm: dict, body: str) -> str:
    dumped = yaml.safe_dump(
        fm, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).strip()
    body = body.rstrip()
    return f"---\n{dumped}\n---\n\n{body}\n"
