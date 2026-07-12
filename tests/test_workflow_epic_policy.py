"""Per-repo epic policy: target branch + deploy gate (#223).

`/orchestrate-epic` resolves both facts deterministically at Map time instead
of guessing deploy semantics. Fixture repos are built in tmp_path: a bare repo
stands in for `origin`, AGENTS.md carries (or omits) the deploy-gate marker.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lore_cli import workflow_cmd
from lore_workflow import epic_policy


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")


def _add_bare_origin(root: Path, bare: Path, *branches: str) -> None:
    subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True
    )
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "origin", "main")
    for branch in branches:
        _git(root, "branch", branch)
        _git(root, "push", "origin", branch)


# --- target_branch ---------------------------------------------------------


def test_target_main_when_remote_lacks_develop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _add_bare_origin(repo, tmp_path / "origin.git")
    assert epic_policy.resolve_epic_policy(repo).target_branch == "main"


def test_target_develop_when_present_on_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _add_bare_origin(repo, tmp_path / "origin.git", "develop")
    assert epic_policy.resolve_epic_policy(repo).target_branch == "develop"


def test_target_main_when_no_remote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)  # no origin configured at all
    assert epic_policy.resolve_epic_policy(repo).target_branch == "main"


def test_target_main_when_not_a_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert epic_policy.resolve_epic_policy(plain).target_branch == "main"


# --- deploy_gate -----------------------------------------------------------


def _agents(root: Path, body: str) -> None:
    (root / "AGENTS.md").write_text(body, encoding="utf-8")


def test_deploy_gate_true_when_marker_in_section(tmp_path: Path) -> None:
    _agents(
        tmp_path,
        "# Repo\n\n## Epic merge policy\n\nepic-merge-policy: confirm\n",
    )
    assert epic_policy.resolve_epic_policy(tmp_path).deploy_gate is True


def test_deploy_gate_false_when_no_marker(tmp_path: Path) -> None:
    _agents(tmp_path, "# Repo\n\n## Epic merge policy\n\nMerges are automatic.\n")
    assert epic_policy.resolve_epic_policy(tmp_path).deploy_gate is False


def test_deploy_gate_false_when_no_agents_file(tmp_path: Path) -> None:
    assert epic_policy.resolve_epic_policy(tmp_path).deploy_gate is False


def test_deploy_gate_false_when_marker_outside_section(tmp_path: Path) -> None:
    # Marker appears, but under a different heading — must not trip the gate.
    _agents(
        tmp_path,
        "# Repo\n\n## Notes\n\nepic-merge-policy: confirm\n\n## Epic merge policy\n\nn/a\n",
    )
    assert epic_policy.resolve_epic_policy(tmp_path).deploy_gate is False


# --- CLI -------------------------------------------------------------------


def test_epic_policy_cmd_emits_json(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _add_bare_origin(repo, tmp_path / "origin.git", "develop")
    _agents(repo, "## Epic merge policy\n\nepic-merge-policy: confirm\n")
    rc = workflow_cmd.main(["epic-policy", str(repo)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"target_branch": "develop", "deploy_gate": True}
