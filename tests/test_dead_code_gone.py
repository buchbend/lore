"""Guard test for the #258 dead-code sweep — asserts each retired module,
package, script, and hygiene no-op pass is actually gone.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

DEAD_MODULES = [
    "lore_core.flush_store",
    "lore_core.session_writer",
    "lore_curator.retire_session_notes",
    "lore_curator.open_items_migration",
    "lore_curator.noteworthy",
    "lore_core.noteworthy_features",
    "lore_core.narrative_kind",
    "lore_core.decision_signals",
    "lore_core.threads",
    "lore_core.topic_files",
    "lore_curator.summary_block",
    "lore_core.projects.router",
]


@pytest.mark.parametrize("module_name", DEAD_MODULES)
def test_module_is_gone(module_name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_lore_runtime_package_is_gone():
    assert not (REPO / "lib" / "lore_runtime").exists()


def test_surface_templates_package_is_gone():
    assert not (REPO / "lib" / "lore_core" / "surface_templates").exists()


def test_purge_curator_transcripts_script_is_gone():
    assert not (REPO / "scripts" / "purge_curator_transcripts.py").exists()


def test_hygiene_staleness_pass_is_gone():
    from lore_curator.hygiene import HYGIENE_PASSES

    assert "staleness" not in {p.name for p in HYGIENE_PASSES}
    assert not hasattr(importlib.import_module("lore_curator.hygiene"), "_pass_staleness")


# ---------------------------------------------------------------------------
# No surviving docstring names a module the session-note teardown deleted.
# ---------------------------------------------------------------------------

LIB = REPO / "lib"


@pytest.mark.parametrize("deleted_name", ["session_filer", "session_activity"])
def test_no_docstring_names_a_deleted_module(deleted_name: str):
    hits = sorted(
        str(p.relative_to(REPO))
        for p in LIB.rglob("*.py")
        if deleted_name in p.read_text(errors="replace")
    )
    assert hits == [], f"{deleted_name!r} still named under lib/: {hits}"
