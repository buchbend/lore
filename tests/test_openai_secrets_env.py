"""Integration tests: OpenAI backend reads secrets from $LORE_ROOT/.lore/secrets.env.

Confirms the secrets-env loader is wired into ``_resolve_openai_settings``
and ``make_llm_client``, with the documented precedence:
process env > secrets.env > config.yml.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_core import secrets_env


_TRACKED_KEYS = (
    "LORE_LLM_BACKEND",
    "LORE_OPENAI_API_KEY",
    "LORE_OPENAI_BASE_URL",
    "LORE_OPENAI_MODEL_SIMPLE",
    "LORE_OPENAI_MODEL_MIDDLE",
    "LORE_OPENAI_MODEL_HIGH",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _reset_secrets_and_env():
    """Reset loader cache and snapshot/restore any env keys it might inject.

    ``load_into_environ`` writes to ``os.environ`` directly, which is
    invisible to pytest's monkeypatch — without this snapshot a value
    injected from a tmp secrets.env would leak into later tests.
    """
    import os
    secrets_env.reset_cache()
    snapshot = {k: os.environ.get(k) for k in _TRACKED_KEYS}
    yield
    secrets_env.reset_cache()
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _write_config(tmp_path: Path, body: str) -> None:
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.yml").write_text(body)


def _write_secrets(tmp_path: Path, body: str, mode: int = 0o600) -> None:
    cfg_dir = tmp_path / ".lore"
    cfg_dir.mkdir(exist_ok=True)
    p = cfg_dir / "secrets.env"
    p.write_text(body)
    p.chmod(mode)


def test_resolve_reads_api_key_from_secrets_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lore_curator.llm_client import _resolve_openai_settings

    monkeypatch.delenv("LORE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://x.example/v1")
    _write_secrets(tmp_path, "LORE_OPENAI_API_KEY=sk-from-file\n")

    base_url, api_key, _ = _resolve_openai_settings(tmp_path)
    assert api_key == "sk-from-file"
    assert base_url == "https://x.example/v1"


def test_resolve_reads_base_url_from_secrets_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lore_curator.llm_client import _resolve_openai_settings

    monkeypatch.delenv("LORE_OPENAI_BASE_URL", raising=False)
    _write_secrets(
        tmp_path,
        "LORE_OPENAI_BASE_URL=https://from-file.example/v1\n"
        "LORE_OPENAI_API_KEY=sk-x\n",
    )

    base_url, _, _ = _resolve_openai_settings(tmp_path)
    assert base_url == "https://from-file.example/v1"


def test_process_env_beats_secrets_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user who exported a key in their shell shouldn't be overridden
    by a stale value in the on-disk file."""
    from lore_curator.llm_client import _resolve_openai_settings

    monkeypatch.setenv("LORE_OPENAI_API_KEY", "sk-from-shell")
    monkeypatch.setenv("LORE_OPENAI_BASE_URL", "https://x.example/v1")
    _write_secrets(tmp_path, "LORE_OPENAI_API_KEY=sk-from-file\n")

    _, api_key, _ = _resolve_openai_settings(tmp_path)
    assert api_key == "sk-from-shell"


def test_secrets_env_provides_models_and_config_provides_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shows the layered config: yaml for non-secrets, secrets.env for the key."""
    from lore_curator.llm_client import _resolve_openai_settings

    monkeypatch.delenv("LORE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LORE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LORE_OPENAI_MODEL_SIMPLE", raising=False)
    monkeypatch.delenv("LORE_OPENAI_MODEL_MIDDLE", raising=False)
    monkeypatch.delenv("LORE_OPENAI_MODEL_HIGH", raising=False)

    _write_config(
        tmp_path,
        "curator:\n"
        "  backend: openai\n"
        "  openai:\n"
        "    base_url: https://gateway.example/v1\n"
        "    model_simple: tiny\n"
        "    model_middle: mid\n"
        "    model_high: big\n",
    )
    _write_secrets(tmp_path, "LORE_OPENAI_API_KEY=sk-secret\n")

    base_url, api_key, tier_to_model = _resolve_openai_settings(tmp_path)
    assert base_url == "https://gateway.example/v1"
    assert api_key == "sk-secret"
    assert tier_to_model["simple"].id == "tiny"
    assert tier_to_model["middle"].id == "mid"
    assert tier_to_model["high"].id == "big"


def test_make_llm_client_picks_up_backend_from_secrets_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LORE_LLM_BACKEND in secrets.env triggers the openai branch."""
    from lore_curator.llm_client import OpenAICompatibleClient, make_llm_client

    # Stub out the actual openai SDK construction so we don't need it.
    class _Stub:
        def __init__(self, **kw):
            self.kw = kw

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise NotImplementedError

    import sys
    import types
    fake = types.ModuleType("openai")
    fake.OpenAI = _Stub
    monkeypatch.setitem(sys.modules, "openai", fake)

    monkeypatch.delenv("LORE_LLM_BACKEND", raising=False)
    monkeypatch.delenv("LORE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LORE_OPENAI_BASE_URL", raising=False)
    _write_secrets(
        tmp_path,
        "LORE_LLM_BACKEND=openai\n"
        "LORE_OPENAI_BASE_URL=https://x.example/v1\n"
        "LORE_OPENAI_API_KEY=sk-via-file\n",
    )

    client = make_llm_client(lore_root=tmp_path)
    assert isinstance(client, OpenAICompatibleClient)
