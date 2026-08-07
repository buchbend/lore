"""Guard test for the #258 dead-code sweep — asserts each retired module,
package, script, and hygiene no-op pass is actually gone.
"""

from __future__ import annotations

import importlib
import re
import subprocess
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


# ---------------------------------------------------------------------------
# The curator framing — no docstring names a role Lore retired, and the spawn
# registry is gone.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retired_name", ["session_curator", "run_curator_a"])
def test_lore_curator_docstring_names_no_retired_entry_point(retired_name: str):
    import lore_curator

    assert retired_name not in (lore_curator.__doc__ or "")


def test_run_log_docstring_names_no_session_curator_producer():
    import lore_core.run_log

    assert "session_curator" not in (lore_core.run_log.__doc__ or "")


def test_spawn_role_registry_is_gone():
    spawn_module = importlib.import_module("lore_cli.spawn")

    for name in ("SPAWN_ROLES", "SpawnRole", "spawn"):
        assert not hasattr(spawn_module, name), f"lore_cli.spawn still exports {name!r}"
    with pytest.raises(ImportError):
        from lore_cli.spawn import SPAWN_ROLES  # noqa: F401


# ---------------------------------------------------------------------------
# Prose describing the retired pipeline.
# ---------------------------------------------------------------------------

SESSION_NOTE_RE = re.compile(r"session[ _-]notes?", re.IGNORECASE)

# The record of the retirement may name what it retired. Editing these would
# falsify a dated measurement or a shipped changelog entry.
STUB_NOTE_RECORD_FILES = {
    "CHANGELOG.md",
    "docs/session-note-teardown-sweep.md",
}


def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", "*.md"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def test_no_skill_claims_lore_writes_a_session_note():
    """No shipped skill names a session note — no producer writes one."""
    hits = sorted(
        f"{p.relative_to(REPO)}:{lineno}"
        for p in (REPO / "skills").rglob("*.md")
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1)
        if SESSION_NOTE_RE.search(line)
    )
    assert hits == [], f"skills/ still names a session note: {hits}"


def test_no_tracked_markdown_names_stub_note():
    """``lore_curator.stub_note`` is gone; only the record may still name it."""
    hits = sorted(
        rel
        for rel in _tracked_markdown()
        if rel not in STUB_NOTE_RECORD_FILES
        and not rel.startswith(("docs/prd/", "docs/adr/"))
        and "stub_note" in (REPO / rel).read_text(encoding="utf-8")
    )
    assert hits == [], f"stub_note still named in tracked Markdown: {hits}"


def test_readme_states_what_lore_captures():
    """The README's opening claim matches what Lore captures today."""
    opening = (REPO / "README.md").read_text(encoding="utf-8").split("## The pitch")[0]

    assert "transcript" in opening.lower()
    assert "flag" in opening.lower()
    assert not SESSION_NOTE_RE.search(opening)
