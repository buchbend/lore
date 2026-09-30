"""Tests for per-wiki config loader."""

import subprocess
import warnings
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
        assert cfg.breadcrumb.mode == "normal"


class TestWikiConfigNestedDataclasses:
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
        fi = get_field(wiki, "breadcrumb.mode")
        assert fi.value == "normal"
        assert fi.source == "default"
        assert fi.type_name == "str"

    def test_get_field_unknown_path_raises_with_suggestion(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import get_field

        wiki = _fresh_wiki(tmp_path, "")
        with pytest.raises(KeyError, match="did you mean.*breadcrumb.mode"):
            get_field(wiki, "breadcrumb.mdoe")

    def test_set_field_persists_and_round_trips(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import get_field, set_field

        wiki = _fresh_wiki(tmp_path, "breadcrumb:\n  mode: quiet\n")
        fi = set_field(wiki, "git.auto_push", "true")
        assert fi.value is True
        assert get_field(wiki, "git.auto_push").value is True
        assert get_field(wiki, "breadcrumb.mode").value == "quiet"  # untouched

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
        set_field(wiki, "breadcrumb.mode", "quiet")
        assert get_field(wiki, "breadcrumb.mode").value == "quiet"
        fi = unset_field(wiki, "breadcrumb.mode")
        assert fi.value == "normal"
        assert get_field(wiki, "breadcrumb.mode").value == "normal"

    def test_unset_field_noop_when_not_set(self, tmp_path: Path) -> None:
        from lore_core.wiki_config import unset_field

        wiki = _fresh_wiki(tmp_path, "")
        fi = unset_field(wiki, "breadcrumb.mode")
        assert fi.value == "normal"

    def test_schema_tree_covers_all_leaves(self) -> None:
        from lore_core.wiki_config import schema_tree

        paths = {p for p, _, _, _ in schema_tree()}
        assert "git.auto_commit" in paths
        assert "breadcrumb.scope_filter" in paths
        assert "breadcrumb.mode" in paths
        assert "git" not in paths  # groups excluded, leaves only


class TestAutoPushDefault:
    """A wiki with a remote is shared, so lore pushes it unless told not to."""

    def _repo(self, tmp_path: Path, *, remote: bool) -> Path:
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main"], cwd=wiki, check=True, capture_output=True
        )
        if remote:
            subprocess.run(
                ["git", "remote", "add", "origin", str(tmp_path / "origin.git")],
                cwd=wiki,
                check=True,
                capture_output=True,
            )
        return wiki

    def test_auto_push_defaults_true_for_a_wiki_with_a_remote(self, tmp_path: Path):
        assert load_wiki_config(self._repo(tmp_path, remote=True)).git.auto_push is True

    def test_auto_push_defaults_false_for_a_wiki_without_a_remote(self, tmp_path: Path):
        assert load_wiki_config(self._repo(tmp_path, remote=False)).git.auto_push is False

    def test_an_explicit_false_wins_over_the_remote_default(self, tmp_path: Path):
        wiki = self._repo(tmp_path, remote=True)
        (wiki / ".lore-wiki.yml").write_text("git:\n  auto_push: false\n")
        assert load_wiki_config(wiki).git.auto_push is False


class TestRetiredBlocks:
    """A config file written before a retirement still loads.

    The loader warns once per retired block, by name, and applies the rest
    of the file. ``curator`` retired with the compose pipeline, ``briefing``
    with the briefing command, and ``models`` and ``heartbeat`` with the
    LLM client (issue 423).
    """

    RETIRED = {
        "curator": "curator:\n  threshold_pending_turns: 7\n  reaper_max_per_pass: 1\n",
        "briefing": "briefing:\n  auto: true\n  sinks:\n    - matrix\n",
        "models": "models:\n  simple: a\n  middle: b\n  high: 'off'\n",
        "heartbeat": "heartbeat:\n  enabled: false\n  cooldown_s: 5\n  push_context: false\n",
    }

    @pytest.mark.parametrize("block", sorted(RETIRED))
    def test_the_wiki_config_declares_no_retired_block(self, block: str) -> None:
        assert not hasattr(WikiConfig(), block)

    @pytest.mark.parametrize("block", sorted(RETIRED))
    def test_a_retired_block_warns_once_and_keeps_loading(self, tmp_path: Path, block: str):
        (tmp_path / ".lore-wiki.yml").write_text(
            self.RETIRED[block] + "git:\n  auto_push: true\n", encoding="utf-8"
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_wiki_config(tmp_path)

        messages = [str(w.message) for w in caught]
        named = [m for m in messages if f"'{block}' is retired and is ignored" in m]
        assert len(named) == 1, messages
        assert len(messages) == 1, messages
        assert cfg.git.auto_push is True
