"""Generic dataclass-schema validation over YAML-backed config files.

Shared plumbing behind `lore config get/set/unset/edit` for both
`RootConfig` (`root_config.py`) and `WikiConfig` (`wiki_config.py`).
Everything here is generic over "some dataclass tree, loaded from
some YAML file under some base dir" via :class:`ConfigSchema` — a
future slice reuses `validate_raw`/`suggest` for `.lore.yml` offer
validation without needing a third copy of this logic.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class FieldInfo:
    """One leaf field in a resolved config tree. See `walk_fields`."""

    path: str
    value: Any
    default: Any
    type_name: str
    doc: str
    source: str  # "file" | "default"


@dataclass
class ConfigSchema:
    """Binds a dataclass config type to its on-disk file and loader.

    ``load_fn`` is the existing lenient loader (warns on unknown
    keys/malformed YAML, never raises) — reused here for read paths.
    ``config_path_fn`` maps the base dir (lore_root or wiki_dir) to
    the YAML file path.
    """

    default_factory: Callable[[], Any]
    load_fn: Callable[[Path], Any]
    config_path_fn: Callable[[Path], Path]


def suggest(dotted_path: str, valid_paths: list[str], n: int = 3) -> list[str]:
    """Nearest valid dotted paths to `dotted_path`, closest first."""
    return difflib.get_close_matches(dotted_path, valid_paths, n=n)


def _doc_for(parent_cls: type, field_name: str) -> str:
    raw = (parent_cls.__doc__ or "").strip()
    if not raw or raw.startswith(f"{parent_cls.__name__}("):
        return ""
    return raw.splitlines()[0].strip()


def _navigate(obj: Any, parts: list[str]) -> Any:
    cur = obj
    for p in parts:
        if not is_dataclass(cur):
            raise KeyError(f"path enters a non-dataclass at {p!r}")
        if p not in {f.name for f in fields(cur)}:
            raise KeyError(p)
        cur = getattr(cur, p)
    return cur


def _coerce_value(raw: str, target_type: type) -> Any:
    """Coerce a CLI string to a field's declared type.

    Strict — refuses values that don't parse cleanly. ``bool`` accepts
    the usual on/off/yes/no/true/false/0/1 spellings (case-insensitive).
    """
    if target_type is bool:
        s = raw.strip().lower()
        if s in {"true", "yes", "on", "1"}:
            return True
        if s in {"false", "no", "off", "0"}:
            return False
        raise ValueError(f"cannot parse {raw!r} as bool")
    if target_type is int:
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"cannot parse {raw!r} as int") from None
    if target_type is float:
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"cannot parse {raw!r} as float") from None
    if target_type is str:
        return raw
    raise ValueError(
        f"unsupported type {target_type.__name__!r} — only bool/int/float/str "
        f"are settable from the CLI today"
    )


def _all_leaf_paths(obj: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    for f in fields(obj):
        path = f.name if not prefix else f"{prefix}.{f.name}"
        value = getattr(obj, f.name)
        if is_dataclass(value):
            out.extend(_all_leaf_paths(value, path))
        else:
            out.append(path)
    return out


def _unknown_path_error(schema: ConfigSchema, dotted_path: str) -> str:
    valid = _all_leaf_paths(schema.default_factory())
    hint = suggest(dotted_path, valid)
    msg = f"unknown config path: {dotted_path}"
    if hint:
        msg += f" — did you mean: {', '.join(hint)}?"
    return msg


def _walk(obj: Any, prefix: str, raw_at_path: dict | None, defaults_obj: Any) -> list[FieldInfo]:
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
        present_in_file = bool(isinstance(raw_at_path, dict) and f.name in raw_at_path)
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


def _read_raw(cfg_path: Path) -> dict:
    if not cfg_path.exists():
        return {}
    try:
        parsed = yaml.safe_load(cfg_path.read_text())
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def walk_fields(schema: ConfigSchema, base_dir: Path) -> list[FieldInfo]:
    """Yield FieldInfo for every leaf in the resolved config tree."""
    cfg = schema.load_fn(base_dir)
    raw = _read_raw(schema.config_path_fn(base_dir))
    return _walk(cfg, "", raw, schema.default_factory())


def get_field(schema: ConfigSchema, base_dir: Path, dotted_path: str) -> FieldInfo:
    """Resolve one dotted path to a FieldInfo. Raises KeyError if absent."""
    parts = dotted_path.split(".")
    if not parts or not all(parts):
        raise KeyError(dotted_path)
    for fi in walk_fields(schema, base_dir):
        if fi.path == dotted_path:
            return fi
    cfg = schema.load_fn(base_dir)
    try:
        target = _navigate(cfg, parts)
    except KeyError:
        raise KeyError(_unknown_path_error(schema, dotted_path)) from None
    if is_dataclass(target):
        raise KeyError(f"{dotted_path!r} is a config group, not a leaf — try one of its fields")
    raise KeyError(_unknown_path_error(schema, dotted_path))


def set_field(schema: ConfigSchema, base_dir: Path, dotted_path: str, raw_value: str) -> FieldInfo:
    """Persist a value to the schema's config file.

    Validates ``dotted_path`` and coerces ``raw_value`` to the field's
    declared type *before* touching the file — an unknown path or bad
    type leaves the file on disk unchanged. Raises ``KeyError`` for
    unknown paths, ``ValueError`` for type mismatches.
    """
    parts = dotted_path.split(".")
    if not parts or not all(parts):
        raise KeyError(dotted_path)
    cfg = schema.load_fn(base_dir)
    parent = _navigate(cfg, parts[:-1])
    if not is_dataclass(parent):
        raise KeyError(f"path enters a non-dataclass at {parts[-2]!r}")
    field_map = {f.name: f for f in fields(type(parent))}
    if parts[-1] not in field_map:
        raise KeyError(_unknown_path_error(schema, dotted_path))
    field_def = field_map[parts[-1]]
    target_type = (
        field_def.type if isinstance(field_def.type, type) else type(getattr(parent, parts[-1]))
    )
    coerced = _coerce_value(raw_value, target_type)

    cfg_path = schema.config_path_fn(base_dir)
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
    return get_field(schema, base_dir, dotted_path)


def unset_field(schema: ConfigSchema, base_dir: Path, dotted_path: str) -> FieldInfo:
    """Remove a persisted override so the field reverts to its default.

    Validates the path against the schema first — an unknown path
    raises ``KeyError`` and leaves the file untouched. A path that
    resolves but isn't currently overridden is a no-op (already at
    default). Prunes parent mappings left empty by the removal.
    """
    parts = dotted_path.split(".")
    fi = get_field(schema, base_dir, dotted_path)  # validates + suggests

    cfg_path = schema.config_path_fn(base_dir)
    if not cfg_path.exists():
        return fi
    raw = _read_raw(cfg_path)
    cur = raw
    chain: list[tuple[dict, str]] = []
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            return fi  # not present anywhere along the path — no-op
        chain.append((cur, p))
        cur = nxt
    if parts[-1] not in cur:
        return fi  # no-op — already at default
    del cur[parts[-1]]
    for parent, key in reversed(chain):
        if not parent[key]:
            del parent[key]
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False) if raw else "")
    return get_field(schema, base_dir, dotted_path)


def schema_tree(schema: ConfigSchema) -> list[tuple[str, str, Any, str]]:
    """[(dotted_path, type_name, default, group_doc), ...] — pure introspection."""
    cfg = schema.default_factory()
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


def validate_raw(dataclass_type: type, raw: Any) -> list[str]:
    """Strict validation for a parsed-YAML mapping against a dataclass tree.

    Unlike the lenient loaders (which warn and fall back to defaults),
    this returns every problem found — empty list means valid. Used by
    ``lore config edit`` to refuse saving a broken file, and reusable
    by any future writer that needs the same schema for a raw mapping.

    Walks a *default instance* rather than field annotations: every
    `*_config.py` module here uses ``from __future__ import annotations``,
    so ``Field.type`` is a string, not a type object — the existing
    ``set_field``/``_navigate`` code already works around this by
    reading the type off a live default value instead, and this
    follows the same pattern.
    """
    if not isinstance(raw, dict):
        return [f"top-level must be a mapping, got {type(raw).__name__}"]

    default_root = dataclass_type()
    valid_paths = _all_leaf_paths(default_root)
    errors: list[str] = []

    def walk(obj: dict, prefix: str, default_node: Any) -> None:
        field_names = {f.name for f in fields(default_node)}
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            if key not in field_names:
                hint = suggest(path, valid_paths)
                msg = f"unknown config path: {path!r}"
                if hint:
                    msg += f" — did you mean: {', '.join(hint)}?"
                errors.append(msg)
                continue
            default_value = getattr(default_node, key)
            if is_dataclass(default_value):
                if isinstance(value, dict):
                    walk(value, path, default_value)
                else:
                    errors.append(f"{path!r} expects a mapping, got {type(value).__name__}")
                continue
            errors.extend(_type_errors(path, type(default_value), value))

    walk(raw, "", default_root)
    return errors


def _type_errors(path: str, expected_type: type, value: Any) -> list[str]:
    """Type-check one leaf value against its declared type. bool/int are
    kept distinct (bool is an int subclass in Python)."""
    got = type(value).__name__

    def _mismatch(expected_name: str) -> list[str]:
        return [f"{path!r} expects {expected_name}, got {got}"]

    if expected_type is bool:
        return [] if isinstance(value, bool) else _mismatch("bool")
    if expected_type in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _mismatch(expected_type.__name__)
        return []
    if expected_type is str:
        return [] if isinstance(value, str) else _mismatch("str")
    if expected_type is list:
        return [] if isinstance(value, list) else _mismatch("list")
    if expected_type is dict:
        return [] if isinstance(value, dict) else _mismatch("dict")
    return []
