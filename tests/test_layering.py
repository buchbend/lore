"""Layering guard.

The dependency graph is one-way:

    plugin/skills → lore_cli → lore_core / lore_curator / lore_mcp / lore_search

Lower layers must not import from ``lore_cli``. The typer/click
plumbing for every CLI verb lives in ``lore_cli/<verb>_cmd.py`` (lifted
in v0.13.0 — see ``docs/architecture/cli-contract.md``). This test
fails fast if anyone reintroduces an upward import.

Static check (grep-style) rather than runtime: an unconditional import
inside a function or a `TYPE_CHECKING` block is still a layer
violation; we want both caught.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"

LOWER_LAYERS = (
    "lore_core",
    "lore_curator",
    "lore_mcp",
    "lore_search",
    "lore_adapters",
)

# Match `from lore_cli...`, `import lore_cli`, or `import lore_cli.<sub>`.
# We tolerate the bare string `lore_cli` appearing inside a string literal,
# error message, or comment — those are not imports.
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+lore_cli(?:\.\w+)*\s+import\b|import\s+lore_cli(?:\.\w+)*\b)",
    re.MULTILINE,
)


def _python_files(pkg: str) -> list[Path]:
    pkg_dir = LIB / pkg
    if not pkg_dir.is_dir():
        return []
    return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("pkg", LOWER_LAYERS)
def test_lower_layer_does_not_import_lore_cli(pkg: str) -> None:
    offenders: list[tuple[Path, str]] = []
    for path in _python_files(pkg):
        text = path.read_text()
        for m in IMPORT_RE.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            offenders.append((path.relative_to(REPO_ROOT), f"{line_no}: {m.group(0).strip()}"))

    assert not offenders, (
        f"Layer violation: {pkg!r} must not import from lore_cli. "
        f"Lift typer plumbing to lore_cli/<verb>_cmd.py (see "
        f"docs/architecture/cli-contract.md). Offending sites:\n  "
        + "\n  ".join(f"{p}:{loc}" for p, loc in offenders)
    )
