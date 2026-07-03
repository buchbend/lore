"""`lore surface` is not a CLI command.

Curator B's surface-extraction pass is retired; the surface-authoring
CLI, its module, and lore_core.surfaces have all been deleted. This
guards against a future re-mount silently reintroducing the verb.
"""

from __future__ import annotations

from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()


def test_surface_absent_from_top_level_app():
    result = runner.invoke(app, ["surface", "lint"])
    assert result.exit_code != 0
    assert "no such command" in result.output.lower()
