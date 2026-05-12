"""Shared pytest fixtures.

The three autouse "grandfather" fixtures that pinned the test suite to
pre-production defaults (``LORE_NOTEWORTHY_MODE=llm_only``,
``LORE_PROJECT_FOLDERS=off``, ``LORE_BUFFER_FLUSH=0``) were removed in
PR 6a of the streamlining track (issue #80). Tests now run under
production defaults; tests that genuinely exercise the legacy paths
opt in inline with ``monkeypatch.setenv``.

The legacy code paths themselves are still in the tree; PRs 6b/6c/6d
delete them once the test suite stops protecting them.
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
