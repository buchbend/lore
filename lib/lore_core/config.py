"""Configuration — where the vault lives.

Resolves LORE_ROOT in this order:

1. ``$LORE_ROOT`` env var (whitespace-stripped, non-empty)
2. ``~/.config/lore/config.yml`` (or ``$XDG_CONFIG_HOME/lore/config.yml``)
   with a top-level ``lore_root: <path>`` key
3. Default ``~/lore``

The config-file fallback exists for hosts where setting an env var
per-shell isn't ergonomic (Cursor, Codex, Gemini — see issue #6).

Four resolvers are exported:

- :func:`resolve_lore_root` — returns ``Path | None``; ``None`` iff
  neither env nor config-file provides a value. Use when the question
  is "did the user explicitly configure a vault anywhere?"
- :func:`get_lore_root` — silently defaults to ``~/lore`` when neither
  env nor config-file is set. Use when "any path is fine, just compute
  one."
- :func:`require_lore_root` — raises :class:`LoreRootNotConfigured` or
  :class:`LoreRootMissing`. Use in CLI entrypoints that need a real
  vault present.
- :func:`lore_root_source` — debug/display only; returns which source
  the path came from. Provenance is *not* propagated to subprocesses
  (children see ``LORE_ROOT`` injected by their parent and report
  ``"env"``). Do not branch on this — use ``resolve_lore_root`` instead.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Literal

import yaml


class LoreRootError(Exception):
    """Base class for resolver errors raised by :func:`require_lore_root`."""


class LoreRootNotConfigured(LoreRootError):
    """Neither ``$LORE_ROOT`` nor ``~/.config/lore/config.yml`` provides a value."""


# Deprecated alias — kept one release for any external code catching it.
LoreRootNotSet = LoreRootNotConfigured


class LoreRootMissing(LoreRootError):
    """A value was provided but the resolved path doesn't exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"LORE_ROOT does not exist: {path}")
        self.path = path


# ---------------------------------------------------------------------------
# Config-file (~/.config/lore/config.yml) reader
# ---------------------------------------------------------------------------


def user_config_path() -> Path:
    """Resolve ``$XDG_CONFIG_HOME/lore/config.yml`` (or ``~/.config/lore/config.yml``).

    Per the XDG spec, ``$XDG_CONFIG_HOME`` set to empty / whitespace-only /
    a relative path is treated as unset (fall back to ``$HOME/.config``).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    xdg_path = Path(xdg) if xdg else None
    if xdg_path is None or not xdg_path.is_absolute():
        xdg_path = Path.home() / ".config"
    return xdg_path / "lore" / "config.yml"


def _read_lore_root_from_config() -> Path | None:
    """Read ``lore_root`` from the user config file, or return ``None``.

    Failure modes (all return ``None``; warn where the file is present
    but unusable):

    - File absent → silent
    - OSError reading (directory, symlink loop, EACCES) → warn
    - Malformed YAML → warn
    - Top-level not a mapping → warn
    - Unknown top-level keys → warn (but still try ``lore_root``)
    - ``lore_root`` absent / null → silent (file may exist for future keys)
    - ``lore_root`` non-string / empty / whitespace-only → warn (non-string only)
    """
    path = user_config_path()
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        warnings.warn(f"config: cannot read {path}: {e}", stacklevel=3)
        return None
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        warnings.warn(f"config: malformed YAML at {path}: {e}", stacklevel=3)
        return None
    if not isinstance(raw, dict):
        warnings.warn(
            f"config: top-level must be a mapping at {path}", stacklevel=3
        )
        return None
    for key in raw:
        if key != "lore_root":
            warnings.warn(f"config: unknown key {key!r} in {path}", stacklevel=3)
    value = raw.get("lore_root")
    if value is None:
        return None  # missing or explicit-null — silent (forward-compat)
    if not isinstance(value, str):
        warnings.warn(
            f"config: lore_root must be a string, got {type(value).__name__}",
            stacklevel=3,
        )
        return None
    value = value.strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

# DO NOT @cache — both env and config file may change within a process
# lifetime (long-running hooks; tests using monkeypatch). Re-resolution
# on every call is intentional. If perf becomes a concern, cache with
# an mtime-based invalidation key, not unconditionally.


def _resolve_lore_root() -> tuple[Path | None, Literal["env", "config"] | None]:
    """Single source of truth for resolution precedence.

    Returns ``(path, source)`` where ``path`` is ``None`` iff neither
    env nor config-file provides a value. Path is ``.expanduser().resolve()``;
    existence is *not* checked here.
    """
    env = os.environ.get("LORE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve(), "env"
    config = _read_lore_root_from_config()
    if config is not None:
        return config, "config"
    return None, None


def resolve_lore_root() -> Path | None:
    """Return the configured Lore root, or ``None`` if unconfigured.

    Use when the question is "did the user explicitly configure a vault
    anywhere?" — env or config-file both count as configured.
    """
    path, _ = _resolve_lore_root()
    return path


def get_lore_root() -> Path:
    """Resolve the Lore root, falling back to ``~/lore``. Always returns a path."""
    path, _ = _resolve_lore_root()
    return path or (Path.home() / "lore").resolve()


def get_wiki_root() -> Path:
    """Return the wiki mount directory under the Lore root."""
    return get_lore_root() / "wiki"


def list_wikis(lore_root: Path) -> list[Path]:
    """Return every wiki directory under ``lore_root``, sorted by name.

    The package's one wiki enumerator. Returns ``[]`` for a vault with no
    ``wiki/`` directory. Symlinks are followed, so a wiki mounted from
    elsewhere in the filesystem still counts as a directory.
    """
    wiki_root = lore_root / "wiki"
    if not wiki_root.is_dir():
        return []
    return [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]


def require_lore_root() -> Path:
    """Strict resolver — raises if unconfigured or path missing.

    Raises :class:`LoreRootNotConfigured` if neither env nor config-file
    provides a value, :class:`LoreRootMissing` if the resolved path does
    not exist on disk. Use in entrypoints that need a real vault.
    """
    path, _ = _resolve_lore_root()
    if path is None:
        raise LoreRootNotConfigured(
            "LORE_ROOT not configured "
            "(neither $LORE_ROOT nor ~/.config/lore/config.yml is set)"
        )
    if not path.exists():
        raise LoreRootMissing(path)
    return path


def lore_root_source() -> Literal["env", "config", "default"]:
    """Debug/display only — DO NOT branch on this for control flow.

    Returns which source resolved the value: ``"env"`` if ``$LORE_ROOT``
    was set, ``"config"`` if the config file provided it, ``"default"``
    if neither (caller falls back to ``~/lore``).

    Provenance is per-process; subprocesses spawned with ``LORE_ROOT``
    injected into their env see ``"env"`` regardless of how the parent
    resolved. Use :func:`resolve_lore_root` when you need "is this
    configured?" — that question survives process boundaries.
    """
    _, source = _resolve_lore_root()
    return source or "default"
