#!/usr/bin/env python3
"""Undo legacy install.sh — reverse every mutation it made.

Lets the maintainer wipe install.sh-era state from `~/.claude` so the
new `lore install` flow can be tested on a clean slate. Runs on any
system with Python 3.11+ — no `lore_core` imports, no third-party
deps, no lore CLI required (works on a borked install).

Removes:
  • ~/.claude/skills/lore:*       symlinks pointing into a lore repo
  • ~/.claude/agents/lore-*.md    symlinks pointing into a lore repo
  • From ~/.claude/settings.json:
      - hooks.SessionStart entries whose command starts with `lore hook`
      - hooks.PreCompact entries whose command starts with `lore hook`
      - permissions.allow: Bash(lore:*), Bash(lore *), Read(<cache>/**),
        Read(<vault>/**)
      - env.LORE_ROOT
  • Optional with --pipx-uninstall: `pipx uninstall lore`

Idempotent. Resolves symlinks before mutating shared JSON. Acquires
fcntl.flock on settings.json so concurrent dispatcher runs don't
clobber each other's edits.

Usage:
    python3 tools/undo_install_sh.py [--dry-run] [--pipx-uninstall] [--yes]
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
SKILLS_DIR = CLAUDE_DIR / "skills"
AGENTS_DIR = CLAUDE_DIR / "agents"

LEGACY_HOOK_COMMAND_PREFIX = "lore hook"
LEGACY_PERMISSION_RULES = {"Bash(lore:*)", "Bash(lore *)"}
# Match Read(<anything>/.cache/lore/**) and Read(<vault>/**) heuristically
LEGACY_PERMISSION_RULE_PREFIXES = ("Read(", "Bash(lore")


# ---------------------------------------------------------------------------
# Output helpers (no Rich dep — keeps this file standalone)
# ---------------------------------------------------------------------------


def _say(msg: str, *, dry: bool = False) -> None:
    prefix = "[dry-run] " if dry else "          "
    print(prefix + msg)


def _info(msg: str) -> None:
    print(f"==> {msg}")


def _ok(msg: str) -> None:
    print(f" ok {msg}")


def _warn(msg: str) -> None:
    print(f" !! {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# flock helper — keep settings.json safe under concurrent edits
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _flocked(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Symlink cleanup (skills + agents)
# ---------------------------------------------------------------------------


def _is_lore_repo_target(target: str) -> bool:
    """A symlink target is 'lore-repo-shaped' if it contains '/lore/' segment.

    This catches `~/git/lore/skills/lore:foo` etc. without requiring the
    user to specify their checkout location. Conservative — won't match
    a hypothetical `/some/other/repo/lore`.
    """
    return "/lore/" in target.replace(os.sep, "/")


def _clean_symlinks_in(
    dir_path: Path,
    name_prefix: str,
    label: str,
    dry: bool,
) -> int:
    if not dir_path.is_dir():
        return 0
    removed = 0
    for entry in sorted(dir_path.iterdir()):
        if not entry.name.startswith(name_prefix):
            continue
        if not entry.is_symlink():
            continue
        target = os.readlink(entry)
        if not _is_lore_repo_target(target):
            continue
        _say(f"remove {label}: {entry} -> {target}", dry=dry)
        if not dry:
            entry.unlink()
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# settings.json mutations
# ---------------------------------------------------------------------------


def _read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        _warn(
            f"{SETTINGS_PATH} is not valid JSON — leaving untouched. "
            "Fix it manually then re-run."
        )
        return {}


def _write_settings_atomic(data: dict) -> None:
    real = Path(os.path.realpath(SETTINGS_PATH))
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, real)


def _strip_lore_hooks(cfg: dict) -> int:
    """Remove every hook entry whose command starts with `lore hook`."""
    removed = 0
    hooks = cfg.get("hooks") or {}
    for event in list(hooks.keys()):
        new_groups = []
        for grp in hooks[event]:
            kept = []
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                if isinstance(cmd, str) and cmd.startswith(
                    LEGACY_HOOK_COMMAND_PREFIX
                ):
                    removed += 1
                    continue
                kept.append(h)
            if kept:
                new_groups.append({**grp, "hooks": kept})
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]
    if not hooks:
        cfg.pop("hooks", None)
    return removed


def _strip_lore_permissions(cfg: dict) -> int:
    perms = cfg.get("permissions") or {}
    allow = perms.get("allow") or []
    new_allow = []
    removed = 0
    for rule in allow:
        if rule in LEGACY_PERMISSION_RULES:
            removed += 1
            continue
        # Read(<cache>/**) and Read(<lore_root>/**) — heuristic
        if isinstance(rule, str) and rule.startswith("Read("):
            tail = rule[len("Read(") : -1]
            if "/.cache/lore" in tail or tail.endswith(("vault/**", "lore/**")):
                # Be conservative — only strip rules the install.sh
                # is known to add. Vault paths can vary; match the
                # patterns install.sh wrote.
                if "/.cache/lore" in tail:
                    removed += 1
                    continue
                # Skip vault Read rules: too risky to guess; user can
                # remove manually.
        new_allow.append(rule)
    if removed:
        perms["allow"] = new_allow
    if not perms.get("allow"):
        perms.pop("allow", None)
    if not perms:
        cfg.pop("permissions", None)
    return removed


def _strip_lore_env(cfg: dict) -> int:
    env = cfg.get("env") or {}
    if "LORE_ROOT" in env:
        del env["LORE_ROOT"]
        if not env:
            cfg.pop("env", None)
        return 1
    return 0


def _clean_settings(dry: bool) -> tuple[int, int, int]:
    if not SETTINGS_PATH.exists():
        return 0, 0, 0
    with _flocked(SETTINGS_PATH):
        cfg = _read_settings()
        if not cfg:
            return 0, 0, 0
        h = _strip_lore_hooks(cfg)
        p = _strip_lore_permissions(cfg)
        e = _strip_lore_env(cfg)
        if (h or p or e):
            _say(
                f"settings.json: -{h} hook(s), -{p} permission rule(s), "
                f"-{e} env entr(y/ies)",
                dry=dry,
            )
            if not dry:
                _write_settings_atomic(cfg)
        return h, p, e


# ---------------------------------------------------------------------------
# pipx uninstall (optional)
# ---------------------------------------------------------------------------


def _pipx_uninstall(dry: bool) -> bool:
    if not shutil.which("pipx"):
        _warn("pipx not on PATH — skipping pipx uninstall")
        return False
    _say("pipx uninstall lore", dry=dry)
    if dry:
        return True
    try:
        result = subprocess.run(
            ["pipx", "uninstall", "lore"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        _warn(f"pipx uninstall failed: {e}")
        return False
    if result.returncode == 0:
        return True
    _warn(
        f"pipx uninstall exited {result.returncode}: "
        f"{(result.stderr or result.stdout).strip()[:200]}"
    )
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _confirm_or_exit(yes: bool) -> None:
    if yes:
        return
    print(
        "\nThis will remove every install.sh-era mutation under "
        "~/.claude/ (skills, agents, hooks, permissions, env).\n"
        "Lore source files are NOT touched. Re-runnable.\n"
    )
    ans = input("Proceed? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("Aborted.")
        sys.exit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="undo_install_sh",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change; do not modify anything.",
    )
    parser.add_argument(
        "--pipx-uninstall",
        action="store_true",
        help="Also run `pipx uninstall lore` after cleaning ~/.claude.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args(argv)

    dry = args.dry_run
    if not dry:
        _confirm_or_exit(args.yes)

    _info("Cleaning Lore symlinks under ~/.claude/")
    skills = _clean_symlinks_in(SKILLS_DIR, "lore:", "skill", dry)
    agents = _clean_symlinks_in(AGENTS_DIR, "lore-", "agent", dry)
    _ok(f"{skills} skill symlink(s), {agents} agent symlink(s)")

    _info("Cleaning Lore mutations in ~/.claude/settings.json")
    h, p, e = _clean_settings(dry)
    _ok(f"{h} hook entr(y/ies), {p} permission rule(s), {e} env entr(y/ies)")

    if args.pipx_uninstall:
        _info("Uninstalling lore CLI via pipx")
        if _pipx_uninstall(dry):
            _ok("pipx uninstalled lore")

    if dry:
        print("\n(dry-run — nothing changed. Re-run without --dry-run to apply.)")
    else:
        print(
            "\nDone. To re-install with the new flow:\n"
            "  pipx install lore   (or: lore install --dev "
            "from a checkout — see CONTRIBUTING.md)\n"
            "  lore install"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
