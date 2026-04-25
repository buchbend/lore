"""Env > config precedence tests for the OpenAI-compatible backend.

The contract documented in `_resolve_openai_settings` and visible in the
config map (`docs/architecture/config.md`) is: per-tier env overrides
``LORE_OPENAI_MODEL_{SIMPLE,MIDDLE,HIGH}`` win over the matching keys
under ``curator.openai`` in ``$LORE_ROOT/.lore/config.yml``. Existing
tests cover env-only and config-only paths; this file pins down the
*conflict resolution* explicitly so a refactor can't silently invert
the precedence.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _write_config(tmp_path: Path, body: str) -> None:
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.yml").write_text(body)


def test_env_model_middle_beats_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://config-side.example/v1\n"
        "    model_middle: from-config\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LORE_OPENAI_MODEL_MIDDLE", "from-env")

    base_url, api_key, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["middle"] == "from-env"


def test_env_model_simple_beats_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_simple: from-config\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LORE_OPENAI_MODEL_SIMPLE", "from-env")

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["simple"] == "from-env"


def test_env_base_url_beats_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://config-side.example/v1\n",
    )
    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://env-side.example/v1")
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")

    base_url, _, _ = _resolve_openai_settings(tmp_path)
    assert base_url == "https://env-side.example/v1"


def test_config_used_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the precedence: config wins over the implicit
    'unset' default. Reads pass through cleanly when env is empty."""
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://config-only.example/v1\n"
        "    model_high: only-from-config\n",
    )
    monkeypatch.delenv("LORE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LORE_OPENAI_MODEL_HIGH", raising=False)
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")

    base_url, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert base_url == "https://config-only.example/v1"
    assert tier_to_model["high"] == "only-from-config"


def test_partial_env_override_leaves_other_tiers_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting MODEL_MIDDLE in env shouldn't blank out MODEL_HIGH from config."""
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_simple: cfg-simple\n"
        "    model_middle: cfg-middle\n"
        "    model_high: cfg-high\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("LORE_OPENAI_MODEL_SIMPLE", raising=False)
    monkeypatch.setenv("LORE_OPENAI_MODEL_MIDDLE", "env-middle")
    monkeypatch.delenv("LORE_OPENAI_MODEL_HIGH", raising=False)

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["simple"] == "cfg-simple"
    assert tier_to_model["middle"] == "env-middle"
    assert tier_to_model["high"] == "cfg-high"
