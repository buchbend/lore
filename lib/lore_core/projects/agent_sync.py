"""AGENTS.md ↔ project orientation ``## Agent guidance`` sync (Phase 7).

The project orientation note's ``## Agent guidance`` H2 section is the
canonical source for project-level agent instructions. The attached
repo's ``AGENTS.md`` (or ``CLAUDE.md``) is a projection of that section.

When the two diverge, lint surfaces the drift and the user runs
``lore project sync --to-repo`` (orientation → repo) or
``lore project sync --from-repo`` (repo → orientation).

This module exposes the read / write / compare primitives. Lint
integration lives in ``lore_core.lint``; CLI verb in
``lore_cli.project_cmd``.

v1 contract:
  - Source/sink: a ``## Agent guidance`` section in the orientation.
  - Repo side: the full ``AGENTS.md`` body (frontmatter and leading H1
    stripped).
  - Compare on **stripped, normalised** text (whitespace-collapsed) to
    keep trivia from triggering drift warnings.
  - Sync is destructive only on the chosen side; the other side is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lore_core.schema import strip_frontmatter


_AGENT_GUIDANCE_HEADING = "## Agent guidance"


@dataclass(frozen=True)
class SyncStatus:
    orientation_has_section: bool
    repo_file_exists: bool
    orientation_text: str
    repo_text: str
    in_sync: bool


def _normalise(text: str) -> str:
    """Whitespace-collapse for drift comparison.

    Trims, collapses runs of internal whitespace to a single space, and
    drops leading/trailing newlines. Pure-cosmetic differences
    (indentation, trailing newlines) don't trigger drift warnings.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def extract_agent_guidance(orientation_text: str) -> str | None:
    """Return the body of the ``## Agent guidance`` section, or None.

    Walks the post-frontmatter body line-by-line. Captures everything
    between the matching H2 heading and the next H2 (or EOF). Heading
    line is excluded from the returned body.
    """
    body = strip_frontmatter(orientation_text)
    lines = body.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if stripped.lower() == _AGENT_GUIDANCE_HEADING.lower():
                in_section = True
            continue
        if stripped.startswith("## "):
            break
        out.append(line)
    if not in_section:
        return None
    return "\n".join(out).strip("\n").rstrip() + "\n"


def replace_agent_guidance(orientation_text: str, new_body: str) -> str:
    """Return ``orientation_text`` with ``## Agent guidance`` body
    replaced by ``new_body``.

    If the section is missing, appends it at the end of the body.
    Frontmatter is preserved verbatim.
    """
    new_body = (new_body or "").strip("\n").rstrip()

    # Split text into ``frontmatter_block`` (with delimiters) and ``body``.
    # ``strip_frontmatter`` returns body only; we compute the split point.
    body = strip_frontmatter(orientation_text)
    if body == orientation_text:
        prefix = ""
    else:
        # The frontmatter ends just before ``body`` starts. Find that
        # cut by anchoring on the end of the second ``---`` delimiter.
        cut = orientation_text.rfind(body)
        prefix = orientation_text[:cut] if cut >= 0 else ""

    lines = body.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if (
            stripped.lower() == _AGENT_GUIDANCE_HEADING.lower()
            and not replaced
        ):
            out.append(line)
            out.append("")
            out.append(new_body)
            out.append("")
            replaced = True
            i += 1
            # Skip existing section body up to next H2 / EOF.
            while i < len(lines):
                if lines[i].strip().startswith("## "):
                    break
                i += 1
            continue
        out.append(line)
        i += 1

    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(_AGENT_GUIDANCE_HEADING)
        out.append("")
        out.append(new_body)
        out.append("")

    new_body_text = "\n".join(out).rstrip() + "\n"
    return prefix + new_body_text


def read_repo_agent_file(repo_root: Path) -> tuple[Path | None, str]:
    """Locate the repo's agent-guidance file and return its body.

    Preference order: ``AGENTS.md`` > ``CLAUDE.md``. Frontmatter is
    stripped; a leading H1 is also stripped so the body matches the
    orientation section's content shape.

    Returns ``(path, body)``. Path is None when no candidate exists.
    """
    for name in ("AGENTS.md", "CLAUDE.md"):
        candidate = repo_root / name
        if candidate.is_file():
            try:
                text = candidate.read_text(errors="replace")
            except OSError:
                continue
            body = strip_frontmatter(text).lstrip()
            # Strip a leading H1 (``# Title``) — its content overlaps
            # with the repo name and isn't part of the agent guidance.
            lines = body.splitlines()
            if lines and lines[0].strip().startswith("# "):
                body = "\n".join(lines[1:]).lstrip()
            return candidate, body.rstrip() + "\n"
    return None, ""


def write_repo_agent_file(repo_path: Path, body: str) -> None:
    """Atomically rewrite ``AGENTS.md`` (or ``CLAUDE.md``) with ``body``.

    A leading H1 (``# <repo-stem>``) is prepended so the file remains
    a normal-shaped Markdown document.
    """
    from lore_core.io import atomic_write_text

    title = repo_path.parent.name
    text = f"# {title}\n\n{body.strip()}\n"
    atomic_write_text(repo_path, text)


def compute_sync_status(
    orientation_path: Path,
    repo_root: Path,
) -> SyncStatus:
    """Read both sides and report whether they're in sync."""
    orientation_text = ""
    has_section = False
    if orientation_path.is_file():
        try:
            orientation_text = orientation_path.read_text(errors="replace")
        except OSError:
            orientation_text = ""
    section = extract_agent_guidance(orientation_text)
    has_section = section is not None

    repo_path, repo_text = read_repo_agent_file(repo_root)

    if not has_section:
        # Without a section, we can't compare; treat as in-sync (nothing
        # to drift against).
        return SyncStatus(
            orientation_has_section=False,
            repo_file_exists=repo_path is not None,
            orientation_text="",
            repo_text=repo_text,
            in_sync=True,
        )

    in_sync = _normalise(section) == _normalise(repo_text)
    return SyncStatus(
        orientation_has_section=True,
        repo_file_exists=repo_path is not None,
        orientation_text=section,
        repo_text=repo_text,
        in_sync=in_sync,
    )
