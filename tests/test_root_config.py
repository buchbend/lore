import warnings
from pathlib import Path

import pytest

from lore_core.root_config import RootConfig, ObservabilityConfig, load_root_config


def test_defaults_when_file_absent(tmp_path: Path):
    cfg = load_root_config(tmp_path)
    assert cfg.observability.hook_events.max_size_mb == 10
    assert cfg.observability.hook_events.keep_rotations == 1
    assert cfg.observability.runs.keep == 200
    assert cfg.observability.runs.max_total_mb == 100
    assert cfg.observability.runs.keep_trace == 30


def test_partial_override(tmp_path: Path):
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text(
        "observability:\n"
        "  runs:\n"
        "    keep: 50\n"
    )
    cfg = load_root_config(tmp_path)
    assert cfg.observability.runs.keep == 50            # overridden
    assert cfg.observability.runs.max_total_mb == 100   # default preserved
    assert cfg.observability.hook_events.max_size_mb == 10  # default preserved


def test_malformed_yaml_warns(tmp_path: Path, recwarn):
    warnings.simplefilter("always")
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text("this: is: not: valid\n")
    cfg = load_root_config(tmp_path)
    assert cfg.observability.runs.keep == 200
    assert any("malformed YAML" in str(w.message) for w in recwarn)


def test_curator_noteworthy_mode_defaults_to_cascade(tmp_path: Path):
    """v0.6.0 promoted the feature-based cascade from opt-in to default.
    Absent explicit override, every wiki runs cascade mode."""
    cfg = load_root_config(tmp_path)
    assert cfg.curator.noteworthy_mode == "cascade"


def test_curator_noteworthy_mode_can_be_overridden_in_config(tmp_path: Path):
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text(
        "curator:\n"
        "  noteworthy_mode: llm_only\n"
    )
    cfg = load_root_config(tmp_path)
    assert cfg.curator.noteworthy_mode == "llm_only"


def test_use_buffer_flush_defaults_true(tmp_path: Path):
    """Buffer-and-flush is the default heartbeat path as of PR 3.

    Escape hatch lives in :func:`test_use_buffer_flush_can_be_disabled_in_config`.
    """
    cfg = load_root_config(tmp_path)
    assert cfg.curator.use_buffer_flush is True


def test_use_buffer_flush_can_be_disabled_in_config(tmp_path: Path):
    """Operators can opt out of the new path via ``.lore/config.yml``."""
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text(
        "curator:\n"
        "  use_buffer_flush: false\n"
    )
    cfg = load_root_config(tmp_path)
    assert cfg.curator.use_buffer_flush is False


def test_use_buffer_flush_can_be_enabled_in_config(tmp_path: Path):
    """Explicit ``true`` round-trips even though it now matches the default."""
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text(
        "curator:\n"
        "  use_buffer_flush: true\n"
    )
    cfg = load_root_config(tmp_path)
    assert cfg.curator.use_buffer_flush is True


def test_unknown_key_warns(tmp_path: Path, recwarn):
    warnings.simplefilter("always")
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text(
        "observability:\n"
        "  bogus_section: 42\n"
    )
    cfg = load_root_config(tmp_path)
    assert cfg.observability.runs.keep == 200  # still defaults
    assert any("bogus_section" in str(w.message) for w in recwarn), \
        "should warn about unknown key"


# ---------------------------------------------------------------------------
# Introspection / write-back helpers (lore config show / get / set / schema)
# ---------------------------------------------------------------------------


def _fresh_root(tmp_path: Path, body: str) -> Path:
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir(parents=True, exist_ok=True)
    (lore_dir / "config.yml").write_text(body)
    return tmp_path


def test_walk_fields_marks_file_vs_default(tmp_path: Path) -> None:
    from lore_core.root_config import walk_fields

    root = _fresh_root(tmp_path, "curator:\n  closure_judgment_enabled: false\n")
    fields_by_path = {fi.path: fi for fi in walk_fields(root)}

    assert fields_by_path["curator.closure_judgment_enabled"].source == "file"
    assert fields_by_path["curator.closure_judgment_enabled"].value is False
    # backend was not set in YAML — should be default "auto"
    assert fields_by_path["curator.backend"].source == "default"
    assert fields_by_path["curator.backend"].value == "auto"


def test_get_field_returns_leaf_info(tmp_path: Path) -> None:
    from lore_core.root_config import get_field

    root = _fresh_root(tmp_path, "")
    fi = get_field(root, "curator.closure_judgment_enabled")
    assert fi.value is True
    assert fi.default is True
    assert fi.source == "default"
    assert fi.type_name == "bool"


def test_get_field_unknown_path_raises(tmp_path: Path) -> None:
    from lore_core.root_config import get_field

    root = _fresh_root(tmp_path, "")
    with pytest.raises(KeyError, match="unknown config path"):
        get_field(root, "curator.no_such_field")


def test_get_field_on_group_raises(tmp_path: Path) -> None:
    from lore_core.root_config import get_field

    root = _fresh_root(tmp_path, "")
    with pytest.raises(KeyError, match="config group"):
        get_field(root, "curator")


def test_set_field_persists_and_round_trips(tmp_path: Path) -> None:
    from lore_core.root_config import get_field, set_field

    root = _fresh_root(tmp_path, "curator:\n  backend: openai\njournal:\n  enabled: false\n")
    fi = set_field(root, "curator.closure_judgment_enabled", "false")
    assert fi.value is False
    assert fi.source == "file"
    # Re-read; siblings preserved.
    assert get_field(root, "curator.closure_judgment_enabled").value is False
    assert get_field(root, "curator.backend").value == "openai"
    assert get_field(root, "journal.enabled").value is False


def test_set_field_creates_missing_parents(tmp_path: Path) -> None:
    from lore_core.root_config import get_field, set_field

    # Empty config file: setting a nested path should add the parent map.
    root = _fresh_root(tmp_path, "")
    set_field(root, "journal.enabled", "true")
    assert get_field(root, "journal.enabled").value is True


def test_set_field_rejects_bad_type(tmp_path: Path) -> None:
    from lore_core.root_config import set_field

    root = _fresh_root(tmp_path, "")
    with pytest.raises(ValueError, match="cannot parse"):
        set_field(root, "curator.closure_judgment_enabled", "notabool")


def test_set_field_rejects_unknown_path(tmp_path: Path) -> None:
    from lore_core.root_config import set_field

    root = _fresh_root(tmp_path, "")
    with pytest.raises(KeyError, match="unknown config path"):
        set_field(root, "curator.no_such_field", "true")


def test_set_field_bool_accepts_common_spellings(tmp_path: Path) -> None:
    from lore_core.root_config import get_field, set_field

    root = _fresh_root(tmp_path, "")
    for spelling in ("true", "TRUE", "yes", "on", "1"):
        set_field(root, "curator.closure_judgment_enabled", spelling)
        assert get_field(root, "curator.closure_judgment_enabled").value is True
    for spelling in ("false", "FALSE", "no", "off", "0"):
        set_field(root, "curator.closure_judgment_enabled", spelling)
        assert get_field(root, "curator.closure_judgment_enabled").value is False


def test_schema_tree_covers_all_leaves() -> None:
    from lore_core.root_config import schema_tree

    rows = schema_tree()
    paths = {p for p, _, _, _ in rows}
    # Spot-check leaves we ship today.
    assert "curator.backend" in paths
    assert "curator.closure_judgment_enabled" in paths
    assert "curator.use_buffer_flush" in paths
    assert "journal.enabled" in paths
    assert "observability.runs.keep" in paths
    # No group should appear (only leaves).
    assert "curator" not in paths
    assert "observability" not in paths
