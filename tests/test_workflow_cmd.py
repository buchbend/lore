"""Tests for the `lore workflow` Typer subcommands (CLI wrapper over
`lore_workflow`)."""

from __future__ import annotations

from pathlib import Path

from lore_cli import workflow_cmd

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
