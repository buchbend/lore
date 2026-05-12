"""Shared pytest fixtures.

The three autouse "grandfather" fixtures that pinned the test suite to
pre-production defaults (``LORE_NOTEWORTHY_MODE=llm_only``,
``LORE_PROJECT_FOLDERS=off``, ``LORE_BUFFER_FLUSH=0``) were removed in
PR 6a of the streamlining track (issue #80). PR 6b deleted the
buffer-flush legacy code path and the ``LORE_BUFFER_FLUSH`` env var;
PR 6c deleted the ``LORE_NOTEWORTHY_MODE=llm_only`` branch and env var.
The remaining ``LORE_PROJECT_FOLDERS=off`` legacy is slated for PR 6d.
Tests that genuinely exercise it opt in inline with ``monkeypatch.setenv``.
"""
from __future__ import annotations

import pytest


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
