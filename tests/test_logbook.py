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


def _entry(did: str) -> str:
    """An entry with the four labelled parts (issue #500)."""
    return (
        "**Why:** Teams want a logbook without typing.\n\n"
        f"**What we did:** {did}\n\n"
        "**Consequences:** Each scope lists its sinks.\n\n"
        "**Not done yet:**\n- Nothing."
    )


GOOD = _entry("Built the logbook sinks for [#471](https://github.com/acme/widget/issues/471).")


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
        (GOOD + " word" * 300, "300 words"),
        (GOOD.replace("[#471](https://github.com/acme/widget/issues/471)", "#471"), "link"),
        (GOOD + "\ntodo: wire the hook", "todo:"),
        (GOOD + "\nDecided: keep it small", "decided:"),
        (GOOD.replace("**Why:**", ""), "Why:"),
        (GOOD.replace("**What we did:**", ""), "What we did:"),
        (GOOD.replace("**Consequences:**", ""), "Consequences:"),
        (GOOD.replace("**Not done yet:**", ""), "Not done yet:"),
    ],
)
def test_add_rejects_an_entry_that_breaks_a_rule(repo, tmp_path, capsys, text, rule) -> None:
    assert _add(tmp_path, text) == 2
    assert rule in capsys.readouterr().err
    assert not (repo / "docs" / "logbook").exists()


def test_add_takes_paragraphs_lists_and_plain_labels(repo, tmp_path) -> None:
    text = GOOD.replace("**Why:**", "Why:").replace(
        "- Nothing.", "- [#9](u) (a follow-up)\n- a check"
    )
    assert _add(tmp_path, text) == 0


def _entry_files(repo: Path) -> list[Path]:
    files = sorted((repo / "docs" / "logbook").glob("*/*.md"))
    for f in files:
        assert re.fullmatch(r"\d{4}-\d{2}", f.parent.name), f
    return files


def _month_file(repo: Path) -> Path:
    files = _entry_files(repo)
    assert len(files) == 1, files
    return files[0]


def test_file_sink_writes_one_file_per_entry(repo, tmp_path, capsys) -> None:
    """Two branches never touch one file, so no merge mixes two entries (review of PR #501)."""
    from lore_workflow.logbook import Entry, _file_sink

    for at, ref in (("2026-10-13T09:00:00Z", "#471"), ("2026-10-14T09:00:00Z", "#472")):
        text = GOOD.replace("#471", ref)
        _file_sink(Entry(f"lore:acme/widget:{ref}", at, "acme/widget", "", ref, "Ada", text), repo)

    assert len(_entry_files(repo)) == 2
    assert not (repo / ".gitattributes").exists()
    from lore_workflow.logbook import month_text

    shown = month_text(repo, "2026-10")
    assert shown.index("#472") < shown.index("#471")
    assert shown.count("**Not done yet:**") == 2


def test_file_sink_writes_nothing_for_a_repeated_key(repo, tmp_path) -> None:
    assert _add(tmp_path, GOOD) == 0
    before = _month_file(repo).read_text()
    assert _add(tmp_path, GOOD.replace("Built", "Rebuilt")) == 0
    assert _month_file(repo).read_text() == before
    assert "Ada Lovelace" in before


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


@pytest.mark.parametrize("inherited", [["hook"], "hook"])
def test_setup_sink_scope_keeps_the_inherited_default_list(repo, inherited) -> None:
    cfg = {"defaults": {"logbook": inherited}, "scopes": {"ccat": {"path": str(repo)}}}
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


def test_add_reads_an_entry_file_with_a_byte_order_mark(repo, tmp_path) -> None:
    entry = tmp_path / "bom.md"
    entry.write_text("\ufeff" + GOOD, encoding="utf-8")
    assert main(["log", "add", "--ref", "#1", "--text-file", str(entry)]) == 0


@pytest.mark.parametrize("adr", ["0014-one-home-per-fact.md", "0017-the-logbook.md"])
def test_the_adrs_record_the_new_logbook_question(adr) -> None:
    """Issue #500, criterion 8: ADR 0014 and ADR 0017 each carry the amendment."""
    text = (Path(__file__).resolve().parent.parent / "docs" / "adr" / adr).read_text()
    amendments = " ".join(text.split("## Amendments", 1)[1].split())
    assert "Why was this work done, and what does it mean for the team?" in amendments
    assert "summarise" in amendments


def test_show_prints_a_monthly_file_after_the_entry_files(repo, tmp_path, capsys) -> None:
    """A repo keeps the monthly file that the sink wrote before PR #501."""
    from lore_workflow.ledger import utc_date

    old = repo / "docs" / "logbook" / f"{utc_date()[:7]}.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Logbook\n\nAn older entry.\n")
    assert _add(tmp_path, GOOD) == 0
    capsys.readouterr()
    assert main(["log", "show"]) == 0
    out = capsys.readouterr().out
    assert out.index("Built the logbook sinks") < out.index("An older entry.")


# --- issue #490: small logbook defects ---------------------------------------


def test_a_retry_sends_the_text_with_its_repeated_spaces(repo, tmp_path) -> None:
    text = GOOD.replace("logbook sinks", "logbook  sinks")
    out = tmp_path / "stdin"
    _sinks(["pm"], pm={"kind": "command", "run": "exit 3"})
    assert _add(tmp_path, text) == 0
    _sinks(["pm"], pm={"kind": "command", "run": f"cat > {out}"})
    assert main(["log", "flush"]) == 0
    assert out.read_text() == text


def test_the_key_without_a_remote_is_the_same_in_a_linked_worktree(tmp_path, monkeypatch) -> None:
    from lore_workflow.logbook import add

    main_repo = tmp_path / "gadget"
    main_repo.mkdir()
    git = ["git", "-c", "user.name=Ada", "-c", "user.email=a@b"]
    subprocess.run(["git", "init", "-q"], cwd=main_repo, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], cwd=main_repo, check=True)
    linked = tmp_path / "gadget-wt"
    subprocess.run(["git", "worktree", "add", "-q", str(linked)], cwd=main_repo, check=True)
    _sinks([])

    keys = {add(GOOD, "#7", where)[0].key for where in (main_repo, linked)}
    assert keys == {"lore:gadget:#7"}


def test_the_file_sink_header_drops_an_empty_author(repo) -> None:
    from lore_workflow.logbook import Entry, _file_sink

    at = "2026-10-13T10:00:00Z"
    _file_sink(Entry("lore:acme/widget:#1", at, "acme/widget", "", "#1", "", GOOD), repo)
    assert "**2026-10-13** · #1\n" in _month_file(repo).read_text()


def test_two_concurrent_flushes_run_a_command_sink_once(repo, tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from lore_workflow.logbook import flush

    count = tmp_path / "count"
    _sinks(["pm"], pm={"kind": "command", "run": "exit 3"})
    assert _add(tmp_path, GOOD) == 0
    _sinks(["pm"], pm={"kind": "command", "run": f"echo x >> {count}; sleep 0.5"})
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: flush(repo), range(2)))
    assert count.read_text() == "x\n"


def test_precompact_note_skips_a_ledger_with_only_logbook_lines(repo, tmp_path) -> None:
    from lore_workflow.ledger import precompact_note

    _sinks(["pm"], pm={"kind": "command", "run": "exit 3"})
    assert _add(tmp_path, GOOD) == 0
    assert precompact_note(repo) is None


def test_log_add_help_shows_the_logbook_tag(repo, capsys) -> None:
    main(["log", "add", "--help"])
    assert "`[logbook]`" in capsys.readouterr().out


def test_a_ref_in_the_same_repo_keys_as_the_bare_ref(repo, tmp_path) -> None:
    out = tmp_path / "key"
    _sinks(["pm"], pm={"kind": "command", "run": f'printf %s "$LORE_ENTRY_KEY" > {out}'})
    assert _add(tmp_path, GOOD, ref="acme/widget#471") == 0
    assert out.read_text() == "lore:acme/widget:#471"


def test_a_line_separator_in_the_text_keeps_the_entry_pending(repo, tmp_path) -> None:
    """U+2028 in pasted text must not split the ledger line (review of PR #498)."""
    from lore_workflow.logbook import pending

    text = GOOD.replace(" for ", " for\u2028")
    out = tmp_path / "stdin"
    _sinks(["pm"], pm={"kind": "command", "run": "exit 3"})
    assert _add(tmp_path, text) == 0
    assert len(pending(repo / ".git" / "lore-ledger.md")) == 1
    _sinks(["pm"], pm={"kind": "command", "run": f"cat > {out}"})
    assert main(["log", "flush"]) == 0
    assert out.read_text() == text


def test_the_key_in_a_worktree_of_a_bare_repo_names_the_bare_repo(tmp_path) -> None:
    from lore_workflow.logbook import add

    src = tmp_path / "src"
    src.mkdir()
    git = ["git", "-c", "user.name=Ada", "-c", "user.email=a@b"]
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], cwd=src, check=True)
    bare = tmp_path / "gadget.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=bare, check=True)
    subprocess.run(["git", "worktree", "add", "-q", str(tmp_path / "w1")], cwd=bare, check=True)
    _sinks([])

    assert add(GOOD, "#7", tmp_path / "w1")[0].key == "lore:gadget:#7"


def test_the_main_checkout_name_reads_a_relative_common_dir(tmp_path, monkeypatch) -> None:
    """Git < 2.31 has no --path-format; `--git-common-dir` prints `.git` (review of PR #498)."""
    from lore_workflow import logbook

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=".git\n", stderr="")

    monkeypatch.setattr(logbook.subprocess, "run", fake_run)
    assert logbook._main_checkout_name(tmp_path / "gadget") == "gadget"


def test_flush_says_so_when_another_flush_holds_the_lock(repo, capsys) -> None:
    from lore_core.lockfile import flocked

    with flocked(repo / ".git" / "lore-ledger.md.lock"):
        assert main(["log", "flush"]) == 0
    assert "another flush" in capsys.readouterr().out
