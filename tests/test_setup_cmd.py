"""`lore setup` (#470): one command for first run and repair (PRD 0016 § Setup)."""

from __future__ import annotations

import ast
import builtins
import json
import re
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest
import typer
import yaml
from lore_cli import setup_cmd
from lore_cli.__main__ import app
from lore_core.config import user_config_path
from typer.testing import CliRunner

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
PIP = version("lore")


def _person() -> dict:
    p = user_config_path()
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def _plugin_index(ver: str | None) -> None:
    idx = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    idx.parent.mkdir(parents=True, exist_ok=True)
    plugins = {"lore@lore": [{"scope": "user", "version": ver}]} if ver else {}
    idx.write_text(json.dumps({"plugins": plugins}))


@pytest.fixture
def agents(monkeypatch):
    """Claude present, Cursor absent; record integration runs and cache refreshes."""
    calls: list[str] = []
    monkeypatch.setattr(setup_cmd, "_present", lambda name: name == "claude")
    monkeypatch.setattr(
        setup_cmd, "_install_integration", lambda name: calls.append(f"install {name}") or 0
    )
    monkeypatch.setattr(setup_cmd, "_refresh_plugin_cache", lambda: calls.append("refresh") or True)
    monkeypatch.setattr(setup_cmd.checks, "_CHECKS", [("stub", lambda cwd: (True, "ok"), True)])
    monkeypatch.delenv("LORE_ROOT", raising=False)
    return calls


@pytest.fixture
def no_prompt(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("prompted without a terminal")

    monkeypatch.setattr(builtins, "input", _boom)
    monkeypatch.setattr(typer, "prompt", _boom)
    monkeypatch.setattr(typer, "confirm", _boom)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _setup(*args: str):
    return runner.invoke(app, ["setup", *args])


def _snapshot(*roots: Path) -> dict:
    return {p: p.stat().st_mtime_ns for r in roots for p in r.rglob("*") if ".git" not in p.parts}


def test_empty_machine_creates_wiki_and_installs_plugins(tmp_path, agents, no_prompt, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vault = tmp_path / "lore"
    result = _setup("--yes", "--vault", str(vault))
    assert result.exit_code == 0, result.output
    assert (vault / "wiki" / "personal").is_dir()
    assert _person()["lore_root"] == str(vault)
    assert "install claude" in agents
    assert not (vault / "sessions").exists() and not (vault / "drafts").exists()
    assert not (vault / "wiki" / "personal" / "sessions").exists()


def test_repo_without_scope_gets_parent_folder_scope_then_checks(
    tmp_path, agents, no_prompt, monkeypatch
):
    ran: list[str] = []
    monkeypatch.setattr(
        setup_cmd.checks, "_CHECKS", [("stub", lambda cwd: ran.append(cwd) or (True, "ok"), True)]
    )
    repo = _repo(tmp_path / "git" / "proj")
    monkeypatch.chdir(repo)
    result = _setup("--yes", "--vault", str(tmp_path / "lore"))
    assert result.exit_code == 0, result.output
    scope = _person()["scopes"]["git"]
    assert scope == {"path": str(tmp_path / "git"), "wiki": "personal"}
    assert ran, "the check step must run"


def test_second_run_writes_nothing_and_prints_one_mark_per_step(
    tmp_path, agents, no_prompt, monkeypatch
):
    repo = _repo(tmp_path / "git" / "proj")
    monkeypatch.chdir(repo)
    _setup("--yes", "--vault", str(tmp_path / "lore"))
    _plugin_index(PIP)
    before = _snapshot(Path.home(), tmp_path / "lore")
    result = _setup("--yes")
    assert result.exit_code == 0, result.output
    assert _snapshot(Path.home(), tmp_path / "lore") == before
    marks = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(marks) == 4 and all(ln.lstrip().startswith("✓") for ln in marks), result.output


def test_migrates_attachments_and_scopes_json(tmp_path, agents, no_prompt, monkeypatch):
    vault = tmp_path / "lore"
    (vault / "wiki" / "team").mkdir(parents=True)
    old = vault / ".lore"
    old.mkdir()
    a, b, c = tmp_path / "git" / "a", tmp_path / "other" / "a", tmp_path / "git" / "repo-c"
    (old / "attachments.json").write_text(
        json.dumps(
            {
                "attachments": [
                    {"path": str(a), "wiki": "team", "scope": "ccat:a", "attached_at": "x"},
                    {"path": str(b), "wiki": "team", "scope": "ccat:b", "attached_at": "x"},
                    {"path": str(c), "wiki": "team", "scope": "ccat:c", "attached_at": "x"},
                ],
                "declined": [],
            }
        )
    )
    (old / "scopes.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(vault))
    assert result.exit_code == 0, result.output
    scopes = _person()["scopes"]
    # Named after the old scope's leaf: the project slug orientation and lint match on.
    assert {n: s["path"] for n, s in scopes.items()} == {"a": str(a), "b": str(b), "c": str(c)}
    assert all(s["wiki"] == "team" for s in scopes.values())
    assert not (old / "attachments.json").exists() and not (old / "scopes.json").exists()


def test_moves_old_root_config_into_person_config(tmp_path, agents, no_prompt, monkeypatch):
    vault = tmp_path / "lore"
    (vault / "wiki" / "team").mkdir(parents=True)
    (vault / ".lore").mkdir()
    (vault / ".lore" / "config.yml").write_text(
        "user:\n  display_name: Old Name\nfeedback:\n  retrieval_misses: true\n"
        "tiers:\n  overrides:\n    claude:\n      frontier: x\n"
        "workflow:\n  risk:\n    max_files: 7\n"
    )
    user_config_path().parent.mkdir(parents=True)
    user_config_path().write_text("user:\n  display_name: New Name\n")
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(vault))
    assert result.exit_code == 0, result.output
    person = _person()
    assert person["user"]["display_name"] == "New Name"  # the person file wins
    assert person["feedback"]["retrieval_misses"] is True
    assert person["tiers"]["overrides"]["claude"]["frontier"] == "x"
    assert person["workflow"]["risk"]["max_files"] == 7
    assert not (vault / ".lore" / "config.yml").exists()


def test_repairs_stale_plugin_cache_and_names_it(tmp_path, agents, no_prompt, monkeypatch):
    (tmp_path / "lore" / "wiki" / "w").mkdir(parents=True)
    _plugin_index("0.0.1")
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(tmp_path / "lore"))
    assert agents == ["refresh"]
    assert f"0.0.1 → {PIP}" in result.output


def test_repairs_cursor_hook_entry_and_names_it(tmp_path, agents, no_prompt, monkeypatch):
    (tmp_path / "lore" / "wiki" / "w").mkdir(parents=True)
    _plugin_index(PIP)
    (Path.home() / ".cursor").mkdir()
    monkeypatch.setattr(setup_cmd, "_present", lambda name: True)
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(tmp_path / "lore"))
    assert "install cursor" in agents
    assert "hooks.json" in result.output


def test_setup_wiki_runs_only_the_wiki_step(tmp_path, agents, no_prompt, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _setup("wiki", "--yes", "--vault", str(tmp_path / "lore"), "--wiki-new", "team")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "lore" / "wiki" / "team").is_dir()
    assert agents == []


def test_setup_scope_takes_flags(tmp_path, agents, no_prompt, monkeypatch):
    (tmp_path / "lore" / "wiki" / "team").mkdir(parents=True)
    user_config_path().parent.mkdir(parents=True)
    user_config_path().write_text(f"lore_root: {tmp_path / 'lore'}\n")
    repo = _repo(tmp_path / "git" / "ccatobs" / "proj")
    monkeypatch.chdir(repo)
    result = _setup(
        "scope", "--yes", "--name", "ccat", "--wiki", "team", "--remote", "github.com/ccatobs/*"
    )
    assert result.exit_code == 0, result.output
    assert _person()["scopes"]["ccat"] == {
        "path": str(tmp_path / "git" / "ccatobs"),
        "wiki": "team",
        "remote": ["github.com/ccatobs/*"],
    }
    assert agents == []


def test_banner_of_unrouted_repo_names_lore_setup_scope(tmp_path, monkeypatch):
    from lore_core.session_start import session_start_text

    (tmp_path / "lore" / "wiki" / "w").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "lore"))
    repo = _repo(tmp_path / "git" / "proj")
    assert "lore setup scope" in session_start_text(str(repo))


def test_no_session_md_template_shipped():
    assert not (REPO / "lib" / "lore_core" / "templates" / "session.md").exists()


def test_cli_has_no_retired_onboarding_commands():
    root = typer.main.get_command(app)
    for name in ("install", "init", "attach", "scopes", "doctor"):
        assert name not in root.commands
    assert {"wiki", "scope"} <= set(root.commands["setup"].commands)


# ---------------------------------------------------------------------------
# No user-facing string names a retired concept.
# ---------------------------------------------------------------------------

_RETIRED = re.compile(r"session notes?|composed notes?|## Lore\b", re.IGNORECASE)
# Matches old note bodies byte for byte so readers can strip it; never printed.
_ALLOWED = {("lore_core/note_document.py", "DISCLAIMER")}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_no_retired_phrase_in_shipped_string_literals():
    offenders = []
    for path in sorted((REPO / "lib").rglob("*.py")):
        rel = path.relative_to(REPO / "lib").as_posix()
        tree = ast.parse(path.read_text())
        docs = _docstring_nodes(tree)
        allowed_names = {n for f, n in _ALLOWED if f == rel}
        skip = {
            id(c)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in allowed_names for t in node.targets)
            for c in ast.walk(node.value)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and id(node) not in skip
                and _RETIRED.search(node.value)
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, offenders


def test_no_retired_phrase_in_cli_help():
    import click

    def walk(cmd: click.Command, path: str):
        yield path, cmd.help or ""
        for p in cmd.params:
            yield f"{path} {p.name}", getattr(p, "help", "") or ""
        for name, sub in getattr(cmd, "commands", {}).items():
            yield from walk(sub, f"{path} {name}")

    hits = [p for p, text in walk(typer.main.get_command(app), "lore") if _RETIRED.search(text)]
    assert not hits, hits


def test_migrate_skips_bad_rows_and_reports_bad_files(tmp_path, agents, no_prompt, monkeypatch):
    vault = tmp_path / "lore"
    (vault / "wiki" / "team").mkdir(parents=True)
    (vault / ".lore").mkdir()
    row = {"path": str(tmp_path / "git" / "a"), "wiki": "team", "scope": "a"}
    (vault / ".lore" / "attachments.json").write_text(json.dumps({"attachments": ["x", row]}))
    (vault / ".lore" / "config.yml").write_text("- a list\n")
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(vault))
    assert set(_person()["scopes"]) == {"a"}
    assert "must hold a mapping, left in place" in result.output
    assert (vault / ".lore" / "config.yml").exists()
    # A file left in place must not make every run rewrite the person file.
    before = user_config_path().stat().st_mtime_ns
    _setup("--yes")
    assert user_config_path().stat().st_mtime_ns == before


def test_migrate_reports_non_list_attachments(tmp_path, agents, no_prompt, monkeypatch):
    vault = tmp_path / "lore"
    (vault / "wiki" / "team").mkdir(parents=True)
    (vault / ".lore").mkdir()
    (vault / ".lore" / "attachments.json").write_text(json.dumps({"attachments": {"a": 1}}))
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(vault))
    assert "left in place" in result.output
    assert (vault / ".lore" / "attachments.json").exists()


def test_report_pads_the_migrate_label(tmp_path, agents, no_prompt, monkeypatch):
    vault = tmp_path / "lore"
    (vault / "wiki" / "team").mkdir(parents=True)
    (vault / ".lore").mkdir()
    (vault / ".lore" / "scopes.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    result = _setup("--yes", "--vault", str(vault))
    assert "✓ migrate " in result.output


def test_scope_refuses_a_folder_that_holds_home(tmp_path, agents, no_prompt, monkeypatch):
    (tmp_path / "lore" / "wiki" / "team").mkdir(parents=True)
    repo = _repo(Path.home() / "proj")
    monkeypatch.chdir(repo)
    result = _setup("--yes", "--vault", str(tmp_path / "lore"))
    assert "scopes" not in _person()
    assert "too broad" in result.output


def test_scope_refuses_an_unknown_wiki(tmp_path, agents, no_prompt, monkeypatch):
    (tmp_path / "lore" / "wiki" / "team").mkdir(parents=True)
    user_config_path().parent.mkdir(parents=True)
    user_config_path().write_text(f"lore_root: {tmp_path / 'lore'}\n")
    monkeypatch.chdir(_repo(tmp_path / "git" / "proj"))
    result = _setup("scope", "--yes", "--wiki", "nope")
    assert result.exit_code == 1
    assert "known: team" in result.output
    assert "scopes" not in _person()


def test_check_names_failed_advisory_checks(tmp_path, agents, no_prompt, monkeypatch):
    monkeypatch.setattr(
        setup_cmd.checks,
        "_CHECKS",
        [("good", lambda cwd: (True, "ok"), True), ("vale", lambda cwd: (False, "absent"), False)],
    )
    summary, events = setup_cmd.step_check(tmp_path)
    assert summary != "2 checks passed"
    assert "1 of 2" in summary and "vale" in summary


def test_refresh_survives_a_hung_claude_and_refreshes_lore_workflow(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if "marketplace" in argv:
            raise subprocess.TimeoutExpired(argv, 60)
        return subprocess.CompletedProcess(argv, 0, "", "")

    _plugin_index(PIP)
    idx = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    data = json.loads(idx.read_text())
    data["plugins"]["lore-workflow@lore"] = [{"scope": "user", "version": "1.0.0"}]
    idx.write_text(json.dumps(data))
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: "/fake/claude")
    monkeypatch.setattr(setup_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(setup_cmd.integrations, "_refresh_claude_plugin_cache", lambda quiet: True)
    assert setup_cmd._refresh_plugin_cache() is True
    assert ["/fake/claude", "plugin", "update", "lore-workflow@lore"] in calls


def test_no_retired_phrase_in_skill_text():
    hits = [
        f"{p.relative_to(REPO)}:{n}"
        for root in ("skills", "lore-workflow/skills")
        for p in sorted((REPO / root).rglob("*.md"))
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if _RETIRED.search(line)
    ]
    assert not hits, hits


def test_mcp_wiki_not_found_names_lore_setup_scope():
    text = (REPO / "lib" / "lore_mcp" / "server.py").read_text()
    assert "attach the cwd" not in text
    assert "lore setup scope" in text
