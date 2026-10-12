"""`lore search` and `lore setup source` over the source lists."""

from __future__ import annotations

import json

import pytest
import yaml
from lore_cli import search_cmd, setup_cmd
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))  # no gh


def test_search_json_has_wiki_and_one_list_per_source(tmp_path, capsys):
    cfg = tmp_path / "xdg" / "lore"
    cfg.mkdir(parents=True)
    (cfg / "config.yml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "mine": {"kind": "command", "run": 'echo \'[{"ref":"m:1","title":"T"}]\''}
                }
            }
        )
    )

    search_cmd.main(["--json", "q"])

    data = json.loads(capsys.readouterr().out)["data"]
    assert data["wiki"] == []
    by = {s["name"]: s for s in data["sources"]}
    assert by["mine"]["hits"][0]["ref"] == "m:1"
    assert by["github"]["hits"] == [] and by["github"]["note"]


def test_setup_source_dokuwiki_stores_token_and_runs_one_test_query(tmp_path, monkeypatch):
    queries = []

    def fake_search(query, k, cwd, repos, only=None):
        queries.append((query, only))
        return [{"name": only, "kind": "dokuwiki", "hits": [], "note": None}]

    monkeypatch.setattr("lore_core.sources.search_sources", fake_search)

    result = CliRunner().invoke(
        setup_cmd.app,
        [
            "source",
            "dokuwiki",
            "--name",
            "team",
            "--url",
            "https://w.example.org",
            "--token",
            "tok",
        ],
    )

    assert result.exit_code == 0, result.output
    assert yaml.safe_load((tmp_path / "xdg" / "lore" / "secrets.yml").read_text()) == {
        "team": "tok"
    }
    cfg = yaml.safe_load((tmp_path / "xdg" / "lore" / "config.yml").read_text())
    assert cfg["sources"]["team"] == {
        "kind": "dokuwiki",
        "url": "https://w.example.org",
        "secret": "team",
    }
    assert "tok" not in (tmp_path / "xdg" / "lore" / "config.yml").read_text()
    assert queries == [("test", "team")]
