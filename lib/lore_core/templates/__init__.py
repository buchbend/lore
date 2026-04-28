"""Bundled scaffolding templates.

Files in this directory are package data — see `pyproject.toml`'s
`[tool.setuptools.package-data]` table. Reach them via
`importlib.resources.files("lore_core") / "templates" / ...` so the
lookup works in both editable and wheel installs.

Most callers just need the directory path. Use ``templates_dir()`` —
it resolves once via the package's ``__file__`` (which works under both
editable and wheel installs) and validates the directory exists with a
canonical marker file. Centralized here so a future template-layout
change touches one helper, not five copies of the same one-liner.
"""

from __future__ import annotations

from pathlib import Path


def templates_dir() -> Path:
    """Return the path to the bundled templates/ directory.

    Raises FileNotFoundError if the directory or its canonical marker
    (``wiki-CLAUDE.md``) is missing — the symptom of a broken install
    where ``[tool.setuptools.package-data]`` failed to bundle the
    files. Callers should let this propagate; the install-doctor and
    crash-log surface it from there.
    """
    here = Path(__file__).resolve().parent
    if not (here / "wiki-CLAUDE.md").exists():
        raise FileNotFoundError(
            f"Could not locate templates at {here}. Reinstall Lore."
        )
    return here
