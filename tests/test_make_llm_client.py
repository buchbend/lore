"""Tests for make_llm_client factory (Task 5 of Plan 2.5)."""
from __future__ import annotations

import shutil

import pytest

from lore_curator.llm_client import (
    LlmClientError,
    SDKClient,
    SubprocessClient,
    make_llm_client,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_which_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")


def _stub_which_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


class _FakeAnthropicClient:
    """Minimal stand-in for anthropic.Anthropic so SDKClient.__init__ succeeds."""

    class messages:
        @staticmethod
        def create(**kwargs):
            raise NotImplementedError("fake client")


def _stub_anthropic(monkeypatch):
    """Prevent real anthropic.Anthropic() from being called."""
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _FakeAnthropicClient())


# ---------------------------------------------------------------------------
# Explicit "subscription" backend
# ---------------------------------------------------------------------------

def test_factory_explicit_subscription_returns_subprocess_client(monkeypatch):
    _stub_which_found(monkeypatch)
    client = make_llm_client(backend="subscription")
    assert isinstance(client, SubprocessClient)


def test_factory_explicit_subscription_raises_if_binary_missing(monkeypatch):
    _stub_which_missing(monkeypatch)
    with pytest.raises(LlmClientError, match="claude binary not on PATH"):
        make_llm_client(backend="subscription")


# ---------------------------------------------------------------------------
# Explicit "api" backend
# ---------------------------------------------------------------------------

def test_factory_explicit_api_returns_sdk_client(monkeypatch):
    _stub_anthropic(monkeypatch)
    client = make_llm_client(backend="api", api_key="sk-test")
    assert isinstance(client, SDKClient)


def test_factory_explicit_api_raises_without_api_key(monkeypatch):
    with pytest.raises(LlmClientError, match="no ANTHROPIC_API_KEY"):
        make_llm_client(backend="api", api_key=None)


def test_factory_explicit_api_raises_on_empty_key(monkeypatch):
    with pytest.raises(LlmClientError, match="no ANTHROPIC_API_KEY"):
        make_llm_client(backend="api", api_key="")


# ---------------------------------------------------------------------------
# Auto-detection (backend=None / "auto")
# ---------------------------------------------------------------------------

def test_factory_auto_prefers_subprocess_when_available(monkeypatch):
    """Both claude on PATH and api_key set → SubprocessClient wins."""
    _stub_which_found(monkeypatch)
    _stub_anthropic(monkeypatch)
    client = make_llm_client(api_key="sk-test")
    assert isinstance(client, SubprocessClient)


def test_factory_auto_falls_back_to_sdk_when_no_claude_binary(monkeypatch):
    _stub_which_missing(monkeypatch)
    _stub_anthropic(monkeypatch)
    client = make_llm_client(api_key="sk-test")
    assert isinstance(client, SDKClient)


def test_factory_auto_returns_none_when_nothing_available(monkeypatch):
    _stub_which_missing(monkeypatch)
    result = make_llm_client()
    assert result is None


# ---------------------------------------------------------------------------
# LORE_LLM_BACKEND env var
# ---------------------------------------------------------------------------

def test_factory_reads_env_var(monkeypatch):
    """env=api + backend=None → SDKClient."""
    _stub_which_missing(monkeypatch)
    _stub_anthropic(monkeypatch)
    monkeypatch.setenv("LORE_LLM_BACKEND", "api")
    client = make_llm_client(backend=None, api_key="sk-env")
    assert isinstance(client, SDKClient)


def test_factory_explicit_arg_overrides_env(monkeypatch):
    """env says api, arg says subscription → SubprocessClient wins."""
    _stub_which_found(monkeypatch)
    monkeypatch.setenv("LORE_LLM_BACKEND", "api")
    client = make_llm_client(backend="subscription")
    assert isinstance(client, SubprocessClient)


# ---------------------------------------------------------------------------
# Unknown backend string
# ---------------------------------------------------------------------------

def test_factory_raises_on_unknown_backend_string():
    with pytest.raises(ValueError, match="unknown backend"):
        make_llm_client(backend="bogus")


# ---------------------------------------------------------------------------
# LORE_LLM_BACKEND=subscription env path
# ---------------------------------------------------------------------------

def test_factory_reads_env_var_subscription(monkeypatch):
    """LORE_LLM_BACKEND=subscription + no explicit arg → SubprocessClient."""
    monkeypatch.setenv("LORE_LLM_BACKEND", "subscription")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    client = make_llm_client(api_key="sk-x", binary="claude")
    assert isinstance(client, SubprocessClient)


def test_factory_raises_on_uppercase_explicit_arg(monkeypatch):
    """Explicit backend='AUTO' (uppercase) → ValueError, not auto-detect."""
    with pytest.raises(ValueError, match="unknown backend"):
        make_llm_client(backend="AUTO", api_key="sk-x")
