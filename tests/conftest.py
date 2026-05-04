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


@pytest.fixture(autouse=True)
def _default_project_folders_off(monkeypatch):
    """Grandfather pre step-9 tests onto the legacy flat-path layout.

    Step-9 of the projects-as-canonical-surface plan flipped the
    production default of ``LORE_PROJECT_FOLDERS`` from off to on. Tests
    written before the flip assume flat ``plans/<slug>.md``,
    ``concepts/<slug>.md`` etc. and would break under the new default.
    They keep the legacy default via this autouse fixture (matches the
    ``LORE_BUFFER_FLUSH`` and ``LORE_NOTEWORTHY_MODE`` patterns).

    Dual-mode tests that want the on-path set
    ``monkeypatch.setenv("LORE_PROJECT_FOLDERS", "on")`` inline; the
    monkeypatch precedence rule guarantees the per-test override wins.
    """
    monkeypatch.setenv("LORE_PROJECT_FOLDERS", "off")


@pytest.fixture(autouse=True)
def _default_buffer_flush_off(monkeypatch):
    """Grandfather pre-PR-3 tests onto the legacy classify-per-chunk path.

    PR 3 of the very-good-thats-the-mossy-lobster plan flipped the
    production default of ``curator.use_buffer_flush`` to ``True``.
    Tests that drive ``run_curator_a`` end-to-end were written against
    the legacy synthesise-on-append path; they keep the legacy default
    via this autouse fixture (matches the noteworthy_mode pattern
    above).

    Tests that exercise the buffer-and-flush path itself — buffer_store
    primitives, buffer_append, stub_note, synthesis, the reaper, the
    SessionEnd / SessionStart wiring — call those modules directly and
    aren't affected by this flag. Tests that want the buffer path
    inside ``run_curator_a`` set ``monkeypatch.setenv("LORE_BUFFER_FLUSH", "1")``
    inline (precedence rule guarantees per-test wins).
    """
    monkeypatch.setenv("LORE_BUFFER_FLUSH", "0")


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory, monkeypatch):
    """Fake ``$HOME`` and wipe ``$XDG_CONFIG_HOME`` for every test so the
    LORE_ROOT resolver never reads the developer's real
    ``~/.config/lore/config.yml`` (issue #6 added a config-file fallback).

    POSIX-only assumption: ``Path.home()`` reads ``$HOME``, so ``setenv``
    suffices. We deliberately do NOT monkeypatch ``pathlib.Path.home``
    directly — the repo has 20+ unrelated callsites (cache, install,
    adapters), and ``briefing/sinks/matrix.py`` evaluates ``Path.home()``
    at import time where a fixture cannot reach it. Limiting the
    isolation to env vars keeps the blast radius bounded.
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield
