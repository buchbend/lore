"""Per-wiki configuration loader for .lore-wiki.yml."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GitConfig:
    auto_commit: bool = False
    auto_push: bool = False
    auto_pull: bool = True


@dataclass
class CuratorCConfig:
    enabled: bool = False
    mode: str = "local"                 # local | central
    defrag_body_writes: bool = False    # gates orphan-link in-place body rewrites


@dataclass
class CuratorConfig:
    threshold_pending: int = 10
    threshold_tokens: int = 50_000
    a_noteworthy_tier: str = "middle"    # middle | simple
    curator_a_cooldown_s: int = 60
    curator_b_cooldown_s: int = 300
    curator_c: CuratorCConfig = field(default_factory=CuratorCConfig)


@dataclass
class ModelsConfig:
    simple: str = "claude-haiku-4-5"
    middle: str = "claude-sonnet-4-6"
    high: str = "claude-opus-4-7"         # or "off"


@dataclass
class BriefingConfig:
    auto: bool = True
    audience: str = "personal"            # personal | team
    sinks: list[str] = field(default_factory=list)


@dataclass
class HeartbeatConfig:
    enabled: bool = True
    cooldown_s: int = 120
    push_context: bool = True             # inject additionalContext with wikilinks

@dataclass
class BreadcrumbConfig:
    mode: str = "normal"                  # quiet | normal | verbose
    scope_filter: bool = True


@dataclass
class WikiConfig:
    git: GitConfig = field(default_factory=GitConfig)
    curator: CuratorConfig = field(default_factory=CuratorConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    breadcrumb: BreadcrumbConfig = field(default_factory=BreadcrumbConfig)


def load_wiki_config(wiki_dir: Path) -> WikiConfig:
    """Load `<wiki_dir>/.lore-wiki.yml` merging over the defaults.

    Missing file → all defaults. Unknown keys → `warnings.warn` but
    config loads. Malformed YAML → defaults + warning (no crash).
    """
    path = wiki_dir / ".lore-wiki.yml"
    if not path.exists():
        return WikiConfig()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        warnings.warn(f"wiki_config: malformed YAML at {path}: {e}", stacklevel=2)
        return WikiConfig()
    if not isinstance(raw, dict):
        warnings.warn(f"wiki_config: top-level must be a mapping at {path}", stacklevel=2)
        return WikiConfig()

    return _merge(WikiConfig(), raw, path)


def _merge(default_obj, overrides: dict[str, Any], source: Path):
    """Recursively overlay `overrides` onto a dataclass default.

    For each key in `overrides`:
      - unknown key on the dataclass → warn, skip.
      - nested dataclass + dict override → recurse.
      - scalar / list → assign after type-cast if trivial.
    """
    if not is_dataclass(default_obj):
        return overrides  # trivial override at scalar/list level
    dc_fields = {f.name: f for f in fields(default_obj)}
    for key, val in overrides.items():
        if key not in dc_fields:
            warnings.warn(
                f"wiki_config: unknown key '{key}' in {source}; ignoring",
                stacklevel=3,
            )
            continue
        current = getattr(default_obj, key)
        if is_dataclass(current) and isinstance(val, dict):
            setattr(default_obj, key, _merge(current, val, source))
        else:
            setattr(default_obj, key, val)
    return default_obj
