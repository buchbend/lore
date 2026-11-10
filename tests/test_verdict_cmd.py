"""`lore verdict pending|set` and `lore inbox classify` carry the removed tools' fields."""

from __future__ import annotations

import json

from lore_cli.__main__ import _build_app
from typer.testing import CliRunner

runner = CliRunner()


def _app():
    return _build_app()


def test_verdict_pending_json_fields(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    r = runner.invoke(_app(), ["verdict", "pending", "--wiki", "demo"])
    out = json.loads(r.stdout)
    assert r.exit_code == 0, r.stdout
    assert out["schema"] == "lore.pending_verdicts/1"
    assert {"wiki", "pending", "count", "capped"} <= out.keys()


def test_verdict_set_rejects_bad_verdict_with_error_json():
    r = runner.invoke(_app(), ["verdict", "set", "demo", "n", "bogus"])
    assert r.exit_code == 1
    assert json.loads(r.stdout)["error"]["code"] == "invalid_verdict"


def test_inbox_classify_json_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    r = runner.invoke(_app(), ["inbox", "classify"])
    out = json.loads(r.stdout)
    assert out["schema"] == "lore.inbox.classify/1"
    assert "data" in out
