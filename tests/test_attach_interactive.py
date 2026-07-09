"""Tests for the interactive `lore attach` wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.offer import FILENAME
from lore_core.state.attachments import AttachmentsFile
from lore_core.state.scopes import ScopesFile

runner = CliRunner()


@pytest.fixture
def lore_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lore_root = tmp_path / "lore-root"
    lore_root.mkdir()
    (lore_root / ".lore").mkdir()
    (lore_root / "wiki").mkdir()
    for w in ("private", "ccat", "science"):
        (lore_root / "wiki" / w).mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setattr("lore_core.config.get_lore_root", lambda: lore_root)
    monkeypatch.setattr("lore_core.config.get_wiki_root", lambda: lore_root / "wiki")
    monkeypatch.setattr("lore_cli.attach_cmd._is_interactive", lambda: True)
    return lore_root


def _write_offer(dir_: Path, *, wiki: str = "ccat", scope: str = "ccat:backend",
                 backend: str = "github") -> None:
    (dir_ / FILENAME).write_text(
        f"wiki: {wiki}\nscope: {scope}\nbackend: {backend}\n"
    )


# ---- Config detected flow ----

def test_use_as_is(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="u\n")
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "ccat"
    assert rows[0].scope == "ccat:backend"


def test_skip(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="s\n")
    assert result.exit_code == 0, result.output
    assert "Declined" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    assert len(af.all()) == 0


def test_customize(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="ccat", scope="ccat:backend", backend="github")

    # c=customize, Enter=keep default wiki (ccat), "ccat:frontend"=override scope,
    # Enter=keep backend, Enter=no .lore.yml, y=proceed
    input_lines = "c\n\nccat:frontend\n\n\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].scope == "ccat:frontend"


# ---- Manual flow (no .lore.yml) ----

def test_manual_pick_existing(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # wikis sorted: ccat=1, private=2, science=3
    # 1=ccat wiki, "ccat:newscope"=scope (bare input, no scopes in registry),
    # Enter=default backend, n=no .lore.yml, y=proceed
    input_lines = "1\nccat:newscope\n\nn\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "ccat"
    assert rows[0].scope == "ccat:newscope"


def test_manual_custom_wiki(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # c=custom wiki, "newwiki"=name, "newwiki:sub"=scope (bare input),
    # Enter=default backend, n=no .lore.yml, y=proceed
    input_lines = "c\nnewwiki\nnewwiki:sub\n\nn\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "newwiki"
    assert rows[0].scope == "newwiki:sub"


def test_manual_write_lore_yml(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # wikis sorted: ccat=1, private=2, science=3
    # 2=private wiki, "lore:test"=scope (bare input), Enter=default backend,
    # y=write .lore.yml, y=proceed
    input_lines = "2\nlore:test\n\ny\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert (repo / FILENAME).exists()


def test_abort(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # 2=private wiki, "test"=scope (bare input), Enter=backend, n=no yml, n=abort
    input_lines = "2\ntest\n\nn\nn\nn\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0
    assert "Aborted" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    assert len(af.all()) == 0


# ---- Already attached ----

def test_already_attached_decline_reattach(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    # First attach
    runner.invoke(app, ["attach", "--cwd", str(repo)], input="u\n")

    # Second attach — decline re-attach
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="n\n")
    assert result.exit_code == 0
    assert "Already attached" in result.output


def test_one_click_accept_uses_parent_suggestion(
    lore_env: Path, tmp_path: Path,
) -> None:
    """Ancestor attached → wizard offers a one-click [A]ccept that
    finalises with parent.wiki, parent.scope:dirname, backend=github,
    and writes .lore.yml. Single Enter should be enough."""
    parent = tmp_path / "ccat"
    parent.mkdir()
    runner.invoke(
        app,
        ["attach", "manual", "--wiki", "ccat", "--scope", "ccat", "--cwd", str(parent)],
    )

    repo = parent / "deep" / "myrepo"
    repo.mkdir(parents=True)

    # Single Enter on the [A]ccept prompt — that's the whole UX.
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="\n")
    assert result.exit_code == 0, result.output
    assert "Proposed config" in result.output
    assert "Wrote" in result.output
    assert (repo / ".lore.yml").exists()

    af = AttachmentsFile(lore_env)
    af.load()
    rows = [a for a in af.all() if a.path == repo.resolve()]
    assert len(rows) == 1
    assert rows[0].wiki == "ccat"
    assert rows[0].scope == "ccat:myrepo"


def test_one_click_no_drift_after_writing_offer(
    lore_env: Path, tmp_path: Path,
) -> None:
    """Regression: freshly-written .lore.yml must match attachment fp.

    Before the fix, the row was created with offer_fingerprint=None
    while the wizard wrote a new .lore.yml — classify_state then
    reported DRIFT on the very next SessionStart. The user can't fix
    that themselves because they just created the file. After the fix,
    the wizard stamps the row with the file's fingerprint."""
    from lore_core.consent import ConsentState, classify_state

    parent = tmp_path / "ccat"
    parent.mkdir()
    runner.invoke(
        app,
        ["attach", "manual", "--wiki", "ccat", "--scope", "ccat", "--cwd", str(parent)],
    )
    repo = parent / "myrepo"
    repo.mkdir()

    runner.invoke(app, ["attach", "--cwd", str(repo)], input="\n")

    af = AttachmentsFile(lore_env)
    af.load()
    state = classify_state(repo.resolve(), af).state
    assert state is ConsentState.ATTACHED, f"expected ATTACHED, got {state}"


def test_step_through_overrides_suggestion(
    lore_env: Path, tmp_path: Path,
) -> None:
    """`s` at the one-click prompt drops into per-field flow with the
    suggestion still surfaced as defaults; user can override."""
    parent = tmp_path / "ccat"
    parent.mkdir()
    runner.invoke(
        app,
        ["attach", "manual", "--wiki", "ccat", "--scope", "ccat", "--cwd", str(parent)],
    )

    repo = parent / "myrepo"
    repo.mkdir()

    # s = step through, then: Enter (wiki=ccat default), c=custom scope,
    # "ccat:custom"=custom value, Enter (backend=github default),
    # n (no .lore.yml), y (proceed).
    input_lines = "s\n\nc\nccat:custom\n\nn\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Proposed config" in result.output
    assert "Stepping through" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    rows = [a for a in af.all() if a.path == repo.resolve()]
    assert len(rows) == 1
    assert rows[0].scope == "ccat:custom"


def test_one_click_cancel_aborts(lore_env: Path, tmp_path: Path) -> None:
    """`c` at the one-click prompt aborts cleanly."""
    parent = tmp_path / "ccat"
    parent.mkdir()
    runner.invoke(
        app,
        ["attach", "manual", "--wiki", "ccat", "--scope", "ccat", "--cwd", str(parent)],
    )
    repo = parent / "myrepo"
    repo.mkdir()

    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="c\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output

    af = AttachmentsFile(lore_env)
    af.load()
    assert not any(a.path == repo.resolve() for a in af.all())


def test_no_suggestion_when_no_ancestor_attached(
    lore_env: Path, tmp_path: Path,
) -> None:
    """Wizard stays generic (no one-click, no suggestion line) when
    there's no ancestor attachment."""
    repo = tmp_path / "lonely"
    repo.mkdir()
    # 1=ccat wiki, custom scope, default backend (now github), no .lore.yml, proceed
    input_lines = "1\nccat:lonely\n\nn\nn\ny\n"
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input=input_lines)
    assert result.exit_code == 0, result.output
    assert "Suggested from parent attachment" not in result.output
    assert "Proposed config" not in result.output


def test_parent_attached_shows_info_but_continues(lore_env: Path, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    _write_offer(parent, wiki="ccat", scope="ccat")
    runner.invoke(app, ["attach", "--cwd", str(parent)], input="u\n")

    child = parent / "child"
    child.mkdir()
    _write_offer(child, wiki="ccat", scope="ccat:child")
    # Should show parent info but proceed to the config flow, then use as-is
    result = runner.invoke(app, ["attach", "--cwd", str(child)], input="u\n")
    assert result.exit_code == 0, result.output
    assert "parent attachment" in result.output.lower() or "Covered by" in result.output
    assert "Attached" in result.output


# ---- Inherited offers (issue #24) ----

def test_inherit_message_shown_from_descendant(
    lore_env: Path, tmp_path: Path,
) -> None:
    """Parent `.lore.yml` with `inherit: true`, cwd in a descendant —
    wizard surfaces 'Inherited from {path}' before the config flow."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / FILENAME).write_text(
        "wiki: ccat\nscope: ccat:backend\nbackend: github\ninherit: true\n"
    )
    child = parent / "child"
    child.mkdir()
    # u = use as-is (config-detected flow's "use this offer")
    result = runner.invoke(app, ["attach", "--cwd", str(child)], input="u\n")
    assert result.exit_code == 0, result.output
    assert "Inherited from" in result.output
    assert "Attached" in result.output


def test_no_inherit_message_when_offer_at_cwd(
    lore_env: Path, tmp_path: Path,
) -> None:
    """When the offer is at exact cwd, no 'Inherited from' line."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "--cwd", str(repo)], input="u\n")
    assert result.exit_code == 0, result.output
    assert "Inherited from" not in result.output


def test_accept_subcommand_diagnostic_for_non_inheriting_parent(
    lore_env: Path, tmp_path: Path,
) -> None:
    """`lore attach accept` from a child of a non-inheriting parent
    surfaces the migration hint, not just 'no .lore.yml'."""
    parent = tmp_path / "parent"
    parent.mkdir()
    _write_offer(parent)  # no inherit
    child = parent / "child"
    child.mkdir()
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(child)])
    assert result.exit_code == 1
    assert "inherit: true" in result.output
    assert "does not apply" in result.output


# ---- Subcommands still work ----

def test_subcommand_accept_still_works(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output


def test_subcommand_manual_still_works(lore_env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app, ["attach", "manual", "--wiki", "private", "--scope", "test", "--cwd", str(repo)]
    )
    assert result.exit_code == 0, result.output
    assert "Attached" in result.output
