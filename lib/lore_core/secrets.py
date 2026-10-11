"""Secrets file: ``~/.config/lore/secrets.yml`` (PRD 0016, ADR 0016).

Config files name a secret and hold no value. The values live here, in a
mode ``0600`` file that Lore refuses to read from inside a git worktree.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from lore_core.config import user_config_path


class SecretsError(Exception):
    """The secrets file is unsafe or unreadable."""


def secrets_path() -> Path:
    return user_config_path().parent / "secrets.yml"


def _check_outside_worktree(path: Path) -> None:
    start = path.parent.resolve()
    if any((p / ".git").exists() for p in (start, *start.parents)):
        raise SecretsError(f"refusing to use a secrets file inside a git worktree: {path}")


def read_secrets() -> dict[str, str]:
    """Return ``{name: value}``; ``{}`` when the file is absent."""
    path = secrets_path()
    _check_outside_worktree(path)
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise SecretsError(f"cannot read {path}: {e}") from None
    if not isinstance(raw, dict):
        raise SecretsError(f"top-level must be a mapping in {path}")
    return {str(k): str(v) for k, v in raw.items()}


def _write(secrets: dict[str, str]) -> None:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yml.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, yaml.safe_dump(secrets, sort_keys=True).encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


def set_secret(name: str, value: str) -> None:
    secrets = read_secrets()
    secrets[name] = value
    _write(secrets)


def unset_secret(name: str) -> bool:
    """Remove ``name``. Returns False when it was not set."""
    secrets = read_secrets()
    if secrets.pop(name, None) is None:
        return False
    _write(secrets)
    return True
