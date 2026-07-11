"""Tests for the generic dataclass-schema validation helpers.

These back `lore config get/set/unset/edit` for both root and per-wiki
config today; a future slice reuses `validate_raw` for `.lore.yml`
offer validation, so the tests here use a throwaway schema rather than
RootConfig/WikiConfig to keep the module's genericity honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lore_core.config_schema import suggest, validate_raw


@dataclass
class _Sub:
    count: int = 3
    label: str = "x"


@dataclass
class _Dummy:
    enabled: bool = False
    ratio: float = 1.0
    tags: list[str] = field(default_factory=list)
    sub: _Sub = field(default_factory=_Sub)


def test_suggest_finds_nearest_valid_path():
    valid = ["enabled", "ratio", "sub.count", "sub.label"]
    assert suggest("enabld", valid) == ["enabled"]


def test_suggest_returns_empty_for_no_close_match():
    valid = ["enabled", "ratio"]
    assert suggest("zzzzzzzzzz", valid) == []


def test_validate_raw_accepts_matching_types():
    raw = {"enabled": True, "ratio": 2.5, "tags": ["a"], "sub": {"count": 5, "label": "y"}}
    assert validate_raw(_Dummy, raw) == []


def test_validate_raw_unknown_top_level_key_names_suggestion():
    raw = {"enabld": True}
    errors = validate_raw(_Dummy, raw)
    assert len(errors) == 1
    assert "enabld" in errors[0]
    assert "enabled" in errors[0]


def test_validate_raw_unknown_nested_key_names_suggestion():
    raw = {"sub": {"cnt": 5}}
    errors = validate_raw(_Dummy, raw)
    assert len(errors) == 1
    assert "sub.cnt" in errors[0]
    assert "sub.count" in errors[0]


def test_validate_raw_type_mismatch_names_expected_type():
    raw = {"enabled": "yes"}
    errors = validate_raw(_Dummy, raw)
    assert len(errors) == 1
    assert "enabled" in errors[0]
    assert "bool" in errors[0]


def test_validate_raw_int_accepted_for_float_field():
    """A bare YAML int (2) for a float field is not an error -- Python/YAML
    treat 2 as a valid float value."""
    raw = {"ratio": 2}
    assert validate_raw(_Dummy, raw) == []


def test_validate_raw_bool_rejected_for_int_field():
    """bool is a subclass of int in Python; must not silently pass as int."""
    raw = {"sub": {"count": True}}
    errors = validate_raw(_Dummy, raw)
    assert len(errors) == 1
    assert "sub.count" in errors[0]


def test_validate_raw_non_mapping_top_level():
    errors = validate_raw(_Dummy, ["not", "a", "mapping"])
    assert len(errors) == 1
    assert "mapping" in errors[0]


def test_validate_raw_group_value_must_be_mapping():
    raw = {"sub": "not-a-dict"}
    errors = validate_raw(_Dummy, raw)
    assert len(errors) == 1
    assert "sub" in errors[0]
