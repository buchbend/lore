"""Resolve a style document — wiki override wins, packaged default is the fallback.

Resolution is whole-file: `<wiki>/style/<name>.md` if present, else the copy
shipped as package data under `lore_core/styles/`. A style document is prose,
so there are no merge semantics — merging a rules essay is ill-defined, and one
lookup leaves no cascade to debug. Customizing means copying the default into
the wiki and editing it. There is no per-repo layer.

The defaults live outside `templates/` on purpose: `lore init` copies the whole
templates tree into the vault, and a copy of the register sitting at
`<lore_root>/templates/` would look editable while the resolver ignores it.
"""

from __future__ import annotations

from pathlib import Path

# Style names Lore ships a default for. The per-wiki override uses the same
# file name under `<wiki>/style/`.
KNOWN_STYLES: tuple[str, ...] = ("issue-register",)


class UnknownStyle(ValueError):
    """Raised for a style name Lore does not ship."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"unknown style {name!r} — known styles: {', '.join(KNOWN_STYLES)}"
        )
        self.name = name


def default_style_path(name: str) -> Path:
    """Return the packaged default for ``name``.

    Raises FileNotFoundError if the file is missing — the symptom of a broken
    install where ``[tool.setuptools.package-data]`` failed to bundle it.
    """
    path = Path(__file__).resolve().parent / "styles" / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Could not locate the packaged {name} at {path}. Reinstall Lore.")
    return path


def resolve_style_path(name: str, wiki_dir: Path | None = None) -> Path:
    """Return the path of the style document that wins for ``wiki_dir``."""
    if name not in KNOWN_STYLES:
        raise UnknownStyle(name)
    if wiki_dir is not None:
        override = wiki_dir / "style" / f"{name}.md"
        if override.is_file():
            return override
    return default_style_path(name)
