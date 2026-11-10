"""`lore workflow spec-branch` (#556)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lore_cli import workflow_cmd
from typer.testing import CliRunner

PRD = "docs/prd/0001-x.md"
BODY = (
    "## Summary\nx\n\n**Spec branch:** `spec/x`\n"
    f"**PRD:** [`{PRD}`](../blob/spec/x/{PRD}) - the source of truth.\n\n"
    "## Build order\n\n| # | Feature | Issue | Repo | Type | Blocked by |\n"
    "|---|---|---|---|---|---|\n"
    "| 1 | A | o/r#1 | o/r | AFK | — |\n"
)
NO_BRANCH = BODY.replace("**Spec branch:** `spec/x`\n", "")


def _git(root: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True)


def _repo(tmp: Path, *, prd_on: str) -> Path:
    tmp.mkdir()
    _git(tmp, "init", "-b", "main")
    _git(tmp, "config", "user.email", "t@e.invalid")
    _git(tmp, "config", "user.name", "T")
    (tmp / "README.md").write_text("x\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "init")
    if prd_on == "none":
        return tmp
    if prd_on == "main":
        (tmp / PRD).parent.mkdir(parents=True)
        (tmp / PRD).write_text("prd\n")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-m", "prd")
        return tmp
    _git(tmp, "checkout", "-b", "spec/x")
    (tmp / PRD).parent.mkdir(parents=True)
    (tmp / PRD).write_text("prd\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "prd")
    _git(tmp, "checkout", "main")
    return tmp


def _run(repo: Path, body: str):
    return CliRunner().invoke(workflow_cmd.app, ["spec-branch", "--repo", str(repo)], input=body)


def test_prd_on_side_branch_needs_merge(tmp_path: Path) -> None:
    r = _run(_repo(tmp_path / "r", prd_on="spec"), BODY)
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == {"branch": "spec/x", "prd": PRD, "needs_merge": True}


def test_prd_on_target_needs_no_merge(tmp_path: Path) -> None:
    r = _run(_repo(tmp_path / "r", prd_on="main"), BODY)
    assert json.loads(r.stdout)["needs_merge"] is False


def test_no_spec_branch_and_no_prd_exits_1_naming_prd(tmp_path: Path) -> None:
    r = _run(_repo(tmp_path / "r", prd_on="none"), NO_BRANCH)
    assert r.exit_code == 1
    assert PRD in r.output


def test_spec_branch_only_on_origin_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", prd_on="spec")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "spec/x")
    _git(repo, "fetch", "origin")
    _git(repo, "branch", "-D", "spec/x")
    assert json.loads(_run(repo, BODY).stdout)["needs_merge"] is True


def test_validate_build_order_accepts_the_spec_branch_line() -> None:
    r = CliRunner().invoke(workflow_cmd.app, ["validate-build-order", "-"], input=BODY)
    assert r.exit_code == 0, r.output
