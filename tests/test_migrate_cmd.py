"""`lore migrate` verb removal — the session-note migrations retired.

`lore migrate retire-session-notes` and `lore migrate open-items` backed
artifacts the compose pipeline stopped writing (PRD 0013). Both verbs are
gone from the Typer app; Typer reports an unknown command for either.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.mark.parametrize("verb", ["retire-session-notes", "open-items"])
def test_retired_verb_exits_nonzero(verb: str) -> None:
    from lore_cli.migrate_cmd import app

    result = runner.invoke(app, [verb])
    assert result.exit_code != 0


def test_retire_session_notes_how_to_is_gone() -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    assert not (repo / "docs" / "how-to" / "retire-session-notes.md").exists()
