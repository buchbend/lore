"""Root-level Lore config at $LORE_ROOT/.lore/config.yml.

Observability settings are global (not per-wiki) because the log
streams they govern live at $LORE_ROOT/.lore/ and are shared across
wikis.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class HookEventsConfig:
    max_size_mb: int = 10
    keep_rotations: int = 1


@dataclass
class RunsConfig:
    keep: int = 200
    max_total_mb: int = 100
    keep_trace: int = 30


@dataclass
class ObservabilityConfig:
    hook_events: HookEventsConfig = field(default_factory=HookEventsConfig)
    runs: RunsConfig = field(default_factory=RunsConfig)


@dataclass
class RootConfig:
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)


def _merge(target: Any, raw: dict[str, Any], path: str) -> None:
    """Merge raw into target dataclass in place; warn on unknown keys."""
    valid = {f.name for f in fields(target)}
    for key, value in raw.items():
        if key not in valid:
            warnings.warn(f"root_config: unknown key {path}.{key!r}", stacklevel=3)
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value, f"{path}.{key}")
        else:
            setattr(target, key, value)


def load_root_config(lore_root: Path) -> RootConfig:
    """Load $LORE_ROOT/.lore/config.yml over defaults.

    Missing file / missing section / unknown keys → defaults + warning.
    Malformed YAML → defaults + warning (no crash).
    """
    cfg = RootConfig()
    path = lore_root / ".lore" / "config.yml"
    if not path.exists():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        warnings.warn(f"root_config: malformed YAML at {path}: {e}", stacklevel=2)
        return cfg
    if not isinstance(raw, dict):
        warnings.warn(f"root_config: top-level must be a mapping at {path}", stacklevel=2)
        return cfg
    _merge(cfg, raw, "")
    return cfg
