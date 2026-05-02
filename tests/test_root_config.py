import warnings
from pathlib import Path
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
