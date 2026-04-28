"""`lore drill` CLI verb — wraps handle_drill, renders trace + result."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _envelope() -> dict:
    return {
        "trace": [
            {"stage": "search", "query": "foo", "hits": 1, "elapsed_ms": 5},
            {"stage": "read", "paths": ["foo.md"], "elapsed_ms": 8},
            {"stage": "expand", "wikilinks": ["bar"], "elapsed_ms": 3},
            {"stage": "read_expanded", "paths": ["bar.md"], "elapsed_ms": 6},
        ],
        "result": {
            "notes": [
                {"wiki": "private", "path": "foo.md", "content": "## Foo"},
                {"wiki": "private", "path": "bar.md", "content": "## Bar"},
            ]
        },
    }


def test_drill_renders_trace_and_notes(runner):
    from lore_cli.drill_cmd import app

    with patch("lore_cli.drill_cmd.handle_drill", return_value=_envelope()):
        result = runner.invoke(app, ["foo"])

    assert result.exit_code == 0, result.output
    out = result.output
    # Trace headers visible.
    assert "search" in out and "read" in out and "expand" in out
    # Note paths visible.
    assert "foo.md" in out and "bar.md" in out


def test_drill_json_output(runner):
    from lore_cli.drill_cmd import app

    with patch("lore_cli.drill_cmd.handle_drill", return_value=_envelope()):
        result = runner.invoke(app, ["foo", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["schema"] == "lore.drill/1"
    assert parsed["data"]["result"]["notes"][0]["path"] == "foo.md"


def test_drill_handler_error_exits_nonzero(runner):
    from lore_cli.drill_cmd import app

    err = {"error": {"code": "wiki_not_found", "message": "wiki not found: nope", "next": "run lore status"}}
    with patch("lore_cli.drill_cmd.handle_drill", return_value=err):
        result = runner.invoke(app, ["x", "--wiki", "nope"])

    assert result.exit_code == 1
    assert "wiki_not_found" in result.output


def test_drill_passes_through_options(runner):
    from lore_cli.drill_cmd import app

    with patch("lore_cli.drill_cmd.handle_drill", return_value=_envelope()) as m:
        runner.invoke(app, ["foo", "--wiki", "private", "--k", "3", "--expand-limit", "2"])

    m.assert_called_once_with(query="foo", wiki="private", k=3, expand_limit=2)


def test_drill_renders_skipped_stages(runner):
    """Empty intermediate result is shown without misrepresenting it as a real stage."""
    from lore_cli.drill_cmd import app

    envelope = {
        "trace": [
            {"stage": "search", "query": "x", "hits": 0, "elapsed_ms": 4},
            {"stage": "read", "skipped": "search_returned_zero", "elapsed_ms": 0},
            {"stage": "expand", "skipped": "search_returned_zero", "elapsed_ms": 0},
            {"stage": "read_expanded", "skipped": "search_returned_zero", "elapsed_ms": 0},
        ],
        "result": {"notes": []},
    }
    with patch("lore_cli.drill_cmd.handle_drill", return_value=envelope):
        result = runner.invoke(app, ["x"])

    assert result.exit_code == 0
    assert "skipped" in result.output
    assert "search_returned_zero" in result.output
    assert "No notes" in result.output
