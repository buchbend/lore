"""Guard test for the retired briefings and the retired LLM client.

Nothing Lore keeps calls a model, so the briefing command, its sinks and
the backend-agnostic client leave together. A config file written before
the retirement still loads: the retired block warns once and is ignored.
"""

from __future__ import annotations

import importlib
import warnings
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()

RETIRED_MODULES = [
    "lore_cli.briefing_cmd",
    "lore_core.briefing",
    "lore_core.briefing.compose",
    "lore_core.briefing.format",
    "lore_core.briefing.gather",
    "lore_core.briefing.sinks",
    "lore_curator.llm_client",
]


@pytest.mark.parametrize("module_name", RETIRED_MODULES)
def test_the_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_the_briefing_cli_verb_is_unknown() -> None:
    from lore_cli.__main__ import app

    result = runner.invoke(app, ["briefing"])

    assert result.exit_code != 0
    assert "No such command" in result.output


# ---------------------------------------------------------------------------
# Retired config keys
# ---------------------------------------------------------------------------


def test_the_wiki_config_declares_no_briefing_block() -> None:
    from lore_core.wiki_config import WikiConfig

    assert not hasattr(WikiConfig(), "briefing")


def test_a_retired_briefing_block_warns_once_and_keeps_loading(tmp_path: Path) -> None:
    from lore_core.wiki_config import load_wiki_config

    (tmp_path / ".lore-wiki.yml").write_text(
        "briefing:\n  auto: true\n  audience: team\n  sinks:\n    - matrix\n"
        "git:\n  auto_push: true\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_wiki_config(tmp_path)

    briefing_warnings = [w for w in caught if "briefing" in str(w.message)]
    assert len(briefing_warnings) == 1, [str(w.message) for w in caught]
    assert cfg.git.auto_push is True


def test_the_root_config_declares_no_backend_block() -> None:
    from lore_core.root_config import RootConfig

    assert not hasattr(RootConfig(), "curator")


def test_a_retired_backend_block_warns_once_and_keeps_loading(tmp_path: Path) -> None:
    from lore_core.root_config import load_root_config

    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir()
    (cfg_dir / "config.yml").write_text(
        "curator:\n  backend: openai\n  openai:\n    base_url: https://example.invalid\n"
        "    model_simple: a\n    reasoning_effort_high: low\n"
        "journal:\n  enabled: true\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_root_config(tmp_path)

    curator_warnings = [w for w in caught if "curator" in str(w.message)]
    assert len(curator_warnings) == 1, [str(w.message) for w in caught]
    assert cfg.journal.enabled is True
