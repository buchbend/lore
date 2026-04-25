"""Topic-signal vs boilerplate file classification.

A small module that captures one shared decision: which file paths
carry topical signal vs. which are project-level boilerplate that
appears in nearly every session and shouldn't drive grouping
decisions.

Two consumers today:

- ``lore_core.session_writer._find_todays_open_note`` (Phase C
  topic-aware merge): file-set Jaccard between a new chunk and a
  candidate same-day note. Boilerplate is stripped from both sides
  so e.g. ``CLAUDE.md`` overlap can't bridge unrelated topics.
- ``lore_core.threads.compute_threads`` (Phase D continuation
  linking): union-find connecting notes that share at least one
  non-boilerplate file. Same intuition.

The list intentionally errs on the side of *over-* exclusion: a real
file like ``schema.sql`` would never appear here, while every common
lockfile / project root manifest does. Adding more entries widens
"things-that-don't-bridge-topics"; removing entries makes the heuristic
strictly less aggressive.
"""

from __future__ import annotations


# Files touched by almost every session — using their overlap as a
# topic-similarity signal would link unrelated work together. Phase D
# (continuation linking) and Curator B's surface generation will
# eventually replace this hand-list with a proper IDF-weighted scheme
# computed across the wiki's note corpus.
BOILERPLATE_FILES: frozenset[str] = frozenset({
    # Documentation / project root
    "CLAUDE.md", "AGENTS.md", "README.md", "README.rst", "LICENSE",
    # Python build / packaging
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", ".python-version",
    # JavaScript / TypeScript build / packaging
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "tsconfig.json",
    # Rust
    "Cargo.toml", "Cargo.lock",
    # Build / container / CI
    "Makefile", "Dockerfile", ".dockerignore",
    # Repo-level config
    ".gitignore", ".gitattributes", ".env", ".editorconfig",
})


def basename(path: str) -> str:
    """Last segment of a path-like string. Cross-platform — splits on /
    and ``\\`` so adapter output from any host normalises."""
    if not isinstance(path, str) or not path:
        return ""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def strip_boilerplate(files: list[str] | None) -> set[str]:
    """Return the file set with boilerplate filenames removed.

    Compares basenames so ``/some/repo/pyproject.toml`` and
    ``./pyproject.toml`` both filter out, regardless of how the host
    spelled the path.
    """
    if not files:
        return set()
    out: set[str] = set()
    for f in files:
        base = basename(f)
        if base and base not in BOILERPLATE_FILES:
            out.add(f)
    return out
