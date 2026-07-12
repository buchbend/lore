"""Unit tests for deterministic ref verification.

A fact's refs are the only thing that can earn a rendered line authoritative
phrasing, so what counts as evidence is the whole subject here. The rule is
positive-evidence-only: ``VERIFIED`` requires a check that ran and succeeded,
``MISSING`` a check that ran and came back empty, and ``UNCHECKED`` covers
everything else — offline, no ``gh``, no repo, an unparseable value. Absence of
failure is never promoted.

No network: the ``gh`` seam is faked in every test that reaches it, and git
runs against a throwaway repo under ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_core import ref_verify
from lore_core.ref_verify import MISSING, UNCHECKED, VERIFIED, verify_refs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit, one tag, one tracked file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "lib").mkdir()
    (root / "lib" / "thing.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    _git(root, "tag", "v1.2.0")
    return root


@pytest.fixture
def head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _fake_gh(monkeypatch: pytest.MonkeyPatch, returncode: int | None) -> list[list[str]]:
    """Intercept `gh` calls (git still runs for real). Returns the call log."""
    calls: list[list[str]] = []
    real = ref_verify._run

    def fake(cmd: list[str], *, cwd=None):
        if cmd and cmd[0] == "gh":
            calls.append(cmd)
            return returncode
        return real(cmd, cwd=cwd)

    monkeypatch.setattr(ref_verify, "_run", fake)
    return calls


# ---------------------------------------------------------------------------
# commits — frontmatter facts, then local git
# ---------------------------------------------------------------------------


def test_a_commit_in_the_session_facts_is_verified_without_touching_git():
    sha = "41cab11f0e5a3b2c9d8e7f6a5b4c3d2e1f0a9b8c"
    verdicts = verify_refs([("commit", "41cab11")], commits=[sha])

    assert verdicts[("commit", "41cab11")] == VERIFIED


def test_a_commit_that_exists_in_the_local_repo_is_verified(repo: Path, head: str):
    verdicts = verify_refs([("commit", head[:7])], repo_root=repo)

    assert verdicts[("commit", head[:7])] == VERIFIED


def test_a_commit_that_does_not_exist_demotes_to_missing(repo: Path):
    dead = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    verdicts = verify_refs([("commit", dead)], repo_root=repo)

    assert verdicts[("commit", dead)] == MISSING


def test_a_commit_with_no_repo_to_check_against_is_unchecked_never_verified():
    verdicts = verify_refs([("commit", "41cab11")])

    assert verdicts[("commit", "41cab11")] == UNCHECKED


def test_a_commit_value_that_is_not_a_sha_is_unchecked_and_never_reaches_git(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ref values are model-authored: they never become git arguments."""
    calls: list[list[str]] = []
    monkeypatch.setattr(ref_verify, "_run", lambda cmd, *, cwd=None: calls.append(cmd) or 0)

    verdicts = verify_refs([("commit", "--upload-pack=touch /tmp/pwned")], repo_root=repo)

    assert verdicts[("commit", "--upload-pack=touch /tmp/pwned")] == UNCHECKED
    assert calls == []


# ---------------------------------------------------------------------------
# tags — local git only
# ---------------------------------------------------------------------------


def test_an_existing_tag_is_verified(repo: Path):
    assert verify_refs([("tag", "v1.2.0")], repo_root=repo)[("tag", "v1.2.0")] == VERIFIED


def test_a_nonexistent_tag_demotes_to_missing(repo: Path):
    assert verify_refs([("tag", "v9.9.9")], repo_root=repo)[("tag", "v9.9.9")] == MISSING


def test_a_tag_with_no_repo_is_unchecked():
    assert verify_refs([("tag", "v1.2.0")])[("tag", "v1.2.0")] == UNCHECKED


def test_a_tag_value_that_could_be_an_option_never_reaches_git(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[list[str]] = []
    monkeypatch.setattr(ref_verify, "_run", lambda cmd, *, cwd=None: calls.append(cmd) or 0)

    verdicts = verify_refs([("tag", "--exec=whoami")], repo_root=repo)

    assert verdicts[("tag", "--exec=whoami")] == UNCHECKED
    assert calls == []


# ---------------------------------------------------------------------------
# files — frontmatter facts, then the filesystem
# ---------------------------------------------------------------------------


def test_a_file_in_the_session_facts_is_verified_without_the_filesystem():
    verdicts = verify_refs([("file", "lib/gone.py")], files=["lib/gone.py"])

    assert verdicts[("file", "lib/gone.py")] == VERIFIED


def test_a_file_that_exists_in_the_repo_is_verified(repo: Path):
    verdicts = verify_refs([("file", "lib/thing.py")], repo_root=repo)

    assert verdicts[("file", "lib/thing.py")] == VERIFIED


def test_a_file_that_does_not_exist_demotes_to_missing(repo: Path):
    verdicts = verify_refs([("file", "lib/invented.py")], repo_root=repo)

    assert verdicts[("file", "lib/invented.py")] == MISSING


def test_a_relative_file_with_no_repo_root_is_unchecked():
    assert verify_refs([("file", "lib/thing.py")])[("file", "lib/thing.py")] == UNCHECKED


# ---------------------------------------------------------------------------
# PRs and issues — best-effort via gh, never promoted on failure
# ---------------------------------------------------------------------------


def test_a_pr_gh_can_resolve_is_verified(repo: Path, monkeypatch: pytest.MonkeyPatch):
    calls = _fake_gh(monkeypatch, 0)

    verdicts = verify_refs([("pr", "#286")], repo_root=repo, repo="buchbend/lore")

    assert verdicts[("pr", "#286")] == VERIFIED
    assert calls[0][:4] == ["gh", "pr", "view", "286"]
    assert "--repo" in calls[0] and "buchbend/lore" in calls[0]


def test_a_pr_gh_cannot_resolve_is_unchecked_not_verified(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _fake_gh(monkeypatch, 1)

    assert verify_refs([("pr", "4711")], repo_root=repo, repo="o/r")[("pr", "4711")] == UNCHECKED


def test_an_offline_pr_check_is_unchecked_and_never_raises(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """No network, no `gh` binary, a timeout — all the same: no evidence."""
    _fake_gh(monkeypatch, None)

    assert verify_refs([("pr", "286")], repo_root=repo, repo="o/r")[("pr", "286")] == UNCHECKED


def test_an_issue_gh_can_resolve_is_verified(repo: Path, monkeypatch: pytest.MonkeyPatch):
    calls = _fake_gh(monkeypatch, 0)

    verdicts = verify_refs([("issue", "286")], repo_root=repo, repo="o/r")

    assert verdicts[("issue", "286")] == VERIFIED
    assert calls[0][:3] == ["gh", "issue", "view"]


def test_the_gh_query_never_asks_for_a_field_gh_can_answer_by_itself(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """`gh pr view <N> --json number` echoes N back and exits 0 without ever
    reaching the API — it verifies every number, including invented ones. Any
    field gh can answer offline is a forged check mark, so none may be asked
    for."""
    calls = _fake_gh(monkeypatch, 0)

    verify_refs([("pr", "99999")], repo_root=repo, repo="o/r")

    fields = calls[0][calls[0].index("--json") + 1].split(",")
    assert "number" not in fields


def test_a_pr_number_is_only_ever_verified_by_gh(repo: Path, monkeypatch: pytest.MonkeyPatch):
    """No frontmatter fast-path: `linkage.prs`/`issues` are regexes over commit
    messages and branch names — text agents write. A commit saying "Closes
    #99999" is not evidence #99999 exists."""
    calls = _fake_gh(monkeypatch, None)  # gh cannot answer

    verdicts = verify_refs([("pr", "99999"), ("issue", "99999")], repo_root=repo, repo="o/r")

    assert set(verdicts.values()) == {UNCHECKED}
    assert len(calls) == 2  # both went to the one oracle that can say


def test_a_pr_with_nowhere_to_ask_is_unchecked_and_calls_nothing(monkeypatch: pytest.MonkeyPatch):
    calls = _fake_gh(monkeypatch, 0)

    assert verify_refs([("pr", "286")])[("pr", "286")] == UNCHECKED
    assert calls == []


def test_a_pr_value_that_is_not_a_number_is_unchecked(repo: Path, monkeypatch: pytest.MonkeyPatch):
    calls = _fake_gh(monkeypatch, 0)

    verdicts = verify_refs([("pr", "the ledger PR")], repo_root=repo, repo="o/r")

    assert verdicts == {("pr", "the ledger PR"): UNCHECKED}
    assert calls == []


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------


def test_a_broken_toolchain_never_raises_and_never_verifies(
    repo: Path, head: str, monkeypatch: pytest.MonkeyPatch
):
    """Every subprocess blows up: the render still gets a verdict for each ref."""

    def boom(*args, **kwargs):
        raise OSError("no such tool")

    monkeypatch.setattr(subprocess, "run", boom)

    verdicts = verify_refs(
        [("commit", head), ("tag", "v1.2.0"), ("pr", "286")],
        repo_root=repo,
        repo="o/r",
    )

    assert set(verdicts.values()) == {UNCHECKED}


def test_an_unknown_ref_type_is_unchecked(repo: Path):
    assert verify_refs([("blog", "x")], repo_root=repo)[("blog", "x")] == UNCHECKED


def test_a_repeated_ref_is_checked_once(repo: Path, head: str, monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    real = ref_verify._run

    def counting(cmd, *, cwd=None):
        calls.append(cmd)
        return real(cmd, cwd=cwd)

    monkeypatch.setattr(ref_verify, "_run", counting)

    verdicts = verify_refs([("commit", head), ("commit", head)], repo_root=repo)

    assert verdicts == {("commit", head): VERIFIED}
    assert len(calls) == 1


def test_module_has_no_llm_wiring():
    """Verification is code, all the way down — no model may touch a verdict."""
    src = Path(ref_verify.__file__).read_text()
    for forbidden in ("lore_adapters", "llm_client", "get_adapter", "compose", "prompt"):
        assert forbidden not in src, f"ref_verify must not reference {forbidden!r}"
