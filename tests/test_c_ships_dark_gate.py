"""Task 2: the "ships-dark" merge gate.

Proves that a fresh vault with NO explicit .lore-wiki.yml and NO
Curator C config gets ZERO new behavior post-Plan-5. Any failure
here blocks merge — Curator C MUST be invisible to existing users.

This is the single atomic test for success criterion #6
("zero impact on existing users"). Every other Plan 5 test verifies
behavior WHEN the flag is on; this one verifies the flag-off path is
truly inert.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from lore_cli.hooks import hook_app


runner = CliRunner()


LORE_BLOCK = """\
# Project

## Lore

- wiki: testwiki
- scope: testscope
- backend: none
"""


def _make_fresh_vault(tmp_path: Path) -> Path:
    """Vault with attached CLAUDE.md but NO .lore-wiki.yml — defaults apply."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(LORE_BLOCK)
    (project / "wiki" / "testwiki" / "sessions").mkdir(parents=True)
    (project / ".lore").mkdir(parents=True, exist_ok=True)
    # CRUCIAL: no .lore-wiki.yml is written — all config defaults.
    return project


def _snapshot_tree(path: Path, *, exclude_prefixes: tuple[str, ...] = ()) -> dict[str, bytes]:
    """Return {relative_path: sha256_bytes} for every file under path."""
    import hashlib

    out: dict[str, bytes] = {}
    if not path.exists():
        return out
    for p in sorted(path.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(path))
        if any(rel.startswith(pref) for pref in exclude_prefixes):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).digest()
    return out


# ---------------------------------------------------------------------------
# The five gate tests
# ---------------------------------------------------------------------------


def test_fresh_vault_no_llm_calls(tmp_path: Path, monkeypatch) -> None:
    """No .lore-wiki.yml → SessionStart triggers zero LLM instantiations."""
    project = _make_fresh_vault(tmp_path)

    def blow_up_on_llm(*_args, **_kwargs):
        raise AssertionError(
            "make_llm_client must NOT be called when defrag_curator.enabled is default-false"
        )

    monkeypatch.setattr("lore_curator.llm_client.make_llm_client", blow_up_on_llm)

    # SessionStart hook — default-off config must not reach any LLM path.
    result = runner.invoke(
        hook_app,
        ["session-start", "--cwd", str(project), "--plain"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output


def test_fresh_vault_no_spawns(tmp_path: Path) -> None:
    """SessionStart in a default-config vault spawns ZERO detached curators."""
    project = _make_fresh_vault(tmp_path)

    c_calls: list = []
    b_calls: list = []

    def mock_spawn_c(*a, **kw):
        c_calls.append(1)
        return True

    def mock_spawn_b(*a, **kw):
        b_calls.append(1)
        return True

    with patch("lore_cli.hooks._spawn_detached_curator_c", side_effect=mock_spawn_c), \
         patch("lore_cli.hooks._spawn_detached_curator_b", side_effect=mock_spawn_b):
        runner.invoke(
            hook_app,
            ["session-start", "--cwd", str(project), "--plain"],
            env={"LORE_ROOT": str(project)},
            catch_exceptions=False,
        )

    assert c_calls == [], f"default-off vault must not spawn Curator C; got {c_calls}"
    # Curator B is per-calendar-day. In a test run on an inactive vault with
    # last_curator_b=None it *may* fire. We don't assert on B here — that
    # scenario is a pre-Plan-5 behavior guaranteed by test_hooks_curator_b_trigger.


def test_fresh_vault_no_frontmatter_mutations(tmp_path: Path) -> None:
    """Byte-for-byte snapshot of wiki/ before and after a SessionStart loop."""
    project = _make_fresh_vault(tmp_path)

    # Seed one note to exercise the "would we mutate?" path.
    note = project / "wiki" / "testwiki" / "sessions" / "2026-04-21-test.md"
    note.write_text(
        "---\n"
        "type: session\n"
        "created: 2026-04-21\n"
        "last_reviewed: 2026-04-21\n"
        "status: active\n"
        "description: Test\n"
        "tags: []\n"
        "---\n\n"
        "Body content.\n"
    )

    before = _snapshot_tree(project / "wiki")

    # Run SessionStart + the bare curator commands that exist today.
    runner.invoke(
        hook_app,
        ["session-start", "--cwd", str(project), "--plain"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )

    after = _snapshot_tree(project / "wiki")
    assert before == after, (
        "default-config vault must not mutate wiki/ on SessionStart. Diff:\n"
        f"  added:   {set(after) - set(before)}\n"
        f"  removed: {set(before) - set(after)}\n"
        f"  changed: {[k for k in before if k in after and before[k] != after[k]]}"
    )


def test_fresh_vault_no_diff_log(tmp_path: Path) -> None:
    """No curator-c.diff.*.log is created in a default-config vault."""
    project = _make_fresh_vault(tmp_path)

    runner.invoke(
        hook_app,
        ["session-start", "--cwd", str(project), "--plain"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )

    diff_logs = list((project / ".lore").glob("curator-c.diff.*.log"))
    assert not diff_logs, f"default-config vault must not create diff logs; got {diff_logs}"


def test_fresh_vault_no_new_config_files(tmp_path: Path) -> None:
    """No .lore-wiki.yml materializes from a default-config SessionStart."""
    project = _make_fresh_vault(tmp_path)
    wiki_cfg = project / "wiki" / "testwiki" / ".lore-wiki.yml"
    assert not wiki_cfg.exists(), "precondition: no config file"

    runner.invoke(
        hook_app,
        ["session-start", "--cwd", str(project), "--plain"],
        env={"LORE_ROOT": str(project)},
        catch_exceptions=False,
    )

    assert not wiki_cfg.exists(), (
        "default-config vault must not auto-create .lore-wiki.yml — that would "
        "push opt-in config onto users without consent"
    )
