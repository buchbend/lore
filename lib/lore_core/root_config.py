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
class ProcConfig:
    keep_generations: int = 3


@dataclass
class ObservabilityConfig:
    hook_events: HookEventsConfig = field(default_factory=HookEventsConfig)
    runs: RunsConfig = field(default_factory=RunsConfig)
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
    """

    base_url: str = ""
    api_key_env: str = "LORE_OPENAI_API_KEY"
    model_simple: str = ""
    model_middle: str = ""
    model_high: str = ""


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
class RootConfig:
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    curator: CuratorBackendConfig = field(default_factory=CuratorBackendConfig)
    journal: JournalConfig = field(default_factory=JournalConfig)


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
# Introspection helpers — backing `lore config show / get / set / schema`.
# ---------------------------------------------------------------------------


@dataclass
class FieldInfo:
    """One leaf field in the resolved RootConfig tree.

    Used by ``walk_fields`` and the ``lore config`` subcommands. ``source``
    is one of ``"file"`` (present in the loaded YAML) or ``"default"``
    (using the dataclass default). Env-var overrides are deliberately not
    chased here — the per-call resolvers (e.g. ``_resolve_backend``)
    interpret env vars and would require duplicating their logic to
    surface them as a field source. The docstrings on each *Config
    dataclass already document the env-var override for that field.
    """

    path: str
    value: Any
    default: Any
    type_name: str
    doc: str
    source: str


def _doc_for(parent_cls: type, field_name: str) -> str:
    """Best-effort one-line docstring for a field.

    Strategy: take the parent dataclass docstring if any (covers
    nested-config groups), trimmed to one line. Field-level docs live
    as inline ``# ...`` comments on the dataclass body which Python
    introspection can't reach without source parsing — out of scope
    here. Callers that need richer descriptions read the source file.

    Filters out the dataclass auto-generated ``Cls(field: type = ...,)``
    signature that appears when no explicit docstring is set.
    """
    raw = (parent_cls.__doc__ or "").strip()
    if not raw:
        return ""
    # Auto-generated signature strings start with "<ClassName>(".
    if raw.startswith(f"{parent_cls.__name__}("):
        return ""
    return raw.splitlines()[0].strip()


def _walk(
    obj: Any, prefix: str, raw_at_path: dict | None, defaults_obj: Any
) -> "list[FieldInfo]":
    out: list[FieldInfo] = []
    cls = type(obj)
    for f in fields(obj):
        path = f.name if not prefix else f"{prefix}.{f.name}"
        value = getattr(obj, f.name)
        default_value = getattr(defaults_obj, f.name)
        if is_dataclass(value):
            sub_raw = None
            if isinstance(raw_at_path, dict) and isinstance(raw_at_path.get(f.name), dict):
                sub_raw = raw_at_path[f.name]
            out.extend(_walk(value, path, sub_raw, default_value))
            continue
        present_in_file = bool(
            isinstance(raw_at_path, dict) and f.name in raw_at_path
        )
        out.append(
            FieldInfo(
                path=path,
                value=value,
                default=default_value,
                type_name=type(value).__name__,
                doc=_doc_for(cls, f.name),
                source="file" if present_in_file else "default",
            )
        )
    return out


def walk_fields(lore_root: Path) -> "list[FieldInfo]":
    """Yield FieldInfo for every leaf in the resolved RootConfig.

    Reads ``$LORE_ROOT/.lore/config.yml`` (if present) to mark which
    fields were explicitly set vs. defaulted. Result is suitable for
    rendering by ``lore config show``.
    """
    cfg = load_root_config(lore_root)
    path = lore_root / ".lore" / "config.yml"
    raw: dict | None = None
    if path.exists():
        try:
            parsed = yaml.safe_load(path.read_text())
            if isinstance(parsed, dict):
                raw = parsed
        except yaml.YAMLError:
            raw = None
    return _walk(cfg, "", raw, RootConfig())


def _navigate(obj: Any, parts: list[str]) -> Any:
    cur = obj
    for p in parts:
        if not is_dataclass(cur):
            raise KeyError(f"path enters a non-dataclass at {p!r}")
        if p not in {f.name for f in fields(cur)}:
            raise KeyError(p)
        cur = getattr(cur, p)
    return cur


def get_field(lore_root: Path, dotted_path: str) -> FieldInfo:
    """Resolve one dotted path to a FieldInfo. Raises KeyError if absent."""
    parts = dotted_path.split(".")
    if not parts or not all(parts):
        raise KeyError(dotted_path)
    for fi in walk_fields(lore_root):
        if fi.path == dotted_path:
            return fi
    # Try to give a useful error: did the path navigate into the schema
    # but stop at a dataclass (i.e. a group rather than a leaf)?
    cfg = load_root_config(lore_root)
    try:
        target = _navigate(cfg, parts)
    except KeyError:
        raise KeyError(f"unknown config path: {dotted_path}") from None
    if is_dataclass(target):
        raise KeyError(
            f"{dotted_path!r} is a config group, not a leaf — "
            f"try one of its fields"
        )
    raise KeyError(f"unknown config path: {dotted_path}")


def _coerce_value(raw: str, target_type: type) -> Any:
    """Coerce a CLI string to the dataclass field's expected type.

    Strict — refuses to set a typed field with a value that doesn't
    parse cleanly. ``bool`` accepts the usual on/off/yes/no/true/false/0/1
    spellings (case-insensitive); ``int`` and ``float`` use Python
    parsers; ``str`` is pass-through.
    """
    if target_type is bool:
        s = raw.strip().lower()
        if s in {"true", "yes", "on", "1"}:
            return True
        if s in {"false", "no", "off", "0"}:
            return False
        raise ValueError(f"cannot parse {raw!r} as bool")
    if target_type is int:
        return int(raw)
    if target_type is float:
        return float(raw)
    if target_type is str:
        return raw
    raise ValueError(
        f"unsupported type {target_type.__name__!r} — only bool/int/float/str "
        f"are settable from the CLI today"
    )


def set_field(lore_root: Path, dotted_path: str, raw_value: str) -> FieldInfo:
    """Persist a value to ``$LORE_ROOT/.lore/config.yml``.

    Validates ``dotted_path`` against the schema and coerces
    ``raw_value`` to the field's declared type before writing.
    Existing file content is preserved at the YAML node level (other
    keys untouched), but inline comments may be lost — PyYAML doesn't
    round-trip them. Returns the FieldInfo for the updated value.

    Raises ``KeyError`` for unknown paths and ``ValueError`` for type
    mismatches (the underlying ``_coerce_value`` failure surfaces with
    its message).
    """
    parts = dotted_path.split(".")
    if not parts or not all(parts):
        raise KeyError(dotted_path)
    # Validate the path resolves to a dataclass leaf.
    cfg = load_root_config(lore_root)
    parent = _navigate(cfg, parts[:-1])
    if not is_dataclass(parent):
        raise KeyError(f"path enters a non-dataclass at {parts[-2]!r}")
    parent_cls = type(parent)
    field_map = {f.name: f for f in fields(parent_cls)}
    if parts[-1] not in field_map:
        raise KeyError(f"unknown config path: {dotted_path}")
    field_def = field_map[parts[-1]]
    coerced = _coerce_value(raw_value, field_def.type if isinstance(field_def.type, type) else type(getattr(parent, parts[-1])))
    # Read existing YAML, mutate the nested dict, write back. Missing
    # parents are created on demand.
    cfg_path = lore_root / ".lore" / "config.yml"
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            parsed = yaml.safe_load(cfg_path.read_text())
            if isinstance(parsed, dict):
                raw = parsed
        except yaml.YAMLError as exc:
            raise RuntimeError(f"cannot parse {cfg_path}: {exc}") from exc
    cur = raw
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = coerced
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return get_field(lore_root, dotted_path)


def schema_tree() -> "list[tuple[str, str, Any, str]]":
    """Return [(dotted_path, type_name, default, group_doc), ...] for the
    full RootConfig schema. Pure introspection — no IO.

    Group docstrings appear once per group with empty path-prefix
    semantics: the leaf rows carry the closest enclosing group's
    docstring as their `group_doc`. Suitable for ``lore config schema``.
    """
    cfg = RootConfig()
    rows: list[tuple[str, str, Any, str]] = []

    def visit(obj: Any, prefix: str) -> None:
        for f in fields(obj):
            path = f.name if not prefix else f"{prefix}.{f.name}"
            value = getattr(obj, f.name)
            if is_dataclass(value):
                visit(value, path)
                continue
            rows.append((path, type(value).__name__, value, _doc_for(type(obj), f.name)))

    visit(cfg, "")
    return rows
