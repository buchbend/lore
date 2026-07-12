"""Primitives for writing into files and directories Lore does not own.

Every integration Lore installs into (Claude Code, Cursor, …) keeps its
config in files the *user* owns and hand-edits. Writing into them needs
three things this module provides:

  Atomic JSON merge      read-modify-write a shared config under flock,
                         validated after write, symlink-preserving
  Managed markers        a marker-delimited region inside a markdown file
                         that Lore rewrites on upgrade and strips on
                         uninstall, leaving user content outside intact
  Sentinel-gated trees   directory copy / removal that refuses to touch a
                         tree not carrying Lore's provenance marker

Stdlib-only apart from `lore_core.io.atomic_write_text`. Imports nothing
from `lore_cli` or `lore_core.install` — both import from here.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from lore_core.io import atomic_write_text

# `_lore_schema_version` field marker — distinguishes Lore-managed
# blocks from user-authored ones inside shared JSON config files.
SCHEMA_VERSION_KEY = "_lore_schema_version"

# Standard Lore-managed-rules-file marker pair. Anything between these
# two markers is replaced on upgrade and removed on uninstall; anything
# outside the pair is preserved.
MANAGED_BLOCK_START = (
    "<!-- lore-managed-start; uninstall via lore uninstall -->"
)
MANAGED_BLOCK_END = "<!-- lore-managed-end -->"

# Sentinel file written at the root of a lore-managed plugin tree.
# uninstall refuses to remove a directory tree that lacks this marker
# (defends against blowing away unrelated user content if a path
# collision happens).
PLUGIN_SENTINEL = ".lore-managed"


class MalformedConfigError(RuntimeError):
    pass


class ConcurrentEditError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Atomic JSON merge
# ---------------------------------------------------------------------------


def content_hash(text: str) -> str:
    """Stable SHA-256 of UTF-8 bytes, hex digest. Used for change detection."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@contextlib.contextmanager
def _flocked(path: Path) -> Iterator[None]:
    """fcntl.flock context manager on a sibling lock file.

    We lock a sibling ``.lock`` file so the lock survives ``os.replace``
    (which ``atomic_write_text`` does) — locking the target path itself
    would lose the lock when the file is replaced.

    Thin wrapper over ``lore_core.lockfile.flocked``; kept for the
    sibling-path convention specific to ``json_merge_atomic``.
    """
    from lore_core.lockfile import flocked

    lock_path = path.with_suffix(path.suffix + ".lock")
    with flocked(lock_path, blocking=True):
        yield


def _load_json_object(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path``; ``{}`` when absent.

    Refuses malformed JSON and non-object roots so the caller sees a
    clean error instead of a merge that silently drops user config.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise MalformedConfigError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise MalformedConfigError(
            f"{path} root must be a JSON object, got {type(data).__name__}"
        )
    return data


def json_merge_atomic(
    path: Path,
    mutator: callable,  # type: ignore[type-arg]
    validate: callable | None = None,  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Read-modify-write a JSON file under flock, with optional validation.

    `mutator(data: dict) -> dict` returns the new data. If `validate`
    is given, it runs on the freshly-read-back file after write; if
    it returns False, retry the merge once. If still failing, raises
    ConcurrentEditError.

    Resolves symlinks before mutating (so chezmoi/Stow users don't
    have their symlinks replaced with regular files by os.replace).
    Refuses to load malformed JSON — the caller sees a clean error.
    """
    real_path = Path(os.path.realpath(path))
    real_path.parent.mkdir(parents=True, exist_ok=True)

    def _do_one_pass() -> dict[str, Any]:
        new_data = mutator(_load_json_object(real_path))
        atomic_write_text(real_path, json.dumps(new_data, indent=2) + "\n")
        return new_data

    with _flocked(real_path):
        result = _do_one_pass()
        if validate is None:
            return result
        if validate(result):
            return result
        # Validate-after-write failed — retry once
        result = _do_one_pass()
        if validate(result):
            return result
        raise ConcurrentEditError(
            f"{real_path} keys missing after write; concurrent edit detected. "
            "Quit Claude Code (or other writer) and retry."
        )


# ---------------------------------------------------------------------------
# Managed markdown blocks
# ---------------------------------------------------------------------------

_MANAGED_BLOCK_RE = re.compile(
    re.escape(MANAGED_BLOCK_START)
    + r"\n(.*?)\n"
    + re.escape(MANAGED_BLOCK_END)
    + r"\n?",
    re.DOTALL,
)


def write_managed_markdown(path: Path, body: str) -> None:
    """Write a markdown file wrapped in lore-managed-start/end markers.

    Atomic. Creates parent dirs. Resolves symlinks before writing.
    """
    real_path = Path(os.path.realpath(path)) if path.exists() else path
    real_path.parent.mkdir(parents=True, exist_ok=True)
    full = (
        f"{MANAGED_BLOCK_START}\n{body.rstrip()}\n{MANAGED_BLOCK_END}\n"
    )
    atomic_write_text(real_path, full)


def remove_managed_block(path: Path) -> bool:
    """Remove the lore-managed range from a markdown file.

    Preserves any user content outside the managed markers. Returns
    True if a block was removed, False if the file had no managed
    block (no-op). If no content remains outside the block, the file
    is removed entirely.
    """
    real_path = Path(os.path.realpath(path)) if path.exists() else path
    if not real_path.exists():
        return False
    text = real_path.read_text()
    new_text, n = _MANAGED_BLOCK_RE.subn("", text, count=1)
    if n == 0:
        return False
    new_text = new_text.strip()
    if not new_text:
        real_path.unlink()
    else:
        atomic_write_text(real_path, new_text + "\n")
    return True


def managed_block_content(path: Path) -> str | None:
    """Return the text inside lore-managed markers, or None if absent."""
    if not path.exists():
        return None
    m = _MANAGED_BLOCK_RE.search(path.read_text())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Sentinel-gated directory trees
# ---------------------------------------------------------------------------


def copy_dir_atomic(src: Path, dst: Path) -> None:
    """Copy a directory tree from src to dst, idempotent on re-run.

    Uses ``shutil.copytree(dirs_exist_ok=True)`` semantics: re-running
    install overwrites, files removed from src disappear from dst on
    next install only if we wipe-and-recopy, so we wipe first to keep
    dst exactly mirroring src. The wipe is gated on the
    ``PLUGIN_SENTINEL`` file at dst's parent (not dst itself — the
    parent is the plugin root that owns the whole tree).

    Resolves symlinks in src so the dst is always a real tree.
    """
    src_real = Path(os.path.realpath(src))
    if not src_real.is_dir():
        raise FileNotFoundError(f"copy source not found or not a dir: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # Refuse to wipe dst if the plugin root has no sentinel — that
        # means we don't own this tree.
        plugin_root = dst.parent
        if not (plugin_root / PLUGIN_SENTINEL).exists():
            raise PermissionError(
                f"refusing to overwrite {dst}: plugin root {plugin_root} "
                f"has no {PLUGIN_SENTINEL} sentinel (not lore-managed)"
            )
        shutil.rmtree(dst)
    shutil.copytree(src_real, dst, symlinks=False)


def remove_managed_dir(path: Path) -> None:
    """Remove a lore-managed directory tree, gated on the sentinel.

    Only removes ``path`` if a ``PLUGIN_SENTINEL`` file exists at
    ``path`` itself (the plugin root). Any other path raises so we
    never wipe user content by accident.
    """
    real = Path(os.path.realpath(path)) if path.exists() else path
    if not real.exists():
        return
    if not (real / PLUGIN_SENTINEL).exists():
        raise PermissionError(
            f"refusing to remove {real}: no {PLUGIN_SENTINEL} sentinel "
            f"(not lore-managed)"
        )
    shutil.rmtree(real)
