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
