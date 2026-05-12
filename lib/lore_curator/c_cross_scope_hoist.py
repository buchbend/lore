"""Curator C — cross-scope concept hoist proposal pass (Phase 4).

Scans all project folders (``projects/<slug>/``) for concept notes that
recur with similar names across **sibling** project folders (those
sharing a common parent scope). When a concept slug appears in ≥2
sibling projects with fuzzy similarity ≥ 0.6, proposes hoisting it up
to the parent project's ``concepts/`` subfolder.

Proposal-only: writes a new draft note carrying ``hoist_candidate_sources``
in frontmatter. Originals are untouched. Curator C never inline-edits
project notes — body content remains human-authoritative.

Auto-stub: if the parent project folder doesn't exist yet, materialises
it via :func:`lore_core.projects.stub_generator.stub_project_note` with
``bare=True`` (no harness reads, minimal Overview/Conventions/Architecture
skeleton). Slug-collision against any non-project basename in the wiki
aborts the auto-stub for that hoist.

Threshold rationale: title-slug fuzz at 0.6 matches the existing
adjacent-merge primitive (``c_adjacent_merge.py:43``). A pair-level
match is an adjacent-merge candidate; a cross-sibling cluster of ≥2 is
a hoist candidate. That's the line between the two passes.
"""

from __future__ import annotations

import difflib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lore_core.projects.stub_generator import stub_project_note
from lore_core.schema import parse_frontmatter
from lore_core.wikilinks import existing_slugs


_FUZZ_THRESHOLD = 0.6
_MIN_SIBLINGS = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug_collides_outside_projects(
    wiki_path: Path, slug: str,
) -> bool:
    """True if ``slug`` is already a basename anywhere in the wiki
    *outside* the projects subtree.

    The wikilink resolver (``lore_core.wikilinks.existing_slugs``) is
    basename-indexed across the whole wiki. Auto-stubbing
    ``projects/<slug>/<slug>.md`` would create an ambiguous wikilink
    target if some non-project note already owns the slug — so we
    refuse the auto-stub in that case.
    """
    for p in wiki_path.rglob(f"{slug}.md"):
        # Skip the would-be project orientation itself.
        try:
            rel = p.relative_to(wiki_path)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "projects" and parts[1] == slug:
            continue
        if p.name.startswith("_"):
            continue
        return True
    return False


def _read_project_scope(orientation_path: Path) -> str:
    """Return ``scope:`` frontmatter value of a project orientation note."""
    try:
        text = orientation_path.read_text(errors="replace")
        fm = parse_frontmatter(text) or {}
    except OSError:
        return ""
    return str(fm.get("scope") or "")


def _project_concepts(project_dir: Path) -> list[Path]:
    """Return concept .md files inside ``projects/<slug>/concepts/``.

    Skips ``_`` -prefixed files (collections) and ``proposed-hoist-*``
    proposals from prior runs.
    """
    concepts_dir = project_dir / "concepts"
    if not concepts_dir.is_dir():
        return []
    out: list[Path] = []
    for c in sorted(concepts_dir.glob("*.md")):
        if c.name.startswith("_"):
            continue
        if c.name.startswith("proposed-hoist-"):
            continue
        out.append(c)
    return out


def _scan_projects(wiki_path: Path) -> dict[str, dict]:
    """Map ``project_slug → {scope, concepts, path}``."""
    projects_root = wiki_path / "projects"
    if not projects_root.is_dir():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(projects_root.iterdir()):
        if not p.is_dir():
            continue
        orientation = p / f"{p.name}.md"
        if not orientation.is_file():
            continue
        scope = _read_project_scope(orientation)
        out[p.name] = {
            "scope": scope,
            "concepts": _project_concepts(p),
            "path": p,
        }
    return out


def _group_by_parent_scope(
    projects: dict[str, dict],
) -> dict[str, list[str]]:
    """Map ``parent_scope → [project_slug, ...]`` for projects whose
    scope chain has a parent (≥2 segments). Empty/single-segment scopes
    are skipped — they have no parent to hoist into.
    """
    siblings: dict[str, list[str]] = {}
    for slug, info in projects.items():
        scope = info["scope"]
        if not scope or ":" not in scope:
            continue
        parent_scope = scope.rsplit(":", 1)[0]
        siblings.setdefault(parent_scope, []).append(slug)
    return siblings


def _cluster_concepts_by_fuzz(
    candidates: list[tuple[str, Path, str]],
) -> list[list[tuple[str, Path, str]]]:
    """Connected-component clustering by fuzzy slug match (≥ 0.6).

    Each entry is ``(slug, path, project_slug)``. Greedy: a candidate
    joins the first existing group whose first member has a fuzz ratio
    ≥ threshold; otherwise it starts a new group.
    """
    groups: list[list[tuple[str, Path, str]]] = []
    for cand in candidates:
        placed = False
        for g in groups:
            ref_slug = g[0][0]
            ratio = difflib.SequenceMatcher(None, ref_slug, cand[0]).ratio()
            if ratio >= _FUZZ_THRESHOLD:
                g.append(cand)
                placed = True
                break
        if not placed:
            groups.append([cand])
    return groups


# ---------------------------------------------------------------------------
# Public pass
# ---------------------------------------------------------------------------


def cross_scope_hoist_pass(
    wiki_path: Path,
    *,
    llm_client: Any = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Curator C cross-scope hoist defrag pass.

    Signature matches the contract enforced by
    ``defrag_curator._all_defrag_passes``: ``(wiki_path, *, llm_client,
    dry_run) -> dict[str, int]``. ``llm_client`` is unused for this
    pass — title-slug fuzz is deterministic.

    Returns counters with keys:
      - ``cross_scope_hoist_proposed`` — new proposal notes written
      - ``cross_scope_hoist_skipped_collision`` — parent slug collides
        with a non-project basename, auto-stub aborted
      - ``cross_scope_hoist_existing`` — proposal already on disk, skipped
    """
    projects = _scan_projects(wiki_path)
    if not projects:
        return {"cross_scope_hoist_proposed": 0}

    siblings_by_parent = _group_by_parent_scope(projects)

    proposed = 0
    skipped_collision = 0
    existing = 0
    today = datetime.now(UTC).date().isoformat()

    for parent_scope, sibling_slugs in sorted(siblings_by_parent.items()):
        if len(sibling_slugs) < _MIN_SIBLINGS:
            continue

        # Collect (slug, path, project_slug) for every concept in every sibling.
        candidates: list[tuple[str, Path, str]] = []
        for s in sibling_slugs:
            for c in projects[s]["concepts"]:
                candidates.append((c.stem, c, s))

        # Cluster by fuzzy slug match; only groups spanning ≥2 distinct
        # sibling projects are hoist-worthy.
        groups = _cluster_concepts_by_fuzz(candidates)
        for g in groups:
            distinct_projects = {entry[2] for entry in g}
            if len(distinct_projects) < _MIN_SIBLINGS:
                continue

            parent_slug = parent_scope.rsplit(":", 1)[-1]

            # Auto-stub the parent if missing. Refuse on slug collision.
            parent_exists = parent_slug in projects
            if not parent_exists:
                if _slug_collides_outside_projects(wiki_path, parent_slug):
                    skipped_collision += 1
                    continue
                if not dry_run:
                    stub_project_note(
                        wiki_root=wiki_path,
                        repo_slug=parent_slug,
                        scope=parent_scope,
                        bare=True,
                    )

            ref_slug = g[0][0]
            proposal_path = (
                wiki_path / "projects" / parent_slug / "concepts"
                / f"proposed-hoist-{ref_slug}.md"
            )
            if proposal_path.exists():
                existing += 1
                continue

            if dry_run:
                proposed += 1
                continue

            proposal_path.parent.mkdir(parents=True, exist_ok=True)
            sources_yaml = "\n".join(
                f"  - [[{entry[1].stem}]]" for entry in g
            )
            sources_md = "\n".join(
                f"- [[{entry[1].stem}]] (in `{entry[2]}`)" for entry in g
            )
            text = (
                "---\n"
                "type: concept\n"
                "draft: true\n"
                f"created: {today}\n"
                f"last_reviewed: {today}\n"
                f"description: 'Proposed hoist of {ref_slug!r} "
                f"recurring across {len(distinct_projects)} sibling projects.'\n"
                "tags: []\n"
                f"scope: {parent_scope}\n"
                "hoist_candidate_sources:\n"
                f"{sources_yaml}\n"
                "---\n\n"
                f"# Proposed hoist: {ref_slug}\n\n"
                f"This concept appears in {len(distinct_projects)} sibling "
                f"projects under `{parent_scope}`. Curator C proposes "
                f"hoisting it to the parent project so the knowledge is "
                f"shared rather than duplicated.\n\n"
                "## Source notes\n\n"
                f"{sources_md}\n"
            )
            try:
                proposal_path.write_text(text)
                proposed += 1
            except OSError:
                # Best-effort; surface the count so far.
                return {
                    "cross_scope_hoist_proposed": proposed,
                    "cross_scope_hoist_disk_error": 1,
                }

    out = {
        "cross_scope_hoist_proposed": proposed,
        "cross_scope_hoist_skipped_collision": skipped_collision,
        "cross_scope_hoist_existing": existing,
    }
    return out
