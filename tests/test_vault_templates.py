"""The vault templates `lore init` and `lore wiki new` copy into every new vault."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lore_core.templates import templates_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = {p.name for p in (REPO_ROOT / "skills").iterdir() if p.is_dir()}


@pytest.mark.parametrize("name", ["root-CLAUDE.md", "wiki-CLAUDE.md"])
def test_templates_name_only_shipped_skills(name: str) -> None:
    """A template copied into a vault outlives the skill it names."""
    text = (templates_dir() / name).read_text()
    named = set(re.findall(r"/lore:([\w-]+)", text))
    assert named <= SKILLS, named - SKILLS


@pytest.mark.parametrize("name", ["root-CLAUDE.md", "wiki-CLAUDE.md"])
def test_templates_describe_no_session_notes(name: str) -> None:
    text = (templates_dir() / name).read_text()
    assert "sessions" not in text
    assert "templates/" not in text
    assert ".lore-hints" not in text  # no code reads it


def test_no_session_note_template_ships() -> None:
    assert not (templates_dir() / "session.md").exists()
