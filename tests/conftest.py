"""Shared pytest fixtures.

The autouse fixture below forces ``LORE_NOTEWORTHY_MODE=llm_only`` on
every test. This is a *grandfather* clause — most existing curator
tests were written against the v0.5 default (LLM decides every
slice's verdict), and v0.6.0 promoted the feature-based cascade to
default. Tests that assert on LLM-request shape or on session notes
produced from minimal fixtures still need the old default to stay
valid.

The cascade default *itself* is exercised by:

* ``tests/test_noteworthy.py::test_resolve_mode_default_is_cascade``
  (and friends 287-341) — unit-level coverage of the resolver.
* ``tests/test_curator_a_cascade_default.py`` — Phase 6 integration
  test that opts *out* of the autouse and verifies an end-to-end
  curator A pass under cascade.

**Phase 6 migration policy:** new tests should *not* depend on the
autouse override. If your test needs ``llm_only``, declare it
explicitly with::

    @pytest.fixture(autouse=False)
    def _force_llm_only(monkeypatch):
        monkeypatch.setenv("LORE_NOTEWORTHY_MODE", "llm_only")

…or set it inline. Old tests are grandfathered; they're only
migrated when their behaviour changes.

Tests that exercise cascade mode locally still work because
``monkeypatch.setenv`` precedence guarantees the per-test override
wins over the autouse.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_noteworthy_mode_llm_only(monkeypatch):
    monkeypatch.setenv("LORE_NOTEWORTHY_MODE", "llm_only")
