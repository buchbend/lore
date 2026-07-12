"""CLI contract — every verb is a self-contained typer subapp.

The shape (lifted in v0.13.0):

    lore_cli/<verb>_cmd.py
        - exposes a module-level ``app: typer.Typer``
        - registers its callback / command(s) inside the file
        - imports business logic from lower layers (lore_core /
          lore_curator / lore_mcp / lore_search) — never the reverse

    lore_cli/__main__.py
        - mounts every subapp via ``app.add_typer(<cmd>.app, name=…)``
        - never defines inline ``@app.command`` / ``@app.callback``
          handlers (the documented ``uninstall`` alias is the single
          grandfathered exception)

The static checks below fail fast if a refactor accidentally hides a
verb in the dispatcher or breaks the per-verb encapsulation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
CLI_DIR = LIB / "lore_cli"

# `<verb>_cmd.py` files — one per typer subapp. ``hooks.py`` is the
# grandfathered exception: it pairs the SessionStart-hook callbacks
# with a small ``hook_app`` for the `lore hook` verb. Anything new
# should follow the ``*_cmd.py`` + ``app`` convention.
APP_DEFINITION_RE = re.compile(r"^app\s*=\s*typer\.Typer\(", re.MULTILINE)

# In __main__.py these are forbidden (mounting only, no inline verbs).
INLINE_HANDLER_RE = re.compile(
    r"^@app\.(command|callback)\b", re.MULTILINE,
)

# Documented exception in __main__.py — the symmetric `lore uninstall`
# alias that wraps `install_cmd`. Listed by the function name we expect
# the decorator to sit on.
ALLOWED_INLINE_HANDLERS = {"cmd_uninstall_alias"}


def _cmd_files() -> list[Path]:
    return sorted(CLI_DIR.glob("*_cmd.py"))


@pytest.mark.parametrize("path", _cmd_files(), ids=lambda p: p.name)
def test_every_verb_module_defines_app(path: Path) -> None:
    text = path.read_text()
    assert APP_DEFINITION_RE.search(text), (
        f"{path.relative_to(REPO_ROOT)} must define a module-level "
        "`app = typer.Typer(...)` (CLI contract — see "
        "docs/architecture/cli-contract.md)."
    )


def test_main_dispatcher_only_mounts() -> None:
    """__main__.py must be a pure mounter — no inline command handlers."""
    text = (CLI_DIR / "__main__.py").read_text()
    offenders: list[str] = []
    for match in INLINE_HANDLER_RE.finditer(text):
        # Find the function name on the next non-decorator/non-blank line.
        tail = text[match.end():]
        next_def = re.search(r"^\s*(?:async\s+)?def\s+(\w+)", tail, re.MULTILINE)
        fn_name = next_def.group(1) if next_def else "<unknown>"
        if fn_name not in ALLOWED_INLINE_HANDLERS:
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{line_no}: {match.group(0)} on `{fn_name}`")

    assert not offenders, (
        "lore_cli/__main__.py must only mount subapps via add_typer(). "
        "Inline handlers belong in lore_cli/<verb>_cmd.py. "
        "Offending sites:\n  " + "\n  ".join(offenders)
    )


def test_drain_cmd_and_deprecated_verbs_removed() -> None:
    """`lore drain` was removed outright — vestigial once drain-event
    emission moved onto the spine, where the janitor already
    retention-manages it. The four aliased verbs (`log`/`news`/`runs`/
    `proc`) have since completed their deprecation window and were
    removed too, in favor of `lore trace` / `lore status`. Guards
    against any of the five modules or mounts creeping back."""
    assert not (CLI_DIR / "drain_cmd.py").exists()
    text = (CLI_DIR / "__main__.py").read_text()
    assert "drain_cmd" not in text
    assert 'name="drain"' not in text
    for verb in ("log", "news", "runs", "proc"):
        assert not (CLI_DIR / f"{verb}_cmd.py").exists(), f"{verb}_cmd.py should be deleted"
        assert f'name="{verb}"' not in text, f"lore {verb} should no longer be mounted"
