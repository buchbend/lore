"""Tests for SDKClient — the thin anthropic.Anthropic wrapper."""
from __future__ import annotations

import sys

import pytest

from lore_curator.llm_client import SDKClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeMessages:
    """Records calls to .create()."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return object()  # callers only need a truthy return value here


class _FakeAnthropic:
    """Mimics anthropic.Anthropic constructor + .messages attribute."""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sdk_client_passes_through_to_anthropic(monkeypatch):
    """SDKClient delegates .messages.create kwargs straight to Anthropic."""
    monkeypatch.setattr("anthropic.Anthropic", _FakeAnthropic)

    client = SDKClient(api_key="test-key")

    # The fake Anthropic should have been constructed with the api_key.
    assert isinstance(client._anthropic, _FakeAnthropic)
    assert client._anthropic.init_kwargs == {"api_key": "test-key"}

    # Calling .messages.create should be forwarded to the fake messages object.
    create_kwargs = {"model": "claude-3-5-haiku-20241022", "max_tokens": 1024, "messages": []}
    client.messages.create(**create_kwargs)

    assert client._anthropic.messages.calls == [create_kwargs]


def test_sdk_client_backend_name_is_sdk(monkeypatch):
    """SDKClient.backend_name returns 'sdk'."""
    monkeypatch.setattr("anthropic.Anthropic", _FakeAnthropic)

    client = SDKClient(api_key="x")
    assert client.backend_name == "sdk"


def test_sdk_client_raises_if_anthropic_missing(monkeypatch):
    """SDKClient raises ImportError when the anthropic package is absent."""
    # Setting sys.modules["anthropic"] = None causes `import anthropic` inside
    # SDKClient.__init__ to raise ModuleNotFoundError (a subclass of ImportError).
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(ImportError):
        SDKClient(api_key="x")
