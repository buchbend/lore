"""`lore lab add|show`, the lab sinks and the lab group of the index (PRD 0020 § Lab
notebook, ADR 0019)."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from lore_cli.__main__ import main
from lore_core.config import user_config_path

GOOD = (
    "**Question:** Does the LO drift stay under 0.1 K per hour?\n\n"
    "**Setup:** Cold load for 6 h, 1 s sampling.\n\n"
    "**Result:** Drift 0.04 K per hour.\n\n"
    "**Interpretation:** The new bias board holds the spec.\n\n"
    "**Next:** Repeat with the warm load."
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "widget"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", "git@github.com:acme/widget.git")
    _git(repo, "config", "user.name", "Ada Lovelace")
    _git(repo, "config", "user.email", "ada@example.org")
    (repo / "run.py").write_text("print(1)\n")
    _git(repo, "add", "run.py")
    _git(repo, "commit", "-q", "-m", "first")
    monkeypatch.chdir(repo)
    return repo


def _add(tmp_path: Path, text: str = GOOD, *args: str) -> int:
    entry = tmp_path / "entry.md"
    entry.write_text(text)
    return main(["lab", "add", "--text-file", str(entry), *args])


def _entries(repo: Path) -> list[Path]:
    return sorted((repo / "docs" / "lab").glob("*/*.md"))


def _stamp(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---\n")[1])


@pytest.mark.parametrize("label", ["Question", "Setup", "Result", "Interpretation", "Next"])
def test_add_rejects_an_entry_without_a_label_and_names_it(repo, tmp_path, capsys, label) -> None:
    assert _add(tmp_path, GOOD.replace(f"**{label}:**", "")) == 2
    assert label in capsys.readouterr().err
    assert not (repo / "docs" / "lab").exists()


def test_add_rejects_an_entry_over_400_words(repo, tmp_path, capsys) -> None:
    assert _add(tmp_path, GOOD + " word" * 400) == 2
    assert "400 words" in capsys.readouterr().err


def test_the_stamp_holds_the_commit_of_the_repo(repo, tmp_path) -> None:
    assert _add(tmp_path) == 0
    (path,) = _entries(repo)
    stamp = _stamp(path)
    assert stamp["commit"] == _git(repo, "rev-parse", "HEAD")
    assert stamp["dirty"] is False
    assert stamp["branch"] == "main"
    assert stamp["author"] == "Ada Lovelace"
    assert stamp["repo"] == "acme/widget"
    assert stamp["host"] == socket.gethostname()
    assert stamp["status"] == "open"
    assert str(stamp["date"]).startswith(path.parent.name)
    assert GOOD in path.read_text()


def test_a_dirty_tree_sets_the_dirty_flag(repo, tmp_path) -> None:
    (repo / "run.py").write_text("print(2)\n")
    assert _add(tmp_path) == 0
    assert _stamp(_entries(repo)[0])["dirty"] is True


def test_cmd_data_thread_and_status_reach_the_stamp(repo, tmp_path) -> None:
    data = tmp_path / "scan.csv"
    data.write_text("1,2\n")
    missing = tmp_path / "gone.csv"
    args = ["--cmd", "python run.py --hours 6", "--data", str(data), "--data", str(missing)]
    args += ["--thread", "lo-drift", "--status", "dead-end"]
    assert _add(tmp_path, GOOD, *args) == 0
    stamp = _stamp(_entries(repo)[0])
    assert stamp["cmd"] == "python run.py --hours 6"
    assert stamp["data"] == [
        {"path": str(data), "sha256": hashlib.sha256(b"1,2\n").hexdigest()},
        {"path": str(missing)},
    ]
    assert stamp["thread"] == "lo-drift"
    assert stamp["status"] == "dead-end"


def test_add_rejects_an_unknown_status(repo, tmp_path) -> None:
    assert _add(tmp_path, GOOD, "--status", "maybe") != 0
    assert not (repo / "docs" / "lab").exists()


def test_the_file_sink_writes_one_file_per_entry(repo, tmp_path) -> None:
    assert _add(tmp_path) == 0
    assert _add(tmp_path, GOOD.replace("0.04", "0.05")) == 0
    files = _entries(repo)
    assert len(files) == 2
    assert all(f.parent.parent == repo / "docs" / "lab" for f in files)
    assert _add(tmp_path) == 0  # the same entry again is a no-op
    assert len(_entries(repo)) == 2


def _config(cfg: dict) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg))


def _ledger(repo: Path) -> str:
    return (repo / ".git" / "lore-ledger.md").read_text()


def test_a_scope_lab_sinks_list_replaces_the_file_sink(repo, tmp_path) -> None:
    out = tmp_path / "stdin"
    _config(
        {
            "sinks": {"pm": {"kind": "command", "run": f"cat > {out}"}},
            "scopes": {"acme": {"path": str(repo), "lab": {"sinks": ["pm"]}}},
        }
    )
    assert _add(tmp_path) == 0
    assert not (repo / "docs" / "lab").exists()
    assert GOOD in out.read_text()
    assert "commit:" in out.read_text()
    assert "- [lab] pm=delivered" in _ledger(repo)


def test_the_logbook_list_does_not_reach_lab_entries(repo, tmp_path) -> None:
    _config(
        {
            "sinks": {"pm": {"kind": "command", "run": "exit 3"}},
            "scopes": {"acme": {"path": str(repo), "logbook": ["pm"]}},
        }
    )
    assert _add(tmp_path) == 0
    assert "- [lab] file=delivered —" in _ledger(repo)


def test_an_undelivered_entry_waits_in_the_ledger_until_flush(repo, tmp_path) -> None:
    out = tmp_path / "stdin"
    scopes = {"acme": {"path": str(repo), "lab": {"sinks": ["file", "pm"]}}}
    _config({"sinks": {"pm": {"kind": "command", "run": "exit 3"}}, "scopes": scopes})
    assert _add(tmp_path) == 0
    assert "- [lab] file=delivered pm=undelivered" in _ledger(repo)

    _config({"sinks": {"pm": {"kind": "command", "run": f"cat > {out}"}}, "scopes": scopes})
    assert main(["log", "flush"]) == 0
    assert "- [lab] file=delivered pm=delivered" in _ledger(repo)
    assert GOOD in out.read_text()
    assert len(_entries(repo)) == 1


def _lab_line(repo: Path) -> None:
    entry = {
        "key": "lore:a/b:lab:1",
        "at": "2026-11-10T10:00:00Z",
        "repo": "a/b",
        "scope": "",
        "ref": "",
        "author": "A",
        "text": "x",
    }
    line = f"- [lab] pm=undelivered — {json.dumps(entry, separators=(',', ':'))}\n"
    (repo / ".git" / "lore-ledger.md").write_text(line)


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    from lore_cli import hooks

    (tmp_path / "vault" / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    calls: list[Path] = []
    monkeypatch.setattr(hooks, "_spawn_codemap_refresh", lambda cwd: None)
    monkeypatch.setattr(
        hooks, "_spawn_logbook_flush", lambda cwd: calls.append(Path(cwd).resolve())
    )
    return calls


def _hook(*args: str) -> str:
    from lore_cli.hooks import hook_app
    from typer.testing import CliRunner

    result = CliRunner().invoke(hook_app, list(args), input="")
    assert result.exit_code == 0, result.output
    return result.output


def test_session_start_retries_an_undelivered_lab_line(repo, spawned) -> None:
    _lab_line(repo)
    out = _hook("session-start", "--cwd", str(repo), "--plain")
    assert "1 lab entry undelivered" in out
    assert spawned == [repo.resolve()]


def test_session_end_retries_an_undelivered_lab_line(repo, spawned, monkeypatch) -> None:
    monkeypatch.delenv("LORE_SUPPRESS_CAPTURE", raising=False)
    _lab_line(repo)
    _hook("capture", "--event", "session-end", "--cwd", str(repo))
    assert spawned == [repo.resolve()]


def test_show_prints_entries_newest_first_and_filters_by_thread(repo, tmp_path, capsys) -> None:
    assert main(["lab", "show"]) == 0
    assert "no lab entries" in capsys.readouterr().out
    assert _add(tmp_path, GOOD.replace("0.04", "0.01"), "--thread", "lo-drift") == 0
    assert _add(tmp_path, GOOD.replace("0.04", "0.02"), "--thread", "bias") == 0
    assert _add(tmp_path, GOOD.replace("0.04", "0.03"), "--thread", "lo-drift") == 0
    capsys.readouterr()

    assert main(["lab", "show"]) == 0
    out = capsys.readouterr().out
    assert out.index("0.03") < out.index("0.02") < out.index("0.01")

    assert main(["lab", "show", "--thread", "lo-drift"]) == 0
    out = capsys.readouterr().out
    assert "0.03" in out and "0.01" in out and "0.02" not in out


def test_the_index_lists_the_three_newest_lab_entries(repo, tmp_path) -> None:
    from lore_cli.index_cmd import app
    from typer.testing import CliRunner

    for n in range(4):
        text = GOOD.replace("Does the LO drift", f"Q{n}: does the LO drift")
        assert _add(tmp_path, text) == 0
    out = CliRunner().invoke(app, ["--cwd", str(repo)]).output
    assert "### Lab entries" in out
    assert "Q0:" not in out
    assert out.index("Q1:") < out.index("Q2:") < out.index("Q3:")
    assert "Detail: `lore lab show`" in out


def test_session_start_shows_the_lab_group(repo, tmp_path, spawned) -> None:
    assert _add(tmp_path) == 0
    out = _hook("session-start", "--cwd", str(repo), "--plain", "--probe")
    assert "### Lab entries" in out
    assert "Does the LO drift stay under 0.1 K per hour?" in out


def test_a_lab_entry_keeps_the_agents_md_block_current(repo, tmp_path) -> None:
    from lore_core import repo_index

    assert repo_index.write_agents_block(repo)
    assert _add(tmp_path) == 0
    assert repo_index.agents_block_is_current(repo)
    assert "Lab entries" not in (repo / "AGENTS.md").read_text()
    assert main(["index", "--check"]) == 0


def test_filing_and_adr_0014_name_the_lab_entry_as_the_home_of_a_result() -> None:
    from lore_cli.style_cmd import app
    from typer.testing import CliRunner

    out = CliRunner().invoke(app, ["show", "filing"]).output
    assert "An experiment result becomes a lab entry" in out
    assert "lab entry with status `dead-end`" in out
    adr = next((Path(__file__).resolve().parent.parent / "docs" / "adr").glob("0014-*.md"))
    text = adr.read_text()
    assert "| What did we try, and what came out? | lab entry" in text
    assert "- 2026-11-10: new row" in text.split("## Amendments", 1)[1]


def test_the_experiment_skill_writes_the_question_first_and_a_dead_end_entry() -> None:
    root = Path(__file__).resolve().parent.parent
    text = " ".join((root / "lore-workflow/skills/experiment/SKILL.md").read_text().split())
    assert text.index("## 1. Before the run: write the question and the expected result") < (
        text.index("## 2. Run the experiment")
    )
    assert "lore lab add" in text
    assert "Commit that file on the current branch" in text
    assert "`--status dead-end` when the experiment failed to answer the question" in text
    assert (root / "docs/how-to/run-an-experiment.md").is_file()


# --- review round 1 of PR #562 ----------------------------------------------


def test_an_untracked_lab_entry_and_the_text_file_leave_the_tree_clean(repo, tmp_path) -> None:
    old = repo / "docs" / "lab" / "2026-10" / "old.md"
    old.parent.mkdir(parents=True)
    old.write_text("an earlier entry\n")
    entry = repo / "entry.md"
    entry.write_text(GOOD)
    assert main(["lab", "add", "--text-file", str(entry)]) == 0
    new = [p for p in _entries(repo) if p != old]
    assert _stamp(new[0])["dirty"] is False


def test_a_changed_committed_lab_entry_leaves_the_tree_clean(repo, tmp_path) -> None:
    old = repo / "docs" / "lab" / "2026-10" / "old.md"
    old.parent.mkdir(parents=True)
    old.write_text("an earlier entry\n")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "lab")
    old.write_text("an earlier entry, changed\n")
    assert _add(tmp_path) == 0
    assert _stamp(next(p for p in _entries(repo) if p != old))["dirty"] is False


def test_an_untracked_script_sets_the_dirty_flag(repo, tmp_path) -> None:
    (repo / "analyse.py").write_text("print(3)\n")
    assert _add(tmp_path) == 0
    assert _stamp(_entries(repo)[0])["dirty"] is True


def test_the_same_text_with_a_new_status_is_a_new_entry(repo, tmp_path) -> None:
    assert _add(tmp_path) == 0
    assert _add(tmp_path, GOOD, "--status", "concluded") == 0
    assert len(_entries(repo)) == 2


def test_the_same_add_twice_writes_one_file(repo, tmp_path) -> None:
    for _ in range(2):
        assert _add(tmp_path, GOOD, "--thread", "lo-drift") == 0
    assert len(_entries(repo)) == 1


def test_data_is_hashed_in_chunks(repo, tmp_path, monkeypatch) -> None:
    data = tmp_path / "big.bin"
    data.write_bytes(b"x" * 100)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read the whole file"))
    assert _add(tmp_path, GOOD, "--data", str(data)) == 0
    assert _stamp(_entries(repo)[0])["data"][0]["sha256"] == hashlib.sha256(b"x" * 100).hexdigest()


def test_a_non_utf8_file_under_docs_lab_does_not_block_delivery(repo, tmp_path) -> None:
    bad = repo / "docs" / "lab" / "2026-10" / "bad.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe broken")
    assert _add(tmp_path) == 0
    assert "- [lab] file=delivered" in _ledger(repo)


def test_lore_status_counts_an_undelivered_lab_line(repo, capsys) -> None:
    _lab_line(repo)
    main(["status"])
    assert "lab" in capsys.readouterr().out
