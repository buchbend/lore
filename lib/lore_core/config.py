"""Configuration — where the vault lives.

Resolves LORE_ROOT from the environment. Default: ~/lore. Set LORE_ROOT
to the root of any existing markdown vault that matches the canonical
shape (a directory with a `wiki/` subfolder containing one or more
mounted wikis).

Two resolvers are exported:

- :func:`get_lore_root` — silently defaults to ``~/lore`` when env is
  unset. Use when "any LORE_ROOT is fine, just compute it."
- :func:`require_lore_root` — raises :class:`LoreRootNotSet` or
  :class:`LoreRootMissing` when env is unset or points at a missing
  directory. Use in CLI commands and other entrypoints that need the
  user to have explicitly set up a vault.
"""

from __future__ import annotations

import os
from pathlib import Path


class LoreRootError(Exception):
    """Base class for resolver errors raised by :func:`require_lore_root`."""


class LoreRootNotSet(LoreRootError):
    """Raised when ``LORE_ROOT`` is unset or empty in the environment."""


class LoreRootMissing(LoreRootError):
    """Raised when ``LORE_ROOT`` is set but the path does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"LORE_ROOT does not exist: {path}")
        self.path = path


def get_lore_root() -> Path:
    """Resolve the Lore root directory from LORE_ROOT env var or default."""
    env = os.environ.get("LORE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "lore").resolve()


def get_wiki_root() -> Path:
    """Return the wiki mount directory under the Lore root."""
    return get_lore_root() / "wiki"


def require_lore_root() -> Path:
    """Strict version of :func:`get_lore_root`.

    Raises :class:`LoreRootNotSet` if ``LORE_ROOT`` is unset or empty,
    :class:`LoreRootMissing` if it points at a path that does not
    exist. Use in entrypoints that require the user to have explicitly
    set up a vault, where silently falling back to ``~/lore`` would
    mask a configuration error.
    """
    env = os.environ.get("LORE_ROOT")
    if not env:
        raise LoreRootNotSet("LORE_ROOT is not set")
    root = Path(env).expanduser().resolve()
    if not root.exists():
        raise LoreRootMissing(root)
    return root
