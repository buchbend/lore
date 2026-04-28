"""Lazy KEY=VALUE secrets file loader.

Lore's curator backends (OpenAI-compatible gateways, Anthropic API) need
secret values that don't belong in ``$LORE_ROOT/.lore/config.yml`` — that
file is meant to be readable, diffable, and shareable. The convention in
the Python ecosystem for "env vars that persist on disk" is dotenv: a
file of ``KEY=VALUE`` lines that the app reads at startup and merges into
``os.environ``.

This module is the minimal version of that pattern. It does **not** depend
on ``python-dotenv``; the parser is small and dedicated to our needs:

* one ``KEY=VALUE`` per line
* leading/trailing whitespace tolerated
* values may be wrapped in single or double quotes (stripped on read)
* lines starting with ``#`` are comments
* blank lines are skipped
* malformed lines emit a warning and are skipped (no crash — the curator
  falling silent because of a stray ``=`` would be worse than logging)

The canonical file is ``$LORE_ROOT/.lore/secrets.env``. ``$LORE_ROOT/.lore/``
is already gitignored at the vault level, so the secret is safe from the
default ``git add -A`` flow. We additionally warn (once) if the file's
mode is wider than 0600 — readable by group/other is a common mistake
when copying configs around.

Loading is lazy and cached per-path: tests (and curator threads with
different lore_roots) can safely call ``load_into_environ()`` repeatedly.
"""

from __future__ import annotations

import os
import stat
import warnings
from pathlib import Path
from typing import Iterable

# Per-path cache. Key is the absolute resolved path; value is the parsed
# dict. We cache the dict (not the injection result) so repeated calls
# from different contexts can re-inject without re-reading the file.
_CACHE: dict[str, dict[str, str]] = {}

# Paths we've already warned about for permissive modes. One warning per
# path per process is enough — repeated warnings would just be noise.
_WARNED_PERMISSIVE: set[str] = set()


def parse(text: str, *, source: str = "<string>") -> dict[str, str]:
    """Parse dotenv-style text into a ``dict[str, str]``.

    See module docstring for the supported grammar. ``source`` is used
    only to make warning messages locatable.
    """
    result: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            warnings.warn(
                f"secrets_env: skipping malformed line {source}:{lineno} "
                f"(no '=' separator): {raw!r}",
                stacklevel=3,
            )
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not _is_valid_key(key):
            warnings.warn(
                f"secrets_env: skipping malformed line {source}:{lineno} "
                f"(invalid key): {raw!r}",
                stacklevel=3,
            )
            continue
        value = value.strip()
        # Strip surrounding quotes if matched on both sides.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def _is_valid_key(key: str) -> bool:
    """Env var keys: ASCII letter/underscore start, then alnum/underscore."""
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in key)


def load_file(path: Path) -> dict[str, str]:
    """Read and parse a secrets file. Returns ``{}`` if the file is missing.

    Result is cached by resolved absolute path. Permissive file modes
    trigger a one-time warning per path.
    """
    if not path.exists():
        return {}
    resolved = str(path.resolve())
    if resolved in _CACHE:
        return _CACHE[resolved]
    try:
        st = path.stat()
    except OSError as exc:
        warnings.warn(f"secrets_env: cannot stat {path}: {exc}", stacklevel=2)
        return {}
    # Warn if group or world bits are set on a regular file. We don't
    # auto-chmod — that's the user's tree, not ours to silently change.
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077 and resolved not in _WARNED_PERMISSIVE:
        warnings.warn(
            f"secrets_env: {path} is mode {oct(mode)}; "
            f"recommend `chmod 600 {path}` (currently readable by group/other)",
            stacklevel=2,
        )
        _WARNED_PERMISSIVE.add(resolved)
    try:
        text = path.read_text()
    except OSError as exc:
        warnings.warn(f"secrets_env: cannot read {path}: {exc}", stacklevel=2)
        return {}
    parsed = parse(text, source=str(path))
    _CACHE[resolved] = parsed
    return parsed


def secrets_path(lore_root: Path | None) -> Path:
    """Resolve the canonical secrets.env path under a Lore root.

    When ``lore_root`` is ``None``, falls back to :func:`get_lore_root`
    (which itself honours env → config-file → ``~/lore`` default).
    Always returns a path; the caller is expected to check ``.exists()``
    if needed. Loading from the path is safe regardless — ``load_file``
    no-ops when the file doesn't exist.

    Behavior change (issue #6): previously returned ``None`` when neither
    explicit ``lore_root`` nor ``$LORE_ROOT`` env var was set. Now
    consistent with the rest of the resolver layer — config-file
    fallback applies uniformly.
    """
    if lore_root is None:
        from lore_core.config import get_lore_root
        lore_root = get_lore_root()
    return lore_root / ".lore" / "secrets.env"


def load_into_environ(
    lore_root: Path | None = None,
    *,
    keys: Iterable[str] | None = None,
) -> dict[str, str]:
    """Load secrets.env into ``os.environ`` for keys not already set.

    Existing process env vars always win — a user who already exported
    ``LORE_OPENAI_API_KEY`` in their shell shouldn't be surprised by a
    stale file overriding it. This also makes tests easy: set the env
    var, the loader is a no-op.

    ``keys`` optionally restricts which keys from the file get injected.
    Returns the dict of keys that were actually injected (useful for
    debugging in doctor / trace logs).
    """
    path = secrets_path(lore_root)
    parsed = load_file(path)
    if not parsed:
        return {}
    injected: dict[str, str] = {}
    allow = set(keys) if keys is not None else None
    for key, value in parsed.items():
        if allow is not None and key not in allow:
            continue
        if key in os.environ and os.environ[key].strip():
            continue
        os.environ[key] = value
        injected[key] = value
    return injected


def reset_cache() -> None:
    """Clear the per-path cache. Test helper; not used in production."""
    _CACHE.clear()
    _WARNED_PERMISSIVE.clear()
