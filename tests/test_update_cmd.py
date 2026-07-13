"""Tests for `lore update` — remote version check in front of the
existing package+plugin self-upgrade roundtrip (`_run_self_upgrade`)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer
from lore_cli import install_cmd


def test_up_to_date_exits_zero_without_upgrading(capsys):
    with (
        patch.object(install_cmd, "_local_version", return_value="1.0.0"),
        patch.object(install_cmd, "_fetch_remote_version", return_value="1.0.0"),
        patch.object(install_cmd, "_run_self_upgrade") as mock_upgrade,
    ):
        install_cmd.update_command(check=False, quiet=False)
        mock_upgrade.assert_not_called()
    assert "up to date" in capsys.readouterr().out


def test_remote_newer_triggers_self_upgrade():
    with (
        patch.object(install_cmd, "_local_version", return_value="1.0.0"),
        patch.object(install_cmd, "_fetch_remote_version", return_value="1.1.0"),
        patch.object(install_cmd, "_run_self_upgrade", return_value=0) as mock_upgrade,
    ):
        with pytest.raises(typer.Exit) as exc:
            install_cmd.update_command(check=False, quiet=True)
        mock_upgrade.assert_called_once_with(quiet=True)
    assert exc.value.exit_code == 0


def test_check_flag_reports_only_never_upgrades(capsys):
    with (
        patch.object(install_cmd, "_local_version", return_value="1.0.0"),
        patch.object(install_cmd, "_fetch_remote_version", return_value="1.1.0"),
        patch.object(install_cmd, "_run_self_upgrade") as mock_upgrade,
    ):
        with pytest.raises(typer.Exit) as exc:
            install_cmd.update_command(check=True, quiet=False)
        mock_upgrade.assert_not_called()
    assert exc.value.exit_code == 1
    # Rich auto-highlights numeric substrings with ANSI codes, so match the
    # surrounding text rather than the raw version string.
    assert "Update available" in capsys.readouterr().out


def test_fetch_failure_exits_nonzero_with_helpful_message(capsys):
    with (
        patch.object(install_cmd, "_local_version", return_value="1.0.0"),
        patch.object(install_cmd, "_fetch_remote_version", return_value=None),
        patch.object(install_cmd, "_run_self_upgrade") as mock_upgrade,
    ):
        with pytest.raises(typer.Exit) as exc:
            install_cmd.update_command(check=False, quiet=False)
        mock_upgrade.assert_not_called()
    assert exc.value.exit_code == 1
    assert "lore install --upgrade" in capsys.readouterr().out


def test_version_tuple_compare_avoids_lexicographic_bug():
    """"0.9.0" < "0.10.0" as versions, even though it sorts the other way
    as plain strings."""
    assert install_cmd._remote_is_newer("0.10.0", "0.9.0") is True
    assert install_cmd._remote_is_newer("0.9.0", "0.10.0") is False
