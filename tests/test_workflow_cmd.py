"""Tests for the `lore workflow` Typer subcommands (CLI wrapper over
`lore_workflow`)."""

from __future__ import annotations

import json
from pathlib import Path

from lore_cli import workflow_cmd
from lore_core import note_document as nd
from lore_core.linkage import Linkage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "roadmaps"


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
    nd.create_note(
        path,
        title="Seed lift plumbing",
        description="deterministic seed lift",
        scope="lore",
        created="2026-07-10",
        linkage=Linkage(repo="buchbend/lore", epics=[229]),
    )
    nd.append_chapter(
        path,
        nd.Chapter(blocks=[nd.TopicBlock(lead="Findings lead.", body="Findings body.")]),
        slice_from_turn=0,
        slice_to_turn=5,
    )

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
