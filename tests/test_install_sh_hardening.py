"""Structural guards for `install.sh` onboarding hardening (#194).

install.sh is bash — exercising a full pipx round-trip in pytest is not
worth it. These read the script and assert the two behaviours the epic
requires: it fails loudly when `lore` is not runnable on PATH afterwards,
and a first install chains into the unified `lore init` wizard.
"""

from __future__ import annotations

from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


def _path_check_block() -> str:
    text = INSTALL_SH.read_text()
    assert "command -v lore" in text, "install.sh must verify lore is on PATH"
    # The ~30 lines following the PATH probe cover the not-runnable branch.
    return text.split("command -v lore", 1)[1][:600]


def test_install_sh_fails_when_lore_not_on_path():
    block = _path_check_block()
    assert ("die " in block) or ("exit 1" in block), (
        "the not-on-PATH branch must exit non-zero, not `exit 0`"
    )


def test_install_sh_first_install_chains_into_lore_init():
    assert "exec lore init" in INSTALL_SH.read_text()


def test_install_sh_upgrade_refreshes_both_plugin_caches():
    text = INSTALL_SH.read_text()
    assert "claude plugin update lore@lore" in text
    assert "claude plugin update lore-workflow@lore" in text, (
        "the upgrade path must refresh lore-workflow@lore too — otherwise its "
        "skills cache silently stays on the old version (#311)"
    )


def test_install_sh_pipx_upgrade_does_not_force_over_the_existing_venv():
    """`pipx install --force` fails when pipx builds venvs with uv: uv refuses
    to create a venv over the existing one, the script stops, and neither the
    binary nor the plugins move. Uninstall then install, as `pipx reinstall`
    does, works with both venv backends."""
    text = INSTALL_SH.read_text()
    assert "pipx install --force" not in text
    upgrade = text.split("Upgrading lore via pipx", 1)[1].split(";;", 1)[0]
    assert "pipx uninstall lore" in upgrade
    assert 'pipx install "$LORE_FROM"' in upgrade


def test_install_sh_upgrade_refreshes_the_marketplace_before_the_plugins():
    text = INSTALL_SH.read_text()
    refresh = text.index("claude plugin marketplace update lore")
    assert refresh < text.index("claude plugin update lore@lore")


def test_install_sh_upgrade_says_how_to_load_the_new_plugin():
    text = INSTALL_SH.read_text()
    assert "claude --continue" in text
