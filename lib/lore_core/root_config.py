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

from lore_core.config_schema import ConfigSchema, FieldInfo
from lore_core.config_schema import get_field as _cs_get_field
from lore_core.config_schema import schema_tree as _cs_schema_tree
from lore_core.config_schema import set_field as _cs_set_field
from lore_core.config_schema import unset_field as _cs_unset_field
from lore_core.config_schema import walk_fields as _cs_walk_fields


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
class ProcConfig:
    keep_generations: int = 3


@dataclass
class RetentionConfig:
    """Tiered janitor policy for the event spine + adjacent log families
    (issue #190).

    Hot tier = the live ``spine.jsonl`` (detailed events); size cap is
    ``hook_events.max_size_mb`` (single source of truth, not duplicated
    here). Cold tier = the rotated ``spine.jsonl.1`` — a hot->cold
    downgrade (age- or size-triggered) moves data there; ``cold_days`` /
    ``cold_max_mb`` bound how long it survives before outright deletion
    (there is no tier below cold).

    ``crash_log_days`` bounds ``$LORE_CACHE/crashes/``. ``dead_letter_hard_cap``
    is the escape valve for flush dead letters: unresolved dead letters are
    exempt from normal age-based retention (a human needs to see them) but
    a permanently-stuck pipeline still can't grow the store forever.
    """

    hot_days: int = 7
    cold_days: int = 30
    cold_max_mb: int = 20
    crash_log_days: int = 30
    dead_letter_hard_cap: int = 50


@dataclass
class ObservabilityConfig:
    hook_events: HookEventsConfig = field(default_factory=HookEventsConfig)
    runs: RunsConfig = field(default_factory=RunsConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    proc: ProcConfig = field(default_factory=ProcConfig)


@dataclass
class OpenAIBackendConfig:
    """Settings for an OpenAI-compatible curator backend (e.g. local model gateways).

    ``base_url`` is the OpenAI-compatible API root (e.g. ``https://chat.kiconnect.nrw/api/v1``).
    ``api_key_env`` names the env var holding the API key — this stays out of
    config files. The recommended persistent home for the key itself is
    ``$LORE_ROOT/.lore/secrets.env`` (see :mod:`lore_core.secrets_env`); that
    file is auto-loaded into ``os.environ`` at curator startup, lives inside
    the gitignored ``.lore/`` directory, and never appears in diffs.
    ``model_{simple,middle,high}`` override the Anthropic tier names; leave empty to fall
    back to the env var ``LORE_OPENAI_MODEL_{SIMPLE,MIDDLE,HIGH}`` or pass-through.

    ``reasoning_effort_{simple,middle,high}`` opt the corresponding tier into a
    reasoning-capable model's effort knob (``"low" | "medium" | "high"``).
    Empty string means "unset" (no reasoning_effort forwarded). The empty-
    string-means-unset convention lets the typed CLI set path (``lore config
    set ...``) and the existing schema walker keep working without learning
    about ``None`` as a YAML/CLI value. Validated and forwarded to the wire
    by ``lore_curator.llm_client._resolve_openai_settings``; values from env
    var ``LORE_OPENAI_REASONING_EFFORT_{SIMPLE,MIDDLE,HIGH}`` win over config.
    """

    base_url: str = ""
    api_key_env: str = "LORE_OPENAI_API_KEY"
    model_simple: str = ""
    model_middle: str = ""
    model_high: str = ""
    reasoning_effort_simple: str = ""
    reasoning_effort_middle: str = ""
    reasoning_effort_high: str = ""


@dataclass
class CuratorBackendConfig:
    """Curator LLM backend selection.

    ``backend`` is one of: ``"auto"`` | ``"subscription"`` | ``"api"`` | ``"openai"``.
    ``auto`` prefers claude-on-PATH → ANTHROPIC_API_KEY → OpenAI (if configured) → None.
    Env var ``LORE_LLM_BACKEND`` and CLI ``--backend`` override this config value.
    """

    backend: str = "auto"
    openai: OpenAIBackendConfig = field(default_factory=OpenAIBackendConfig)


@dataclass
class JournalConfig:
    """AI + human freeform journal feature flag.

    Off by default. Enabling injects a SessionStart prompt fragment
    inviting the model to append to ``journals/ai.md`` when it has
    something noteworthy, and exposes ``lore_journal_{read,write}``
    MCP tools. Distinct from the per-day journal *surface* (auto-
    extracted by Curator B) — see ``project_lore_journal_idea``.
    """

    enabled: bool = False


@dataclass
class TierConfig:
    """Model-tier overrides -- user escape hatch over the shipped tier table.

    Keyed by host then tier, e.g. ``claude: {frontier: claude-opus-4-9}``.
    Only present keys override; everything else falls through to
    ``lore_core.tiers.table.TABLE``. See :func:`lore_core.tiers.resolve_tier`.
    """

    overrides: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class UserConfig:
    """Personal identity, used when a wiki has no team-mode `_users.yml`.

    ``display_name`` names the person in authored session notes and
    briefings instead of falling back to the OS `$USER` login name.
    """

    display_name: str = ""


@dataclass
class RootConfig:
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    curator: CuratorBackendConfig = field(default_factory=CuratorBackendConfig)
    journal: JournalConfig = field(default_factory=JournalConfig)
    tiers: TierConfig = field(default_factory=TierConfig)
    user: UserConfig = field(default_factory=UserConfig)


def _merge(target: Any, raw: dict[str, Any], path: str, source: Path) -> None:
    """Merge raw into target dataclass in place; warn on unknown keys."""
    valid = {f.name for f in fields(target)}
    for key, value in raw.items():
        if key not in valid:
            qualified = f"{path}.{key}" if path else key
            warnings.warn(f"root_config: unknown key {qualified!r} in {source}", stacklevel=3)
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value, f"{path}.{key}" if path else key, source)
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
    _merge(cfg, raw, "", path)
    return cfg


# ---------------------------------------------------------------------------
# Introspection helpers — backing `lore config show / get / set / unset /
# schema`. Thin bindings over lore_core.config_schema's generic dataclass
# walker; see that module's docstring for why it's generic (wiki config and
# a future `.lore.yml` offer-validation slice share the same plumbing).
# ---------------------------------------------------------------------------

ROOT_SCHEMA = ConfigSchema(
    default_factory=RootConfig,
    load_fn=load_root_config,
    config_path_fn=lambda lore_root: lore_root / ".lore" / "config.yml",
)


def walk_fields(lore_root: Path) -> list[FieldInfo]:
    """Yield FieldInfo for every leaf in the resolved RootConfig."""
    return _cs_walk_fields(ROOT_SCHEMA, lore_root)


def get_field(lore_root: Path, dotted_path: str) -> FieldInfo:
    """Resolve one dotted path to a FieldInfo. Raises KeyError if absent."""
    return _cs_get_field(ROOT_SCHEMA, lore_root, dotted_path)


def set_field(lore_root: Path, dotted_path: str, raw_value: str) -> FieldInfo:
    """Persist a value to ``$LORE_ROOT/.lore/config.yml``.

    Validates ``dotted_path`` against the schema and coerces
    ``raw_value`` to the field's declared type before writing — an
    unknown path or bad value leaves the file on disk unchanged.
    Raises ``KeyError`` for unknown paths and ``ValueError`` for type
    mismatches.
    """
    return _cs_set_field(ROOT_SCHEMA, lore_root, dotted_path, raw_value)


def unset_field(lore_root: Path, dotted_path: str) -> FieldInfo:
    """Remove a persisted override so the field reverts to its default."""
    return _cs_unset_field(ROOT_SCHEMA, lore_root, dotted_path)


def schema_tree() -> list[tuple[str, str, Any, str]]:
    """Return [(dotted_path, type_name, default, group_doc), ...] for the
    full RootConfig schema. Pure introspection — no IO.
    """
    return _cs_schema_tree(ROOT_SCHEMA)
