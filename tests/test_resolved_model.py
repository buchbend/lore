"""Tests for ResolvedModel + reasoning_effort plumbing through _resolve_openai_settings.

Covers the new per-tier ``reasoning_effort_{simple,middle,high}`` config
keys and the matching ``LORE_OPENAI_REASONING_EFFORT_{TIER}`` env vars.
Slice 1 of PRD #110 stops at resolver level — on-the-wire forwarding of
``extra_body.reasoning_effort`` to the chat completions endpoint lands
in slice 2.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _write_config(tmp_path: Path, body: str) -> None:
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.yml").write_text(body)


def _clear_reasoning_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LORE_OPENAI_REASONING_EFFORT_SIMPLE",
        "LORE_OPENAI_REASONING_EFFORT_MIDDLE",
        "LORE_OPENAI_REASONING_EFFORT_HIGH",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# ResolvedModel value object basics
# ---------------------------------------------------------------------------


def test_resolved_model_defaults_reasoning_effort_to_none() -> None:
    from lore_curator.llm_client import ResolvedModel

    rm = ResolvedModel(id="some-model")
    assert rm.id == "some-model"
    assert rm.reasoning_effort is None


def test_resolved_model_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    from lore_curator.llm_client import ResolvedModel

    rm = ResolvedModel(id="x")
    with pytest.raises(FrozenInstanceError):
        rm.id = "y"  # type: ignore[misc]


def test_resolved_model_equality_and_hash() -> None:
    """Frozen dataclass → equality + hashable, suitable for set/dict keys."""
    from lore_curator.llm_client import ResolvedModel

    a = ResolvedModel(id="m", reasoning_effort="high")
    b = ResolvedModel(id="m", reasoning_effort="high")
    c = ResolvedModel(id="m", reasoning_effort=None)
    d = ResolvedModel(id="other", reasoning_effort="high")

    assert a == b
    assert a != c
    assert a != d
    assert hash(a) == hash(b)
    # Usable as dict key
    bag = {a: 1}
    bag[b] = 2  # same key
    assert bag == {a: 2}


# ---------------------------------------------------------------------------
# _resolve_openai_settings — reasoning_effort from config
# ---------------------------------------------------------------------------


def test_resolver_returns_reasoning_effort_per_tier_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lore_curator.llm_client import ResolvedModel, _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_simple: m-s\n"
        "    model_middle: m-m\n"
        "    model_high: m-h\n"
        "    reasoning_effort_simple: low\n"
        "    reasoning_effort_middle: medium\n"
        "    reasoning_effort_high: high\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["simple"] == ResolvedModel(id="m-s", reasoning_effort="low")
    assert tier_to_model["middle"] == ResolvedModel(id="m-m", reasoning_effort="medium")
    assert tier_to_model["high"] == ResolvedModel(id="m-h", reasoning_effort="high")


def test_resolver_returns_none_reasoning_effort_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backwards-compat: pre-PR config without the new keys → reasoning_effort=None."""
    from lore_curator.llm_client import ResolvedModel, _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_simple: m-s\n"
        "    model_middle: m-m\n"
        "    model_high: m-h\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["simple"] == ResolvedModel(id="m-s", reasoning_effort=None)
    assert tier_to_model["middle"] == ResolvedModel(id="m-m", reasoning_effort=None)
    assert tier_to_model["high"] == ResolvedModel(id="m-h", reasoning_effort=None)


def test_resolver_empty_reasoning_effort_string_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit empty string == unset; matches the dataclass default-empty convention."""
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_high: m-h\n"
        "    reasoning_effort_high: \"\"\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["high"].reasoning_effort is None


def test_resolver_accepts_uppercase_reasoning_effort_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HIGH`` is a valid spelling and normalizes to lowercase."""
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_high: m-h\n"
        "    reasoning_effort_high: HIGH\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["high"].reasoning_effort == "high"


# ---------------------------------------------------------------------------
# Validation — invalid reasoning_effort values surface clearly
# ---------------------------------------------------------------------------


def test_resolver_raises_on_invalid_reasoning_effort_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lore_curator.llm_client import LlmClientError, _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_middle: m-m\n"
        "    reasoning_effort_middle: strong\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)

    with pytest.raises(LlmClientError) as excinfo:
        _resolve_openai_settings(tmp_path)
    msg = str(excinfo.value)
    assert "middle" in msg
    assert "strong" in msg


def test_resolver_raises_on_invalid_reasoning_effort_env_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad env-var value should surface with the env name in the message."""
    from lore_curator.llm_client import LlmClientError, _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_high: m-h\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)
    monkeypatch.setenv("LORE_OPENAI_REASONING_EFFORT_HIGH", "yes")

    with pytest.raises(LlmClientError) as excinfo:
        _resolve_openai_settings(tmp_path)
    msg = str(excinfo.value)
    assert "high" in msg
    assert "yes" in msg


# ---------------------------------------------------------------------------
# Env precedence — env beats config (same rule as model id)
# ---------------------------------------------------------------------------


def test_env_reasoning_effort_high_beats_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_high: m-h\n"
        "    reasoning_effort_high: medium\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)
    monkeypatch.setenv("LORE_OPENAI_REASONING_EFFORT_HIGH", "high")

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["high"].reasoning_effort == "high"


def test_env_reasoning_effort_partial_leaves_other_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting ENV for HIGH shouldn't blank out other tiers' config values."""
    from lore_curator.llm_client import _resolve_openai_settings

    _write_config(
        tmp_path,
        "curator:\n"
        "  openai:\n"
        "    base_url: https://x.example/v1\n"
        "    model_simple: m-s\n"
        "    model_middle: m-m\n"
        "    model_high: m-h\n"
        "    reasoning_effort_simple: low\n"
        "    reasoning_effort_middle: medium\n"
        "    reasoning_effort_high: low\n",
    )
    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-test")
    _clear_reasoning_env(monkeypatch)
    monkeypatch.setenv("LORE_OPENAI_REASONING_EFFORT_HIGH", "high")

    _, _, tier_to_model = _resolve_openai_settings(tmp_path)
    assert tier_to_model["simple"].reasoning_effort == "low"
    assert tier_to_model["middle"].reasoning_effort == "medium"
    assert tier_to_model["high"].reasoning_effort == "high"
