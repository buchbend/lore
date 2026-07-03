"""Kill switch: `lore curator run --defrag` no longer exists.

The weekly whole-wiki defragmentation pass (Curator C) is retired and
its code deleted. The CLI no longer accepts `--defrag` at all — passing
it is a typer parse error, not a code path that silently no-ops.
"""

from __future__ import annotations

from lore_cli.curator_cmd import app
from typer.testing import CliRunner

runner = CliRunner()


def _seed_vault(tmp_path):
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "testwiki" / "sessions").mkdir(parents=True)
    return lore_root


def test_defrag_flag_is_rejected(tmp_path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(app, ["run", "--defrag"], catch_exceptions=False)

    assert result.exit_code != 0
    assert "defrag" in result.output.lower()


def test_defrag_flag_rejected_even_with_dry_run(tmp_path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(app, ["run", "--defrag", "--dry-run"], catch_exceptions=False)
    assert result.exit_code != 0
