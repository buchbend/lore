"""Tests for `lore init` — vault scaffold + the unified onboarding wizard.

The wizard is the single guided path from "binary installed" to "notes
being written": vault -> wiki -> integrations -> optional attach ->
automatic doctor -> handoff. It is idempotent and resumable, so
re-running repairs a partial install.
"""

from __future__ import annotations

import os
import subprocess

import pytest
import typer
from lore_cli import init_cmd
from lore_cli.init_cmd import app, init_vault, run_wizard
from lore_core.root_config import load_root_config
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _restore_lore_root_env():
    """The wizard commits its chosen vault to ``os.environ['LORE_ROOT']``
    for the running process; snapshot + restore so it never leaks across
    tests."""
    saved = os.environ.get("LORE_ROOT")
    yield
    if saved is None:
        os.environ.pop("LORE_ROOT", None)
    else:
        os.environ["LORE_ROOT"] = saved


@pytest.fixture()
def no_integrations(monkeypatch):
    """Neutralise the integration-wiring step so tests never touch a real
    Claude Code / Cursor install (the dev host has ``claude`` on PATH)."""
    monkeypatch.setattr(init_cmd, "_step_integrations", lambda **kw: None)


# --------------------------------------------------------------------------
# init_vault unit — display-name onboarding (unchanged behaviour)
# --------------------------------------------------------------------------


def test_init_vault_noninteractive_leaves_display_name_unset(tmp_path):
    init_vault(tmp_path)
    assert load_root_config(tmp_path).user.display_name == ""


def test_init_vault_display_name_arg_persists(tmp_path):
    init_vault(tmp_path, display_name="Christof")
    assert load_root_config(tmp_path).user.display_name == "Christof"


def test_init_vault_already_configured_not_reprompted(tmp_path, monkeypatch):
    from lore_core.root_config import set_field

    set_field(tmp_path, "user.display_name", "Christof")
    monkeypatch.setattr(init_cmd, "_is_interactive", lambda: True)
    init_vault(tmp_path, force=True)
    assert load_root_config(tmp_path).user.display_name == "Christof"


# --------------------------------------------------------------------------
# Vault resolution / persistence
# --------------------------------------------------------------------------


def test_prompt_vault_flag_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("LORE_ROOT", raising=False)
    got = init_cmd._prompt_vault(str(tmp_path / "v"), interactive=False)
    assert got == (tmp_path / "v").resolve()


def test_prompt_vault_honors_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "envvault"))
    got = init_cmd._prompt_vault(None, interactive=False)
    assert got == (tmp_path / "envvault").resolve()


# --------------------------------------------------------------------------
# Wizard end-to-end (--yes, CI-runnable)
# --------------------------------------------------------------------------


def test_wizard_yes_creates_vault_wiki_scopes(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    rc = run_wizard(vault=str(vault), yes=True, cwd=tmp_path)

    assert (vault / "CLAUDE.md").exists()
    assert (vault / "templates").is_dir()
    wikis = [d for d in (vault / "wiki").iterdir() if d.is_dir()]
    assert wikis, "wizard must scaffold a wiki by default"
    assert (wikis[0] / "_scopes.yml").exists()

    # Exit code mirrors a direct doctor run on the same state.
    from lore_cli import doctor_cmd

    expected = doctor_cmd.main(["--cwd", str(tmp_path)])
    assert rc == expected


def test_wizard_yes_display_name(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    run_wizard(vault=str(vault), yes=True, display_name="Christof", cwd=tmp_path)
    assert load_root_config(vault).user.display_name == "Christof"


# --------------------------------------------------------------------------
# Exit code reflects the doctor result
# --------------------------------------------------------------------------


@pytest.mark.parametrize("doctor_rc", [0, 1])
def test_wizard_exit_reflects_doctor(tmp_path, monkeypatch, no_integrations, doctor_rc):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: doctor_rc)
    rc = run_wizard(vault=str(vault), yes=True, cwd=tmp_path)
    assert rc == doctor_rc


# --------------------------------------------------------------------------
# Idempotent + resumable
# --------------------------------------------------------------------------


def test_wizard_idempotent_second_run(tmp_path, monkeypatch, no_integrations, capsys):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)

    run_wizard(vault=str(vault), yes=True, cwd=tmp_path)
    wikis1 = sorted(p.name for p in (vault / "wiki").iterdir() if p.is_dir())
    scopes = next((vault / "wiki").glob("*/_scopes.yml"))
    bytes1 = scopes.read_bytes()
    capsys.readouterr()

    run_wizard(vault=str(vault), yes=True, cwd=tmp_path)
    out = capsys.readouterr().out

    wikis2 = sorted(p.name for p in (vault / "wiki").iterdir() if p.is_dir())
    assert wikis1 == wikis2, "second run must not create a duplicate wiki"
    assert scopes.read_bytes() == bytes1, "second run must not churn _scopes.yml"
    assert out.count("skipped") >= 2, "vault + wiki collapse to skip lines"


def test_wizard_repairs_one_deleted_artifact(tmp_path, monkeypatch, no_integrations, capsys):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)

    run_wizard(vault=str(vault), yes=True, cwd=tmp_path)
    (vault / "CLAUDE.md").unlink()  # break exactly one vault artifact
    capsys.readouterr()

    run_wizard(vault=str(vault), yes=True, cwd=tmp_path)
    out = capsys.readouterr().out

    assert (vault / "CLAUDE.md").exists(), "vault step must repair the deletion"
    assert "skipped" in out, "the wiki step still collapses (only vault repaired)"


# --------------------------------------------------------------------------
# Flag parity — wiki targets
# --------------------------------------------------------------------------


def test_wizard_wiki_new_names_wiki(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    run_wizard(vault=str(vault), wiki_new="research", yes=True, cwd=tmp_path)
    assert (vault / "wiki" / "research" / "_scopes.yml").exists()


def test_wizard_wiki_link_symlinks_dir(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    src = tmp_path / "external-wiki"
    src.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    run_wizard(vault=str(vault), wiki_link=str(src), yes=True, cwd=tmp_path)
    linked = vault / "wiki" / "external-wiki"
    assert linked.is_symlink() and linked.resolve() == src.resolve()


def test_wizard_wiki_clone_uses_git(tmp_path, monkeypatch, no_integrations):
    srcrepo = tmp_path / "srcwiki"
    srcrepo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=srcrepo, check=True)
    (srcrepo / "README.md").write_text("team wiki\n")
    subprocess.run(["git", "add", "-A"], cwd=srcrepo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=srcrepo,
        check=True,
    )
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    run_wizard(vault=str(vault), wiki_clone=str(srcrepo), yes=True, cwd=tmp_path)
    assert (vault / "wiki" / "srcwiki" / "README.md").exists()


def test_wizard_wiki_flags_mutually_exclusive(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    with pytest.raises(typer.BadParameter):
        run_wizard(vault=str(vault), wiki_new="a", wiki_link=str(tmp_path), yes=True, cwd=tmp_path)


# --------------------------------------------------------------------------
# Integration step reuses the `lore install` plumbing
# --------------------------------------------------------------------------


def test_wizard_integration_step_reuses_install(tmp_path, monkeypatch):
    from lore_cli import install_cmd

    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    monkeypatch.setattr(install_cmd, "_integration_present", lambda name: name == "claude")
    spy = []
    monkeypatch.setattr(install_cmd, "main", lambda argv=None: spy.append(argv) or 0)
    run_wizard(vault=str(vault), yes=True, cwd=tmp_path)
    assert spy, "integration step must call `lore install`"
    assert "--yes" in spy[0]


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


def test_init_cli_yes_runs_wizard(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    result = runner.invoke(app, ["--vault", str(vault), "--yes"])
    assert result.exit_code == 0, result.output
    assert (vault / "wiki").is_dir()


def test_init_cli_root_alias_still_accepted(tmp_path, monkeypatch, no_integrations):
    vault = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(vault))
    monkeypatch.setattr(init_cmd, "_run_doctor", lambda cwd: 0)
    result = runner.invoke(app, ["--root", str(vault), "--yes"])
    assert result.exit_code == 0, result.output
