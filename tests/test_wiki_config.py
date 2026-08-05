"""Tests for per-wiki config loader."""

from pathlib import Path

import pytest
from lore_core.wiki_config import WikiConfig, load_wiki_config


class TestWikiConfigDefaults:
    def test_load_defaults_on_missing_file(self, tmp_path: Path):
        """No file → returns full default WikiConfig()."""
        cfg = load_wiki_config(tmp_path)
        assert isinstance(cfg, WikiConfig)
        assert cfg.git.auto_commit is False
        assert cfg.git.auto_push is False
        assert cfg.git.auto_pull is True
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
        assert cfg.git.auto_commit is False  # default preserved
        assert cfg.git.auto_pull is True
        # All other sections fully default
        assert cfg.models.simple == "claude-haiku-4-5"


class TestWikiConfigNestedDataclasses:
    def test_load_models_high_off_parsed(self, tmp_path: Path):
        """YAML with models.high="off" → parsed correctly."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text('models:\n  high: "off"\n')
        cfg = load_wiki_config(tmp_path)
        assert cfg.models.high == "off"
        assert cfg.models.simple == "claude-haiku-4-5"  # defaults preserved
        assert cfg.models.middle == "claude-sonnet-4-6"

    def test_load_briefing_sinks_parsed(self, tmp_path: Path):
        """YAML with briefing.sinks list → parsed correctly."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text(
            "briefing:\n  sinks:\n    - matrix:#dev-notes\n    - markdown:~/foo.md\n"
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
        assert cfg.git.auto_commit is False  # defaults returned

    def test_load_unknown_nested_key_warns_and_continues(self, tmp_path: Path):
        """Unknown nested key → warns, other git defaults preserved."""
        config_file = tmp_path / ".lore-wiki.yml"
        config_file.write_text("git:\n  fake_flag: true\n")
        with pytest.warns(UserWarning, match="unknown key 'fake_flag'"):
            cfg = load_wiki_config(tmp_path)
        assert cfg.git.auto_commit is False  # other defaults preserved
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


# ---------------------------------------------------------------------------
# Introspection / write-back helpers (lore config get/set/unset --wiki)
# ---------------------------------------------------------------------------


def _fresh_wiki(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".lore-wiki.yml").write_text(body)
    return tmp_path


class TestWikiConfigWriteBack:
    def test_walk_fields_marks_file_vs_default(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import walk_fields

        wiki = _fresh_wiki(tmp_path, "git:\n  auto_push: true\n")
        fields_by_path = {fi.path: fi for fi in walk_fields(wiki)}
        assert fields_by_path["git.auto_push"].source == "file"
        assert fields_by_path["git.auto_push"].value is True
        assert fields_by_path["git.auto_commit"].source == "default"
        assert fields_by_path["git.auto_commit"].value is False

    def test_get_field_returns_leaf_info(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import get_field

        wiki = _fresh_wiki(tmp_path, "")
        fi = get_field(wiki, "heartbeat.cooldown_s")
        assert fi.value == 120
        assert fi.source == "default"
        assert fi.type_name == "int"

    def test_get_field_unknown_path_raises_with_suggestion(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import get_field

        wiki = _fresh_wiki(tmp_path, "")
        with pytest.raises(KeyError, match="did you mean.*models.simple"):
            get_field(wiki, "models.simpel")

    def test_set_field_persists_and_round_trips(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import get_field, set_field

        wiki = _fresh_wiki(tmp_path, "briefing:\n  audience: team\n")
        fi = set_field(wiki, "git.auto_push", "true")
        assert fi.value is True
        assert get_field(wiki, "git.auto_push").value is True
        assert get_field(wiki, "briefing.audience").value == "team"  # untouched

    def test_set_field_rejects_bad_type_file_unchanged(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import set_field

        wiki = _fresh_wiki(tmp_path, "")
        cfg_path = wiki / ".lore-wiki.yml"
        before = cfg_path.read_text()
        with pytest.raises(ValueError, match="cannot parse"):
            set_field(wiki, "git.auto_push", "notabool")
        assert cfg_path.read_text() == before

    def test_set_field_rejects_unknown_path_file_unchanged(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import set_field

        wiki = _fresh_wiki(tmp_path, "git:\n  auto_push: true\n")
        cfg_path = wiki / ".lore-wiki.yml"
        before = cfg_path.read_text()
        with pytest.raises(KeyError, match="unknown config path"):
            set_field(wiki, "git.no_such_field", "true")
        assert cfg_path.read_text() == before

    def test_unset_field_reverts_to_default(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import get_field, set_field, unset_field

        wiki = _fresh_wiki(tmp_path, "")
        set_field(wiki, "heartbeat.cooldown_s", "5")
        assert get_field(wiki, "heartbeat.cooldown_s").value == 5
        fi = unset_field(wiki, "heartbeat.cooldown_s")
        assert fi.value == 120
        assert get_field(wiki, "heartbeat.cooldown_s").value == 120

    def test_unset_field_noop_when_not_set(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import unset_field

        wiki = _fresh_wiki(tmp_path, "")
        fi = unset_field(wiki, "heartbeat.cooldown_s")
        assert fi.value == 120

    def test_schema_tree_covers_all_leaves(self) -> None:
        from lore_core.wiki_config import schema_tree

        paths = {p for p, _, _, _ in schema_tree()}
        assert "git.auto_commit" in paths
        assert "heartbeat.cooldown_s" in paths
        assert "models.simple" in paths
        assert "briefing.audience" in paths
        assert "heartbeat.enabled" in paths
        assert "breadcrumb.mode" in paths
        assert "git" not in paths  # groups excluded, leaves only
