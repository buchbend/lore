"""Shared pytest fixtures.

Most curator tests were written against the old default of ``llm_only``
— the LLM decides every slice's noteworthy verdict. v0.6.0 promoted the
feature-based cascade to default, which skips the LLM on clearly-trivial
slices. Tests that assert on LLM-request shape or on session notes being
produced from minimal fixtures need the old default to stay valid.

Tests that specifically exercise cascade mode override via
``monkeypatch.setenv`` locally; monkeypatch precedence guarantees the
local override wins.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_noteworthy_mode_llm_only(monkeypatch):
    monkeypatch.setenv("LORE_NOTEWORTHY_MODE", "llm_only")
