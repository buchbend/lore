"""Configuration — where the vault lives.

Resolves LORE_ROOT from the environment. Default: ~/lore. Set LORE_ROOT
to the root of any existing markdown vault that matches the canonical
shape (a directory with a `wiki/` subfolder containing one or more
mounted wikis).
"""

from __future__ import annotations

import os
from pathlib import Path


def get_lore_root() -> Path:
    """Resolve the Lore root directory from LORE_ROOT env var or default."""
    env = os.environ.get("LORE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "lore").resolve()


def get_wiki_root() -> Path:
    """Return the wiki mount directory under the Lore root."""
    return get_lore_root() / "wiki"
