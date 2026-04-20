"""Surface note writer — create curator-authored surface notes.

Writes notes under <wiki_root>/<surface-name-pluralised>/<slug>.md with
YAML frontmatter populated from required fields + sane defaults + caller
extras. Always sets draft:true on Curator B-authored surfaces.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from lore_core.io import atomic_write_text
from lore_core.schema import required_fields_for
from lore_core.surfaces import SurfaceDef, SurfacesDoc


@dataclass
class FiledSurface:
    path: Path
    wikilink: str          # "[[<stem>]]"


def file_surface(
    *,
    surface_name: str,
    title: str,
    body: str,                              # main body text (no frontmatter)
    sources: list[str],                     # wikilinks like "[[2026-04-19-foo]]"
    wiki_root: Path,
    surfaces_doc: SurfacesDoc,
    extra_frontmatter: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> FiledSurface:
    """Write a curator-authored surface note. Always draft:true.

    Path: <wiki_root>/<plural-of-surface-name>/<slug>.md
    Frontmatter: required fields from SURFACES.md (via required_fields_for)
                 + caller-supplied extras + draft:true + synthesis_sources.

    Raises ValueError if a required frontmatter field is unfilled
    after merging extras + defaults.
    """
    now = now or datetime.now(UTC)
    surface_def = _find_surface_def(surfaces_doc, surface_name)
    if surface_def is None:
        raise ValueError(
            f"surface_filer: '{surface_name}' is not declared in SURFACES.md "
            f"({wiki_root}); declared: {[s.name for s in surfaces_doc.surfaces]}"
        )

    subdir = wiki_root / _pluralise(surface_name)
    subdir.mkdir(parents=True, exist_ok=True)

    slug = _slug(title)
    path = subdir / f"{slug}.md"
    counter = 1
    while path.exists():
        counter += 1
        path = subdir / f"{slug}-{counter}.md"

    fm = _build_frontmatter(
        surface_name=surface_name,
        surface_def=surface_def,
        title=title,
        sources=sources,
        wiki_root=wiki_root,
        extra=extra_frontmatter or {},
        now=now,
    )

    text = _render_markdown(fm, body)
    atomic_write_text(path, text)
    return FiledSurface(path=path, wikilink=f"[[{path.stem}]]")


# ---------- helpers ----------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    return s[:60] if s else "surface"


def _pluralise(name: str) -> str:
    if name.endswith("s"):
        return name
    return f"{name}s"


def _find_surface_def(doc: SurfacesDoc, name: str) -> SurfaceDef | None:
    for s in doc.surfaces:
        if s.name == name:
            return s
    return None


def _build_frontmatter(
    *,
    surface_name: str,
    surface_def: SurfaceDef,
    title: str,
    sources: list[str],
    wiki_root: Path,
    extra: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Build the frontmatter dict, validating required fields."""
    today = now.date().isoformat()
    defaults: dict[str, Any] = {
        "schema_version": 2,
        "type": surface_name,
        "created": today,
        "last_reviewed": today,
        "description": title,
        "tags": [],
        "draft": True,
        "synthesis_sources": list(sources),
        "curator_b_run": now.isoformat(),
    }
    fm: dict[str, Any] = {}
    fm.update(defaults)
    fm.update(extra)
    # Force draft:true regardless of what extras say — curator-authored surfaces
    # are always drafts.
    fm["draft"] = True
    # Force type to match surface_name.
    fm["type"] = surface_name

    # Use the in-memory surface_def.required (already resolved from surfaces_doc).
    # Fall back to required_fields_for only when the surface_def has no required list,
    # which can happen if SURFACES.md omits required fields (e.g. for custom surfaces
    # not yet migrated). Prefer the live surface_def to avoid a second file read.
    if surface_def.required:
        required = list(surface_def.required)
    else:
        required = required_fields_for(surface_name, wiki_dir=wiki_root)
    missing = [f for f in required if f not in fm or fm[f] in (None, "")]
    if missing:
        raise ValueError(
            f"surface_filer: missing required frontmatter for '{surface_name}': {missing}"
        )

    return fm


def _render_markdown(fm: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body_text = body.rstrip() + "\n" if body.strip() else ""
    return f"---\n{dumped}\n---\n\n{body_text}"
