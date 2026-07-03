"""Kill switch: `lore curator run --defrag` no longer exists.

The weekly whole-wiki defragmentation pass (Curator C's LLM-adjacent-merge
/ auto-supersede / orphan-repair / draft-promotion) is retired. The CLI no
longer accepts `--defrag` at all — passing it is a typer parse error, not
a code path that silently no-ops.

The plain `lore curator [--wiki] [--apply]` hygiene pass (stale-flag /
supersession / backfill / implements-propagation, backing the
`/lore:curator` skill) is a separate, still-supported feature and is
untouched by this kill switch.
"""

from __future__ import annotations

from unittest.mock import patch

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


def test_run_without_defrag_flag_never_reaches_curator_c(tmp_path, monkeypatch) -> None:
    """`curator run` (Curator A's path) never calls Curator C's run_curator_c."""
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    calls = []
    with patch(
        "lore_curator.defrag_curator.run_curator_c",
        side_effect=lambda *a, **kw: calls.append(kw) or [],
    ):
        result = runner.invoke(app, ["run"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert calls == [], f"`curator run` must never invoke Curator C: {calls}"
