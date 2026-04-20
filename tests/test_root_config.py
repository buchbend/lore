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
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text("this: is: not: valid\n")
    cfg = load_root_config(tmp_path)
    assert cfg.observability.runs.keep == 200
    assert any("malformed YAML" in str(w.message) for w in recwarn)
