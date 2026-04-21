"""Task 4: `lore curator run --defrag [--dry-run]` CLI wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from lore_curator.curator_c import app


runner = CliRunner()


def _seed_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "testwiki" / "sessions").mkdir(parents=True)
    return lore_root


def test_defrag_flag_invokes_run_curator_c_with_defrag_true(tmp_path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    captured_kwargs = []

    def mock_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return []

    with patch("lore_curator.curator_c.run_curator_c", side_effect=mock_run):
        result = runner.invoke(app, ["run", "--defrag"], catch_exceptions=False)

    assert result.exit_code == 0
    assert captured_kwargs, "run_curator_c should have been called"
    assert captured_kwargs[0].get("defrag") is True


def test_defrag_dry_run_passes_through(tmp_path, monkeypatch) -> None:
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    captured_kwargs = []
    with patch(
        "lore_curator.curator_c.run_curator_c",
        side_effect=lambda *a, **kw: captured_kwargs.append(kw) or [],
    ):
        runner.invoke(app, ["run", "--defrag", "--dry-run"], catch_exceptions=False)
    assert captured_kwargs[0].get("dry_run") is True
    assert captured_kwargs[0].get("defrag") is True


def test_no_defrag_flag_uses_curator_a_path(tmp_path, monkeypatch) -> None:
    """Without --defrag, the command still runs Curator A (pre-Plan-5 behavior)."""
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    defrag_called = []

    def mock_c(*a, **kw):
        defrag_called.append(kw)
        return []

    with patch("lore_curator.curator_c.run_curator_c", side_effect=mock_c):
        # Without --defrag, run_curator_c should NOT be called via the --defrag
        # short-circuit. The command proceeds to Curator A.
        result = runner.invoke(app, ["run"], catch_exceptions=False)

    # run_curator_c may be called WITHOUT defrag=True later in the pipeline,
    # but the --defrag branch is the only place the short-circuit goes. Since
    # there's no --defrag flag, the CLI goes through Curator A's path.
    # Assert no --defrag-branch call.
    defrag_calls = [kw for kw in defrag_called if kw.get("defrag") is True]
    assert defrag_calls == [], "no --defrag → no defrag-branch call to run_curator_c"


def test_defrag_without_lore_root_exits_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    result = runner.invoke(app, ["run", "--defrag"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "LORE_ROOT" in result.output


def test_defrag_without_llm_client_warns_and_still_runs(tmp_path, monkeypatch) -> None:
    """Missing LLM creds → run proceeds with clear warning; LLM passes skip."""
    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Force make_llm_client to return None (no client available).
    with patch("lore_curator.llm_client.make_llm_client", return_value=None):
        result = runner.invoke(app, ["run", "--defrag"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    # Clear warning about skipped LLM passes.
    assert "LLM" in result.output or "llm" in result.output
