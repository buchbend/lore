"""Tests for the `lore workflow` Typer subcommands (CLI wrapper over
`lore_workflow`)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from lore_cli import workflow_cmd
from lore_core import note_document as nd

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "roadmaps"


def _seed_note_with_chapter(path: Path) -> None:
    """Write a minimal note file directly, plus one topic chapter.

    The chapter lifecycle (create_note, append_chapter, Chapter, TopicBlock)
    was deleted with the compose pipeline (PRD 0013) — seed-lift only reads
    a note through read_note, so the fixture writes the shape by hand.
    """
    fm = {
        "schema_version": 2,
        "type": "session",
        "note_status": "open",
        "created": "2026-07-10",
        "last_reviewed": "2026-07-10",
        "title": "Seed lift plumbing",
        "description": "deterministic seed lift",
        "scope": "lore",
        "chapters": [{"n": 1, "kind": "topic", "from_turn": 0, "to_turn": 5}],
        "linkage": {
            "schema_version": 1,
            "repo": "buchbend/lore",
            "branch": "",
            "issues": [],
            "prs": [],
            "epics": [229],
            "author": "",
            "trace_id": None,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = f"{nd.DISCLAIMER}\n\n<!-- lore:chapter 1 @0-5 -->\n\n**Findings lead.** Findings body."
    path.write_text(f"---\n{dumped}\n---\n\n{body}\n")


def test_validate_roadmap_ok(capsys) -> None:
    rc = workflow_cmd.main(["validate-roadmap", str(FIXTURES / "well-formed.md")])
    assert rc == 0
    assert "roadmap OK" in capsys.readouterr().out


def test_validate_roadmap_invalid(capsys) -> None:
    rc = workflow_cmd.main(["validate-roadmap", str(FIXTURES / "cyclic.md")])
    assert rc != 0
    assert "roadmap INVALID" in capsys.readouterr().out


def test_create_prd_writes_file(tmp_path: Path) -> None:
    rc = workflow_cmd.main(
        [
            "create-prd",
            "--slug",
            "foo",
            "--title",
            "Foo",
            "--epic-url",
            "https://github.com/o/r/issues/8",
            "--repo",
            "o/r",
            "--target",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert (tmp_path / "docs" / "prd" / "0001-foo.md").exists()


def test_seed_lift_prints_json_on_usable_note(tmp_path: Path, capsys) -> None:
    path = tmp_path / "sessions" / "10-topic.md"
    _seed_note_with_chapter(path)

    rc = workflow_cmd.main(["seed-lift", str(path)])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "buchbend/lore" in payload["origin"]
    assert "Findings body." in payload["findings"]
    assert payload["source_note"] == str(path)


def test_seed_lift_exits_nonzero_when_note_missing(tmp_path: Path, capsys) -> None:
    rc = workflow_cmd.main(["seed-lift", str(tmp_path / "sessions" / "nope.md")])

    assert rc != 0
    assert "fall back to freehand" in capsys.readouterr().out
