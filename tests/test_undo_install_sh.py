"""Tests for `tools/undo_install_sh.py` — stdlib-only legacy cleaner.

Imports the module by file path (it's a script, not a package member).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Load the script as a module
SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "tools"
    / "undo_install_sh.py"
)


def _load_script(monkeypatch, fake_home: Path):
    """Re-import the script with HOME-derived constants pointed at fake_home."""
    spec = importlib.util.spec_from_file_location("undo_install_sh", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("undo_install_sh", None)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "HOME", fake_home)
    monkeypatch.setattr(module, "CLAUDE_DIR", fake_home / ".claude")
    monkeypatch.setattr(
        module, "SETTINGS_PATH", fake_home / ".claude" / "settings.json"
    )
    monkeypatch.setattr(
        module, "SKILLS_DIR", fake_home / ".claude" / "skills"
    )
    monkeypatch.setattr(
        module, "AGENTS_DIR", fake_home / ".claude" / "agents"
    )
    return module


@pytest.fixture
def fake_install_sh_state(tmp_path, monkeypatch):
    """Build a fake $HOME with install.sh-shaped state."""
    home = tmp_path / "fake-home"
    home.mkdir()
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True)
    repo = tmp_path / "lore"
    (repo / "skills" / "lore:foo").mkdir(parents=True)
    (repo / "agents").mkdir()
    (repo / "agents" / "lore-bar.md").write_text("# fake agent\n")
    # Symlinks
    (skills / "lore:foo").symlink_to(repo / "skills" / "lore:foo")
    (agents / "lore-bar.md").symlink_to(repo / "agents" / "lore-bar.md")
    # settings.json with the install.sh-era mutations
    settings = home / ".claude" / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "env": {"LORE_ROOT": str(tmp_path / "vault")},
                "permissions": {
                    "allow": [
                        "Bash(lore:*)",
                        "Bash(lore *)",
                        "Read(/home/u/.cache/lore/**)",
                        "Bash(other)",  # not Lore — must survive
                    ]
                },
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": "lore hook session-start"},
                                {"type": "command", "command": "other-hook"},
                            ]
                        }
                    ],
                    "PreCompact": [
                        {
                            "hooks": [
                                {"type": "command", "command": "lore hook pre-compact"}
                            ]
                        }
                    ],
                },
            }
        )
    )
    return home, repo


def test_dry_run_does_not_modify(monkeypatch, fake_install_sh_state, capsys):
    home, repo = fake_install_sh_state
    module = _load_script(monkeypatch, home)
    rc = module.main(["--dry-run"])
    assert rc == 0
    # Symlinks still present
    assert (home / ".claude" / "skills" / "lore:foo").is_symlink()
    assert (home / ".claude" / "agents" / "lore-bar.md").is_symlink()
    # settings.json untouched
    cfg = json.loads((home / ".claude" / "settings.json").read_text())
    assert "LORE_ROOT" in cfg["env"]
    assert "Bash(lore *)" in cfg["permissions"]["allow"]
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_full_run_removes_lore_state(monkeypatch, fake_install_sh_state):
    home, repo = fake_install_sh_state
    module = _load_script(monkeypatch, home)
    rc = module.main(["--yes"])
    assert rc == 0
    # Symlinks gone
    assert not (home / ".claude" / "skills" / "lore:foo").exists()
    assert not (home / ".claude" / "agents" / "lore-bar.md").exists()
    # settings.json: Lore entries gone, others survive
    cfg = json.loads((home / ".claude" / "settings.json").read_text())
    assert "env" not in cfg or "LORE_ROOT" not in cfg.get("env", {})
    assert "Bash(lore:*)" not in cfg["permissions"]["allow"]
    assert "Bash(lore *)" not in cfg["permissions"]["allow"]
    assert "Bash(other)" in cfg["permissions"]["allow"]
    # The "other-hook" SessionStart entry must survive
    sessstart = cfg["hooks"]["SessionStart"]
    surviving = [
        h["command"] for grp in sessstart for h in grp["hooks"]
    ]
    assert "other-hook" in surviving
    assert all("lore hook" not in c for c in surviving)
    # PreCompact had only Lore hooks → entire event removed
    assert "PreCompact" not in cfg.get("hooks", {})


def test_idempotent_second_run(monkeypatch, fake_install_sh_state, capsys):
    home, _ = fake_install_sh_state
    module = _load_script(monkeypatch, home)
    module.main(["--yes"])
    capsys.readouterr()
    # Second run finds nothing to remove and exits cleanly
    rc = module.main(["--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 skill symlink(s)" in out
    assert "0 hook entr" in out


def test_only_lore_repo_symlinks_removed(monkeypatch, tmp_path, capsys):
    """Symlinks pointing to non-lore targets must NOT be removed."""
    home = tmp_path / "fake-home"
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    # Lore symlink (target contains /lore/)
    repo = tmp_path / "lore"
    (repo / "skills" / "lore:keepme").mkdir(parents=True)
    (skills / "lore:keepme").symlink_to(repo / "skills" / "lore:keepme")
    # Non-lore symlink (target does NOT contain /lore/)
    other = tmp_path / "elsewhere" / "lore:other"
    other.mkdir(parents=True)
    (skills / "lore:other").symlink_to(other)

    module = _load_script(monkeypatch, home)
    module.main(["--yes"])
    # Lore-pointed link gone
    assert not (skills / "lore:keepme").exists()
    # Non-lore-pointed link preserved
    assert (skills / "lore:other").is_symlink()
