"""Shared pytest fixtures.

The three autouse "grandfather" fixtures that pinned the test suite to
pre-production defaults (``LORE_NOTEWORTHY_MODE=llm_only``,
``LORE_PROJECT_FOLDERS=off``, ``LORE_BUFFER_FLUSH=0``) were removed in
PR 6a of the streamlining track (issue #80). PRs 6b/6c/6d deleted the
underlying legacy branches and env vars (buffer-flush,
``noteworthy_mode=llm_only``, ``LORE_PROJECT_FOLDERS=off``), closing
out the grandfather track.
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
    adapters), some of them evaluated at import time where a fixture
    cannot reach them. Limiting the isolation to env vars keeps the blast
    radius bounded.
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield


def seed_scope(path, wiki: str, name: str | None = None) -> None:
    """Route ``path`` to ``wiki`` with a scope in the person's config (PRD 0016).

    Merges into ``~/.config/lore/config.yml`` under the fake ``$HOME``.
    Import with ``from conftest import seed_scope``.
    """
    from pathlib import Path

    import yaml
    from lore_core.config import user_config_path

    cfg_path = user_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = (yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else None) or {}
    cfg.setdefault("scopes", {})[name or Path(path).name] = {"path": str(path), "wiki": wiki}
    cfg_path.write_text(yaml.safe_dump(cfg))
