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


# ---------------------------------------------------------------------------
# The person's config (PRD 0016) and the secrets rule (ADR 0016 invariant)
# ---------------------------------------------------------------------------

_PERSON = {
    "lore_root": "~/lore",
    "defaults": {"tracker": "auto"},
    "scopes": {
        "ccatobs": {
            "path": "~/git/ccatobs",
            "remote": ["github.com/ccatobs/*"],
            "wiki": "ccat",
            "logbook": ["file", "pm-ccat"],
            "sources": ["ccatwiki"],
        }
    },
    "sinks": {},
    "sources": {},
    "user": {"display_name": "Christof Buchbender"},
}


def test_person_config_from_prd_0016_validates():
    from lore_core.root_config import RootConfig

    assert validate_raw(RootConfig, _PERSON) == []


def test_unknown_scope_key_is_named():
    from lore_core.root_config import RootConfig

    raw = {"scopes": {"ccatobs": {"path": "~/git/ccatobs", "wikki": "ccat"}}}
    errors = validate_raw(RootConfig, raw)
    assert any("scopes.ccatobs.wikki" in e for e in errors), errors


def test_invariant_secrets_rule_token_or_password_key_fails_and_names_the_file(tmp_path):
    from lore_core.root_config import RootConfig

    cfg = tmp_path / "config.yml"
    for raw in (
        {"sinks": {"pm-ccat": {"kind": "command", "token": "abc"}}},
        {"sources": {"ccatwiki": {"password": "hunter2"}}},
    ):
        errors = validate_raw(RootConfig, raw, source=cfg)
        assert any(str(cfg) in e and ("token" in e or "password" in e) for e in errors), errors
