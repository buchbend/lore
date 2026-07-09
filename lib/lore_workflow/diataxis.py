#!/usr/bin/env python3
"""Diátaxis quadrant-classification heuristic for the document-epic skill.

The `document-epic` skill body is LLM-driven prose, but the part that decides
*which Diátaxis quadrant a given change belongs to* must be deterministic so it
can be tested and so two runs of the skill agree. That decision lives here as a
pure helper with no I/O and no third-party dependency (stdlib only, so it runs
in CI without an install step).

The four Diátaxis quadrants (https://diataxis.fr/):

  - tutorial    — learning-oriented; a guided first-time walkthrough.
  - how-to      — task-oriented; a recipe to accomplish a specific goal.
  - reference   — information-oriented; for us that means docstrings + the
                  autosummary/toctree wiring that surfaces them, NOT prose.
  - explanation — understanding-oriented; background, the "why", trade-offs.

Hard rule encoded here and relied on by the skill: `docs/prd/` and `docs/adr/`
are the canonical, human-owned record. `document-epic` reads them for intent but
NEVER edits them, so they are excluded from every quadrant — `classify()`
returns None for them regardless of where they sit under `docs/`.
"""

from __future__ import annotations

from collections.abc import Iterable

# The canonical Diátaxis four. Anything the skill writes maps to one of these.
QUADRANTS = ("tutorial", "how-to", "reference", "explanation")

# Documentation directory names mapped to their quadrant. Keys are the directory
# segment as it appears under a docs root. We accept the canonical Diátaxis
# names plus the synonyms commonly used across repos so the heuristic works on a
# target repo's existing layout without forcing a rename.
_DOC_DIR_QUADRANT = {
    # tutorial
    "tutorial": "tutorial",
    "tutorials": "tutorial",
    "getting-started": "tutorial",
    # how-to
    "how-to": "how-to",
    "howto": "how-to",
    "how-to-guides": "how-to",
    "guide": "how-to",
    "guides": "how-to",
    "recipes": "how-to",
    # reference
    "reference": "reference",
    "api": "reference",
    # explanation
    "explanation": "explanation",
    "explanations": "explanation",
    "concepts": "explanation",
    "discussion": "explanation",
    "background": "explanation",
}

# Path segments that mark the canonical, human-owned record. Never edited.
_EXCLUDED_DOC_DIRS = ("prd", "adr")


def _segments(path: str) -> list[str]:
    """Split a repo-relative path into normalised segments."""
    return [seg for seg in path.replace("\\", "/").split("/") if seg]


def is_excluded(path: str) -> bool:
    """True if the path lives under a `docs/prd/` or `docs/adr/` tree.

    These are the canonical PRD/ADR record. The skill reads them but must never
    write them, so they are excluded from classification entirely. Matches the
    `prd`/`adr` segment only when it sits directly under a `docs` segment, so a
    source file that merely happens to be named `adr.py` is not caught.
    """
    segs = _segments(path)
    for i, seg in enumerate(segs[:-1]):
        if seg == "docs" and segs[i + 1] in _EXCLUDED_DOC_DIRS:
            return True
    return False


def _doc_quadrant(path: str) -> str | None:
    """Quadrant for a path that lives under a docs tree, else None."""
    segs = _segments(path)
    if "docs" not in segs:
        return None
    # Look at the segment immediately following the docs root.
    idx = segs.index("docs")
    for seg in segs[idx + 1 :]:
        if seg in _DOC_DIR_QUADRANT:
            return _DOC_DIR_QUADRANT[seg]
    return None


def _is_source(path: str) -> bool:
    """True for a code source file (something that can carry docstrings)."""
    return path.endswith((".py", ".pyi"))


def classify(path: str, *, public_api: bool = False) -> str | None:
    """Map a changed path to the Diátaxis quadrant whose docs it should update.

    Returns one of QUADRANTS, or None when the change warrants no docs edit.

    Rules, in order:
      1. PRD/ADR paths are excluded — always None (never edited).
      2. A path under a recognised docs directory maps to that directory's
         quadrant (canonical names + common synonyms).
      3. A *public* source file maps to `reference`: a public API change means
         its docstring and autosummary/toctree wiring need attention, not prose.
      4. Everything else (private source, tests, build/config) maps to None.

    `public_api` flags whether a source change touches the public surface; the
    caller (the skill) determines this from the diff (e.g. a non-underscore
    symbol exported from the package).
    """
    if is_excluded(path):
        return None

    doc_q = _doc_quadrant(path)
    if doc_q is not None:
        return doc_q

    if _is_source(path) and public_api:
        return "reference"

    return None


def classify_changeset(changes: Iterable[dict]) -> list[dict]:
    """Classify a whole cumulative epic diff into an edit plan.

    `changes` is an iterable of dicts with at least a `path` key and optionally
    `public_api`. Returns one dict per change with the resolved `quadrant`
    (None = no docs edit) and an `excluded` flag for prd/adr paths. Excluded
    paths are guaranteed to carry `quadrant=None`, so the edit plan (the subset
    with a non-None quadrant) can never contain a prd/adr file.
    """
    plan: list[dict] = []
    for change in changes:
        path = change["path"]
        excluded = is_excluded(path)
        quadrant = (
            None
            if excluded
            else classify(path, public_api=bool(change.get("public_api", False)))
        )
        plan.append(
            {
                "path": path,
                "quadrant": quadrant,
                "excluded": excluded,
            }
        )
    return plan
