"""`lore log add|flush|show` and the sinks (PRD 0017 § Logbook entry, Sinks,
Pending entries; ADR 0017)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml
from lore_cli.__main__ import main
from lore_core.config import user_config_path
from lore_core.secrets import set_secret

GOOD = "Built the logbook sinks for [#471](https://github.com/acme/widget/issues/471)."


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "widget"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["remote", "add", "origin", "git@github.com:acme/widget.git"],
        ["config", "user.name", "Ada Lovelace"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    return repo


def _add(tmp_path: Path, text: str, ref: str = "#471") -> int:
    entry = tmp_path / "entry.md"
    entry.write_text(text)
    return main(["log", "add", "--ref", ref, "--text-file", str(entry)])


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (GOOD + "\n\nA second paragraph.", "one paragraph"),
        (GOOD + " word" * 80, "80 words"),
        ("Built the logbook sinks.", "link"),
        (GOOD + "\ntodo: wire the hook", "todo:"),
        (GOOD + "\nDecided: keep it small", "decided:"),
    ],
)
def test_add_rejects_an_entry_that_breaks_a_rule(repo, tmp_path, capsys, text, rule) -> None:
    assert _add(tmp_path, text) == 2
    assert rule in capsys.readouterr().err
    assert not (repo / "docs" / "logbook").exists()


def _month_file(repo: Path) -> Path:
    files = list((repo / "docs" / "logbook").glob("*.md"))
    assert len(files) == 1, files
    assert re.fullmatch(r"\d{4}-\d{2}\.md", files[0].name)
    return files[0]


def test_file_sink_puts_the_newest_entry_on_top(repo, tmp_path) -> None:
    assert _add(tmp_path, GOOD, ref="#471") == 0
    assert _add(tmp_path, "Filed [#472](https://github.com/acme/widget/issues/472).", "#472") == 0

    text = _month_file(repo).read_text()
    assert text.index("#472") < text.index("Built the logbook sinks")
    assert "Ada Lovelace" in text
    attrs = (repo / ".gitattributes").read_text()
    assert attrs.count("docs/logbook/*.md merge=union") == 1


def test_file_sink_writes_nothing_for_a_repeated_key(repo, tmp_path) -> None:
    assert _add(tmp_path, GOOD) == 0
    before = _month_file(repo).read_text()
    assert _add(tmp_path, GOOD.replace("Built", "Rebuilt")) == 0
    assert _month_file(repo).read_text() == before


def _sinks(logbook: list[str], **sinks: dict) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"sinks": sinks, "defaults": {"logbook": logbook}}))


def _ledger(repo: Path) -> str:
    return (repo / ".git" / "lore-ledger.md").read_text()


def test_command_sink_gets_the_text_on_stdin_and_the_entry_variables(repo, tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    _sinks(["stub"], stub={"kind": "command", "run": f"cat > {out}/stdin; env > {out}/env"})

    assert _add(tmp_path, GOOD) == 0

    assert (out / "stdin").read_text() == GOOD
    env = dict(ln.split("=", 1) for ln in (out / "env").read_text().splitlines() if "=" in ln)
    assert env["LORE_ENTRY_KEY"] == "lore:acme/widget:#471"
    assert env["LORE_ENTRY_REPO"] == "acme/widget"
    assert env["LORE_ENTRY_REF"] == "#471"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", env["LORE_ENTRY_AT"])
    assert "LORE_ENTRY_SCOPE" in env
    assert "LORE_SECRET" not in env
    assert "stub=delivered" in _ledger(repo)


def test_a_named_secret_reaches_the_sink_as_lore_secret_only(repo, tmp_path, capsys) -> None:
    out = tmp_path / "secret-seen"
    set_secret("hookshot", "s3cret-value")
    _sinks(
        ["matrix"],
        matrix={
            "kind": "command",
            "run": f'printf %s "$LORE_SECRET" > {out}',
            "secret": "hookshot",
        },
    )

    assert _add(tmp_path, GOOD) == 0

    assert out.read_text() == "s3cret-value"
    captured = capsys.readouterr()
    assert "s3cret-value" not in captured.out + captured.err
    assert "s3cret-value" not in _ledger(repo)
    assert "s3cret-value" not in user_config_path().read_text()


def test_one_failing_sink_stays_undelivered_until_flush_delivers_it(repo, tmp_path) -> None:
    _sinks(["file", "pm"], pm={"kind": "command", "run": "exit 3"})
    assert _add(tmp_path, GOOD) == 0
    assert "file=delivered pm=undelivered" in _ledger(repo)

    _sinks(["file", "pm"], pm={"kind": "command", "run": "cat > /dev/null"})
    assert main(["log", "flush"]) == 0
    assert "file=delivered pm=delivered" in _ledger(repo)


def _person() -> dict:
    return yaml.safe_load(user_config_path().read_text())


def test_setup_sink_command_adds_the_definition_and_tests_it(repo, capsys) -> None:
    set_secret("hookshot", "s3cret-value")
    args = ["setup", "sink", "command", "--name", "pm", "--run", "cat -", "--secret", "hookshot"]
    assert main(args) == 0
    assert _person()["sinks"]["pm"] == {"kind": "command", "run": "cat -", "secret": "hookshot"}
    assert "s3cret-value" not in user_config_path().read_text() + capsys.readouterr().out


def test_setup_sink_names_a_missing_command_and_a_missing_secret(repo, capsys) -> None:
    args = [
        "setup",
        "sink",
        "command",
        "--name",
        "pm",
        "--run",
        "no-such-pm log X",
        "--secret",
        "nope",
    ]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "no-such-pm" in out
    assert "nope" in out


def test_setup_sink_accepts_a_shell_line(repo, capsys) -> None:
    """The sink runs `run` through the shell; `(printf` is not a command name."""
    run = '(printf "%s\\n\\n" "$LORE_ENTRY_REF"; cat) | cat'
    assert main(["setup", "sink", "command", "--name", "pm", "--run", run]) == 0
    assert "not on PATH" not in capsys.readouterr().out
    assert _person()["sinks"]["pm"]["run"] == run


def test_setup_sink_names_a_shell_syntax_error(repo, capsys) -> None:
    assert main(["setup", "sink", "command", "--name", "pm", "--run", "(cat | cat"]) == 1
    assert "syntax" in capsys.readouterr().out


def test_setup_sink_file_adds_a_file_sink(repo) -> None:
    assert main(["setup", "sink", "file", "--name", "notes"]) == 0
    assert _person()["sinks"]["notes"] == {"kind": "file"}


def test_setup_sink_scope_adds_the_sink_to_the_scope_logbook(repo) -> None:
    """An absent list starts as `[file, <name>]`; a second sink is appended once."""
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text(yaml.safe_dump({"scopes": {"ccat": {"path": str(repo)}}}))
    assert (
        main(["setup", "sink", "command", "--name", "pm", "--run", "cat", "--scope", "ccat"]) == 0
    )
    assert _person()["scopes"]["ccat"]["logbook"] == ["file", "pm"]
    for _ in range(2):
        assert main(["setup", "sink", "file", "--name", "notes", "--scope", "ccat"]) == 0
    assert _person()["scopes"]["ccat"]["logbook"] == ["file", "pm", "notes"]


def test_setup_sink_scope_keeps_the_inherited_default_list(repo) -> None:
    cfg = {"defaults": {"logbook": ["hook"]}, "scopes": {"ccat": {"path": str(repo)}}}
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text(yaml.safe_dump(cfg))
    assert main(["setup", "sink", "file", "--name", "notes", "--scope", "ccat"]) == 0
    assert _person()["scopes"]["ccat"]["logbook"] == ["hook", "notes"]


def test_setup_sink_scope_names_the_known_scopes_for_an_unknown_scope(repo, capsys) -> None:
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text(yaml.safe_dump({"scopes": {"ccat": {"path": str(repo)}}}))
    assert main(["setup", "sink", "file", "--name", "notes", "--scope", "nope"]) == 1
    out = capsys.readouterr()
    assert "ccat" in out.out + out.err
    assert "notes" not in _person().get("sinks", {})


def test_show_prints_this_months_entries(repo, tmp_path, capsys) -> None:
    assert main(["log", "show"]) == 0
    assert "no logbook entries" in capsys.readouterr().out

    assert _add(tmp_path, GOOD) == 0
    capsys.readouterr()
    assert main(["log", "show"]) == 0
    assert GOOD in capsys.readouterr().out
