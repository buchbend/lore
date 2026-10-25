"""Grep coverage: orient, to-epic and build read the system map (issue 534).

Each test pins one acceptance criterion of issue 534 (let orient, to-epic
and build read the system map) in the file where the skill keeps it.
"""

from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "lore-workflow" / "skills"


def _text(rel: str) -> str:
    return " ".join((SKILLS / rel).read_text(encoding="utf-8").split())


def test_orient_starts_the_cross_repo_explorer_from_lore_map() -> None:
    text = _text("orient/SKILL.md")
    assert "one for cross-repo consumers when the intent spans repos" in text
    assert "starts from `lore map <owner/repo> --json`" in text
    assert "an empty list: it scans as before" in text


def test_to_epic_names_the_edge_pin_for_a_cross_repo_row() -> None:
    text = _text("to-epic/SKILL.md")
    assert "A row that crosses repos names the edge's `pin` in its acceptance criteria" in text
    assert "no edge: write the row as before" in text


def test_build_epic_reads_the_pin_in_the_cross_repo_pin_step() -> None:
    text = _text("build/epic.md")
    assert "read the `pin` of the edge between producer and consumer" in text
    assert "Without an edge, record the producer's SHA" in text
