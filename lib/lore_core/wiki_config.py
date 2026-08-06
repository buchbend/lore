"""Per-wiki configuration loader for .lore-wiki.yml."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from lore_core.config_schema import ConfigSchema, FieldInfo
from lore_core.config_schema import get_field as _cs_get_field
from lore_core.config_schema import schema_tree as _cs_schema_tree
from lore_core.config_schema import set_field as _cs_set_field
from lore_core.config_schema import unset_field as _cs_unset_field
from lore_core.config_schema import walk_fields as _cs_walk_fields


@dataclass
class GitConfig:
    auto_commit: bool = False
    auto_push: bool = False
    auto_pull: bool = True


@dataclass
class ModelsConfig:
    simple: str = "claude-haiku-4-5"
    middle: str = "claude-sonnet-4-6"
    high: str = "claude-opus-4-7"  # or "off"


@dataclass
class BriefingConfig:
    auto: bool = True
    audience: str = "personal"  # personal | team
    sinks: list[str] = field(default_factory=list)


@dataclass
class HeartbeatConfig:
    enabled: bool = True
    cooldown_s: int = 120
    push_context: bool = True  # inject additionalContext with wikilinks


@dataclass
class BreadcrumbConfig:
    mode: str = "normal"  # quiet | normal | verbose
    scope_filter: bool = True


@dataclass
class WikiConfig:
    git: GitConfig = field(default_factory=GitConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    breadcrumb: BreadcrumbConfig = field(default_factory=BreadcrumbConfig)


#: Config blocks lore used to honour and no longer does. Named explicitly so
#: a stale file gets a warning that says what happened, rather than the
#: generic unknown-key notice.
RETIRED_BLOCKS = frozenset({"curator"})


def load_wiki_config(wiki_dir: Path) -> WikiConfig:
    """Load `<wiki_dir>/.lore-wiki.yml` merging over the defaults.

    Missing file → all defaults. Unknown keys → `warnings.warn` but
    config loads. Malformed YAML → defaults + warning (no crash).

    ``git.auto_push`` is the one field whose default reads the wiki
    itself: see :func:`_default_auto_push`.
    """
    path = wiki_dir / ".lore-wiki.yml"
    if not path.exists():
        return _with_auto_push_default(WikiConfig(), wiki_dir, set_by_file=False)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        warnings.warn(f"wiki_config: malformed YAML at {path}: {e}", stacklevel=2)
        return _with_auto_push_default(WikiConfig(), wiki_dir, set_by_file=False)
    if not isinstance(raw, dict):
        warnings.warn(f"wiki_config: top-level must be a mapping at {path}", stacklevel=2)
        return _with_auto_push_default(WikiConfig(), wiki_dir, set_by_file=False)

    cfg = _merge(WikiConfig(), raw, path)
    git_block = raw.get("git")
    set_by_file = isinstance(git_block, dict) and "auto_push" in git_block
    return _with_auto_push_default(cfg, wiki_dir, set_by_file=set_by_file)


def _with_auto_push_default(cfg: WikiConfig, wiki_dir: Path, *, set_by_file: bool) -> WikiConfig:
    """Default ``git.auto_push`` to whether the wiki has a git remote.

    A wiki with a remote is a shared vault, and a flag reaches a
    teammate only after a push. A solo wiki has nowhere to push, so the
    same default reads false there. A value written in the file always
    wins over both.
    """
    if not set_by_file:
        from lore_core.git_sync import has_remote

        cfg.git.auto_push = has_remote(wiki_dir)
    return cfg


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
        if key in RETIRED_BLOCKS:
            # One warning for the block, not one per knob inside it: a config
            # written before the teardown carries the whole block, and a
            # warning per key buries the single fact the reader needs.
            warnings.warn(
                f"wiki_config: '{key}' retired with the compose pipeline "
                f"and is ignored; remove it from {source}",
                stacklevel=3,
            )
            continue
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


# ---------------------------------------------------------------------------
# Introspection helpers — backing `lore config get/set/unset/edit --wiki`.
# Thin bindings over lore_core.config_schema's generic dataclass walker;
# mirrors root_config.py's bindings for the vault-root config file.
# ---------------------------------------------------------------------------

WIKI_SCHEMA = ConfigSchema(
    default_factory=WikiConfig,
    load_fn=load_wiki_config,
    config_path_fn=lambda wiki_dir: wiki_dir / ".lore-wiki.yml",
)


def walk_fields(wiki_dir: Path) -> list[FieldInfo]:
    """Yield FieldInfo for every leaf in the resolved WikiConfig."""
    return _cs_walk_fields(WIKI_SCHEMA, wiki_dir)


def get_field(wiki_dir: Path, dotted_path: str) -> FieldInfo:
    """Resolve one dotted path to a FieldInfo. Raises KeyError if absent."""
    return _cs_get_field(WIKI_SCHEMA, wiki_dir, dotted_path)


def set_field(wiki_dir: Path, dotted_path: str, raw_value: str) -> FieldInfo:
    """Persist a value to ``<wiki_dir>/.lore-wiki.yml``.

    Validates ``dotted_path`` against the schema and coerces
    ``raw_value`` to the field's declared type before writing — an
    unknown path or bad value leaves the file on disk unchanged.
    """
    return _cs_set_field(WIKI_SCHEMA, wiki_dir, dotted_path, raw_value)


def unset_field(wiki_dir: Path, dotted_path: str) -> FieldInfo:
    """Remove a persisted override so the field reverts to its default."""
    return _cs_unset_field(WIKI_SCHEMA, wiki_dir, dotted_path)


def schema_tree() -> list[tuple[str, str, Any, str]]:
    """Return [(dotted_path, type_name, default, group_doc), ...] for the
    full WikiConfig schema. Pure introspection — no IO.
    """
    return _cs_schema_tree(WIKI_SCHEMA)
