"""Tests for per-wiki config loader."""

import warnings
from pathlib import Path

import pytest

from lore_core.wiki_config import WikiConfig, load_wiki_config


class TestWikiConfigDefaults:
    def test_load_defaults_on_missing_file(self, tmp_path: Path):
        """No file → returns full default WikiConfig()."""
        cfg = load_wiki_config(tmp_path)
        assert isinstance(cfg, WikiConfig)
        assert cfg.git.auto_commit is True
        assert cfg.git.auto_push is False
        assert cfg.git.auto_pull is True
        assert cfg.curator.threshold_pending == 10
        assert cfg.curator.threshold_tokens == 50_000
        assert cfg.curator.a_noteworthy_tier == "middle"
        assert cfg.curator.curator_c.enabled is False
        assert cfg.curator.curator_c.mode == "local"
        assert cfg.models.simple == "claude-haiku-4-5"
        assert cfg.models.middle == "claude-sonnet-4-6"
        assert cfg.models.high == "claude-opus-4-7"
        assert cfg.briefing.auto is True
        assert cfg.briefing.audience == "personal"
        assert cfg.briefing.sinks == []
        assert cfg.breadcrumb.mode == "normal"
        assert cfg.breadcrumb.scope_filter is True


class TestWikiConfigPartialMerge:
    def test_load_partial_yaml_merges_with_defaults(self, tmp_path: Path):
        """YAML with only git.auto_push=true → other git defaults preserved."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("git:\n  auto_push: true\n")
        cfg = load_wiki_config(tmp_path)
        assert cfg.git.auto_push is True
        assert cfg.git.auto_commit is True  # default preserved
        assert cfg.git.auto_pull is True
        # All other sections fully default
        assert cfg.curator.threshold_pending == 10
        assert cfg.models.simple == "claude-haiku-4-5"


class TestWikiConfigNestedDataclasses:
    def test_load_curator_c_enabled_parsed(self, tmp_path: Path):
        """YAML with curator.curator_c.enabled=true → parsed correctly."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("curator:\n  curator_c:\n    enabled: true\n")
        cfg = load_wiki_config(tmp_path)
        assert cfg.curator.curator_c.enabled is True
        assert cfg.curator.curator_c.mode == "local"  # default

    def test_load_models_high_off_parsed(self, tmp_path: Path):
        """YAML with models.high="off" → parsed correctly."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("models:\n  high: \"off\"\n")
        cfg = load_wiki_config(tmp_path)
        assert cfg.models.high == "off"
        assert cfg.models.simple == "claude-haiku-4-5"  # defaults preserved
        assert cfg.models.middle == "claude-sonnet-4-6"

    def test_load_briefing_sinks_parsed(self, tmp_path: Path):
        """YAML with briefing.sinks list → parsed correctly."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text(
            "briefing:\n"
            "  sinks:\n"
            "    - matrix:#dev-notes\n"
            "    - markdown:~/foo.md\n"
        )
        cfg = load_wiki_config(tmp_path)
        assert cfg.briefing.sinks == ["matrix:#dev-notes", "markdown:~/foo.md"]
        assert cfg.briefing.auto is True  # default preserved

    def test_load_breadcrumb_mode_parsed(self, tmp_path: Path):
        """YAML with breadcrumb.mode="quiet" → parsed correctly."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("breadcrumb:\n  mode: quiet\n")
        cfg = load_wiki_config(tmp_path)
        assert cfg.breadcrumb.mode == "quiet"
        assert cfg.breadcrumb.scope_filter is True  # default preserved


class TestWikiConfigWarnings:
    def test_load_unknown_top_level_key_warns_and_continues(self, tmp_path: Path):
        """Unknown top-level key → warns, returns defaults, no crash."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("nonsense: 42\n")
        with pytest.warns(UserWarning, match="unknown key 'nonsense'"):
            cfg = load_wiki_config(tmp_path)
        assert cfg.git.auto_commit is True  # defaults returned

    def test_load_unknown_nested_key_warns_and_continues(self, tmp_path: Path):
        """Unknown nested key → warns, other git defaults preserved."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("git:\n  fake_flag: true\n")
        with pytest.warns(UserWarning, match="unknown key 'fake_flag'"):
            cfg = load_wiki_config(tmp_path)
        assert cfg.git.auto_commit is True  # other defaults preserved
        assert cfg.git.auto_push is False


class TestWikiConfigErrorHandling:
    def test_load_malformed_yaml_warns_and_returns_defaults(self, tmp_path: Path):
        """Malformed YAML → warns, returns WikiConfig()."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text(":\n  invalid\n")
        with pytest.warns(UserWarning, match="malformed YAML"):
            cfg = load_wiki_config(tmp_path)
        assert cfg == WikiConfig()

    def test_load_non_mapping_yaml_warns_and_returns_defaults(self, tmp_path: Path):
        """Top-level list instead of mapping → warns, returns defaults."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("- just\n- a\n- list\n")
        with pytest.warns(UserWarning, match="top-level must be a mapping"):
            cfg = load_wiki_config(tmp_path)
        assert cfg == WikiConfig()
