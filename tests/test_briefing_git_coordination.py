"""Tests for `lore briefing` git coordination — pull-before / push-after.

The flow: in a multi-user wiki repo, the briefing ledger
(`.briefing-ledger.json`) is the single source of truth for "which
sessions have already been published." Before the one-shot pipeline
runs, the wiki repo must be pulled so we see teammates' marks; after
mark, the ledger update must be committed and pushed so other teammates
see ours.

These tests use a bare-repo + two-clone fixture (mirroring
`test_git_sync.py`), one host playing "Alice" and the other "Bob",
to verify cross-host coordination end-to-end.

`gather()`'s walk over `<wiki>/sessions/` is retired (PRD 0013) — it always
reports zero new sessions now, which would make every test below hit the
"no new sessions" early return before touching git at all. `_stub_gather`
stands in for it: real ledger-filter behaviour (via the retained
`_read_ledger`) over a fixed candidate list, so the mark/commit/push path
this file actually tests still runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest
from lore_cli.briefing_cmd import _run_oneshot
from lore_core.briefing.gather import _read_ledger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _write_session(wiki_dir: Path, name: str, what: str) -> None:
    body = dedent(
        f"""\
        ---
        schema_version: 2
        type: session
        created: {name[:10]}
        last_reviewed: {name[:10]}
        description: "session {name}"
        ---

        ## What we worked on

        {what}
        """
    )
    sessions_dir = wiki_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{name}.md").write_text(body)


def _write_briefing_yaml(wiki_dir: Path, sink: str) -> None:
    (wiki_dir / ".lore-briefing.yml").write_text(f"sink: {sink}\n")


def _seed_wiki_files(wiki_dir: Path, sessions: list[tuple[str, str]]) -> None:
    """Write sessions and `.lore-briefing.yml` into ``wiki_dir``."""
    for name, what in sessions:
        _write_session(wiki_dir, name, what)
    _write_briefing_yaml(wiki_dir, "markdown")


def _init_bare(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--bare", "--initial-branch=main")


def _clone_wiki(origin: Path, dest: Path, name: str) -> None:
    """Clone origin into ``dest`` and configure git identity.

    ``dest`` is the wiki directory itself (i.e. ``<vault>/wiki/ccat``);
    its parent must already exist.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(dest.parent, "clone", str(origin), dest.name)
    _git(dest, "config", "user.email", f"{name}@example.com")
    _git(dest, "config", "user.name", name)


def _commit_all(wiki_dir: Path, message: str) -> None:
    _git(wiki_dir, "add", "-A")
    _git(wiki_dir, "commit", "-m", message)


@pytest.fixture
def two_hosts(tmp_path: Path):
    """Two clones of a bare origin, sharing initial wiki state.

    Layout (mirroring the symlink-mounted-wiki case in production —
    each wiki is its own git repo, with the vault root just hosting
    it as a subdirectory):

      <tmp>/origin.git                  bare repo
      <tmp>/alice/wiki/ccat/.git/...    Alice's clone (vault: <tmp>/alice)
      <tmp>/bob/wiki/ccat/.git/...      Bob's clone (vault: <tmp>/bob)

    Both clones contain the same initial sessions and
    ``.lore-briefing.yml`` after the seed.
    """
    origin = tmp_path / "origin.git"
    alice_vault = tmp_path / "alice"
    bob_vault = tmp_path / "bob"
    _init_bare(origin)

    # Alice clones into <alice_vault>/wiki/ccat and seeds sessions.
    alice_wiki = alice_vault / "wiki" / "ccat"
    _clone_wiki(origin, alice_wiki, name="alice")
    _seed_wiki_files(alice_wiki, [
        ("2026-04-15-fix-a", "- did the A thing"),
        ("2026-04-16-fix-b", "- did the B thing"),
    ])
    _commit_all(alice_wiki, "seed: initial sessions + briefing config")
    _git(alice_wiki, "push", "-u", "origin", "main")

    # Bob clones from origin and inherits the same starting state.
    bob_wiki = bob_vault / "wiki" / "ccat"
    _clone_wiki(origin, bob_wiki, name="bob")

    return tmp_path, alice_vault, bob_vault


@pytest.fixture
def vault_factory(monkeypatch):
    """Return a callable that points LORE_ROOT at a given path."""
    def use(vault_root: Path) -> None:
        monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return use


def _candidates(*names: str) -> list[dict]:
    """Fake `new_sessions` entries for the session files the seed helpers wrote."""
    return [
        {
            "path": f"sessions/{name}.md",
            "date": name[:10],
            "slug": name[11:],
            "frontmatter": {},
            "linkage": {},
        }
        for name in names
    ]


def _stub_gather(monkeypatch: pytest.MonkeyPatch, candidates: list[dict]) -> None:
    """Replace `gather()` with the ledger-filter half only.

    The directory walk that used to find these candidates is gone; this
    keeps the ledger-driven "already incorporated" filtering real (via
    the retained `_read_ledger`) so pull-propagation stays meaningful.
    """
    from lore_core.config import get_wiki_root

    def fake_gather(*, wiki, since=None, include_body_sections=True, user=None, epic=None):
        wiki_path = get_wiki_root() / wiki
        ledger = _read_ledger(wiki_path)
        incorporated = set(ledger.get("incorporated") or [])
        new_sessions = [c for c in candidates if Path(c["path"]).name not in incorporated]
        return {
            "wiki": wiki,
            "today": "2026-04-20",
            "ledger": {
                "last_briefing": ledger.get("last_briefing"),
                "incorporated_count": len(incorporated),
            },
            "sink_config": None,
            "new_sessions": new_sessions,
        }

    monkeypatch.setattr("lore_cli.briefing_cmd.gather", fake_gather)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_oneshot_no_remote_proceeds_and_skips_push(tmp_path: Path, vault_factory, monkeypatch):
    """Single-user wiki without a remote: briefing publishes + marks
    locally, no push attempted, no errors."""
    vault = tmp_path / "vault"
    wiki = vault / "wiki" / "ccat"
    wiki.mkdir(parents=True)
    _seed_wiki_files(wiki, [("2026-04-15-fix-a", "- A thing")])
    out = tmp_path / "brief.md"
    vault_factory(vault)
    _stub_gather(monkeypatch, _candidates("2026-04-15-fix-a"))

    code = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code == 0
    assert out.exists()
    ledger = json.loads((wiki / ".briefing-ledger.json").read_text())
    assert "2026-04-15-fix-a.md" in ledger["incorporated"]


def test_oneshot_pushes_ledger_after_mark(two_hosts, vault_factory, monkeypatch):
    """Happy path: clean repo with remote → briefing publishes, commits
    ledger, pushes. Remote should now be ahead by one commit."""
    tmp, alice_vault, _bob_vault = two_hosts
    alice_wiki = alice_vault / "wiki" / "ccat"
    out = tmp / "alice-brief.md"
    vault_factory(alice_vault)
    _stub_gather(monkeypatch, _candidates("2026-04-15-fix-a", "2026-04-16-fix-b"))

    code = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code == 0
    # Alice's last commit is the briefing commit.
    last_msg = _git(alice_wiki, "log", "-1", "--format=%s").stdout.strip()
    assert last_msg.startswith("briefing(ccat):")
    # Origin has it too.
    origin = tmp / "origin.git"
    origin_log = _git(origin, "log", "-1", "--format=%s").stdout.strip()
    assert origin_log.startswith("briefing(ccat):")


def test_oneshot_pulls_before_gather(two_hosts, vault_factory, monkeypatch):
    """Cross-host coordination: Alice publishes + pushes; Bob's briefing
    pulls Alice's ledger update first and finds 0 new sessions."""
    tmp, alice_vault, bob_vault = two_hosts
    alice_out = tmp / "alice-brief.md"
    bob_out = tmp / "bob-brief.md"
    candidates = _candidates("2026-04-15-fix-a", "2026-04-16-fix-b")

    # Alice publishes.
    vault_factory(alice_vault)
    _stub_gather(monkeypatch, candidates)
    code_a = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{alice_out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code_a == 0

    # Bob runs briefing — should pull Alice's mark and find nothing new.
    vault_factory(bob_vault)
    code_b = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{bob_out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code_b == 0
    assert not bob_out.exists(), (
        "Bob should not have published — Alice's marks should propagate via pull"
    )
    # Bob's local ledger should now match Alice's via the FF pull.
    bob_ledger = json.loads(
        (bob_vault / "wiki" / "ccat" / ".briefing-ledger.json").read_text()
    )
    assert "2026-04-15-fix-a.md" in bob_ledger["incorporated"]
    assert "2026-04-16-fix-b.md" in bob_ledger["incorporated"]


def test_oneshot_aborts_on_dirty_ledger(two_hosts, vault_factory, capsys):
    """Dirty working tree → abort before publishing."""
    tmp, alice_vault, _bob_vault = two_hosts
    # Dirty the ledger file (simulating an interrupted previous run).
    (alice_vault / "wiki" / "ccat" / ".briefing-ledger.json").write_text(
        json.dumps({"last_briefing": "2026-04-01", "incorporated": []})
    )
    out = tmp / "should-not-exist.md"
    vault_factory(alice_vault)

    code = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code == 1
    assert not out.exists(), "Briefing must not publish when repo is dirty"
    err = capsys.readouterr().err
    assert "uncommitted changes" in err
    assert "--no-git" in err


def test_oneshot_aborts_on_diverged(two_hosts, vault_factory, capsys):
    """Local has unpushed commits and remote has moved → abort."""
    tmp, alice_vault, bob_vault = two_hosts
    alice_wiki = alice_vault / "wiki" / "ccat"
    bob_wiki = bob_vault / "wiki" / "ccat"
    # Bob makes a local commit (not pushed).
    (bob_wiki / "notes.md").write_text("bob's local note\n")
    _commit_all(bob_wiki, "bob: local note")
    # Alice pushes a divergent commit.
    (alice_wiki / "other.md").write_text("alice's note\n")
    _commit_all(alice_wiki, "alice: other note")
    _git(alice_wiki, "push")

    out = tmp / "should-not-exist.md"
    vault_factory(bob_vault)
    code = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "diverged" in err.lower()


def test_oneshot_no_git_flag_bypasses_coordination(two_hosts, vault_factory, monkeypatch):
    """--no-git should publish even when the repo would otherwise abort."""
    tmp, alice_vault, _bob_vault = two_hosts
    # Dirty the ledger.
    (alice_vault / "wiki" / "ccat" / ".briefing-ledger.json").write_text(
        json.dumps({"last_briefing": "2026-04-01", "incorporated": []})
    )
    out = tmp / "brief.md"
    vault_factory(alice_vault)
    _stub_gather(monkeypatch, _candidates("2026-04-15-fix-a", "2026-04-16-fix-b"))

    code = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=f"markdown:{out}",
        dry_run=False,
        no_mark=False,
        no_llm=True,
        no_git=True,
    )
    assert code == 0
    assert out.exists()


def test_oneshot_dry_run_skips_git(two_hosts, vault_factory, monkeypatch):
    """--dry-run preview must not touch git either (no commit, no push)."""
    tmp, alice_vault, _bob_vault = two_hosts
    alice_wiki = alice_vault / "wiki" / "ccat"
    # Dirty the ledger so a non-dry-run would abort.
    (alice_wiki / ".briefing-ledger.json").write_text(
        json.dumps({"last_briefing": "2026-04-01", "incorporated": []})
    )
    head_before = _git(alice_wiki, "rev-parse", "HEAD").stdout.strip()
    vault_factory(alice_vault)
    _stub_gather(monkeypatch, _candidates("2026-04-15-fix-a", "2026-04-16-fix-b"))

    code = _run_oneshot(
        wiki="ccat",
        since=None,
        sink_override=None,
        dry_run=True,
        no_mark=False,
        no_llm=True,
        no_git=False,
    )
    assert code == 0
    head_after = _git(alice_wiki, "rev-parse", "HEAD").stdout.strip()
    assert head_before == head_after, "dry-run must not create commits"
