"""The systems page and `lore system`: the system map for humans, and checkout state."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_cli.stats_cmd import app as stats_app
from lore_cli.system_cmd import app as system_app
from lore_core import systems_page
from lore_core.system_checkouts import Checkout, load_target, locate, order, save_target, state
from lore_core.system_map import load_maps
from lore_core.systems_page import RepoView, ScopeView, render
from typer.testing import CliRunner

runner = CliRunner()

MAP = """---
type: system-map
repos: [ccatobs/ops-db, ccatobs/data-center, ccatobs/web, ccatobs/archive]
edges:
  - consumer: ccatobs/data-center
    producer: ccatobs/ops-db
    interface: "SQLAlchemy models"
    pin: sha
    breaks_when: "a migration changes a table data-center reads"
  - consumer: ccatobs/web
    producer: ccatobs/data-center
    interface: "REST API"
  - consumer: ccatobs/web
    producer: ccatobs/ops-db
    interface: "ops-db image"
    pin: image
---

# CCAT data system

The observatory writes to **ops-db**; `data-center` serves it.

- web shows it
"""


def _git(path: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return out.stdout.decode().strip()


def _checkout(folder: Path, slug: str, description: str | None = None) -> Path:
    path = folder / slug.split("/")[1]
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "remote", "add", "origin", f"git@github.com:{slug}.git")
    if description:
        (path / "pyproject.toml").write_text(
            f'[project]\nname = "{path.name}"\ndescription = "{description}"\n'
        )
    (path / "README.md").write_text(f"# {path.name}\n\nThe {path.name} repo.\n")
    _commit(path, "init")
    _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")  # as if pushed
    return path


def _commit(path: Path, message: str) -> None:
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / "wiki" / "ccat" / "systems").mkdir(parents=True)
    (root / "wiki" / "ccat" / "systems" / "ccat.md").write_text(MAP)
    folder = tmp_path / "src" / "ccat"
    _checkout(folder, "ccatobs/ops-db", "The operations database")
    _checkout(folder, "ccatobs/data-center")
    _checkout(folder, "ccatobs/web")
    _checkout(folder, "ccatobs/stray")
    config = tmp_path / "xdg" / "lore" / "config.yml"
    config.parent.mkdir(parents=True)
    config.write_text(f"scopes:\n  ccat:\n    path: {folder}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LORE_ROOT", str(root))
    return root


def _maps(vault: Path):
    return load_maps([vault / "wiki" / "ccat"])


def test_order_puts_each_producer_before_its_consumers(vault: Path) -> None:
    assert order(_maps(vault)) == [
        "ccatobs/archive",
        "ccatobs/ops-db",
        "ccatobs/data-center",
        "ccatobs/web",
    ]


def test_a_cycle_does_not_lose_a_repo(vault: Path) -> None:
    text = MAP.replace(
        "edges:\n",
        "edges:\n  - consumer: ccatobs/ops-db\n    producer: ccatobs/web\n    interface: x\n",
    )
    (vault / "wiki" / "ccat" / "systems" / "ccat.md").write_text(text)
    assert sorted(order(_maps(vault))) == sorted(
        ["ccatobs/archive", "ccatobs/ops-db", "ccatobs/data-center", "ccatobs/web"]
    )


def test_the_page_draws_each_system_repo_and_scope(vault: Path) -> None:
    path = systems_page.write_page(vault)
    assert path == vault / ".lore" / "systems.html"
    html = path.read_text()
    assert "<script" not in html
    assert 'class="map"' in html  # the diagram
    assert "SQLAlchemy models" in html and "a migration changes a table" in html
    assert "<strong>ops-db</strong>" in html  # the map's body, as prose
    assert "The operations database" in html  # pyproject description
    assert "The data-center repo." in html  # README first paragraph
    assert 'id="repo-ccatobs-archive"' in html and "no local checkout" in html
    assert "branch <code>main</code>" in html
    assert "ccatobs/stray" in html and "not in a map" in html
    assert 'href="stats.html"' in html


def test_the_page_is_the_same_for_the_same_inputs(vault: Path) -> None:
    maps = _maps(vault)
    views = {"ccatobs/web": RepoView(repo="ccatobs/web", scope="ccat", path="~/w", branch="x")}
    scopes = [ScopeView(name="ccat", folder="~/src/ccat", repos=["ccatobs/web"])]
    assert render(maps, views, scopes) == render(maps, views, scopes)


def test_no_map_opens_the_explainer(tmp_path: Path) -> None:
    html = render([], {}, [])
    assert "<details open>" in html and "No system map yet" in html


def test_lore_stats_writes_both_pages_and_opens_the_asked_one(vault: Path) -> None:
    result = runner.invoke(stats_app, ["--page", "systems", "--no-open"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith("systems.html")
    assert (vault / ".lore" / "stats.html").exists()
    assert 'href="systems.html"' in (vault / ".lore" / "stats.html").read_text()


def test_state_names_the_action_for_a_target_branch(vault: Path, tmp_path: Path) -> None:
    web = tmp_path / "src" / "ccat" / "web"
    _git(web, "branch", "feat/x")
    checkout = Checkout(repo="ccatobs/web", scope="ccat", path=web)
    assert state(checkout, "feat/x").action() == "switch"
    assert state(checkout, "main").action() == "pull"
    assert state(checkout, "nope").action() == "default"
    assert state(checkout, "feat/x").local_work() == []


@pytest.mark.parametrize(
    ("change", "work"),
    [
        (lambda web: (web / "README.md").write_text("changed\n"), "1 changed files"),
        (lambda web: (web / "notes.txt").write_text("new\n"), "1 untracked files"),
        (
            lambda web: ((web / "README.md").write_text("local\n"), _commit(web, "local")),
            "1 commits on no remote",
        ),
    ],
)
def test_local_work_stops_the_switch(vault: Path, tmp_path: Path, change, work: str) -> None:
    web = tmp_path / "src" / "ccat" / "web"
    _git(web, "branch", "feat/x")
    change(web)
    got = state(Checkout(repo="ccatobs/web", scope="ccat", path=web), "feat/x")
    assert got.local_work() == [work]
    assert got.action() == "stop"


def test_an_unfinished_merge_stops_the_switch(vault: Path, tmp_path: Path) -> None:
    web = tmp_path / "src" / "ccat" / "web"
    _git(web, "switch", "-qc", "side")
    (web / "README.md").write_text("side\n")
    _commit(web, "side")
    _git(web, "switch", "-q", "main")
    (web / "README.md").write_text("main\n")
    _commit(web, "main")
    merge = ["-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "side"]
    subprocess.run(["git", "-C", str(web), *merge], capture_output=True, check=False)
    got = state(Checkout(repo="ccatobs/web", scope="ccat", path=web), "main")
    assert "a merge in progress" in got.local_work()
    assert "1 unmerged files" in got.local_work()
    assert got.action() == "stop"


def test_a_branch_held_by_another_worktree_blocks_the_switch(vault: Path, tmp_path: Path) -> None:
    web = tmp_path / "src" / "ccat" / "web"
    _git(web, "worktree", "add", "-q", "-b", "feat/y", str(tmp_path / "web-y"))
    got = state(Checkout(repo="ccatobs/web", scope="ccat", path=web), "feat/y")
    assert got.target_elsewhere is not None
    assert got.action() == "blocked"


def test_a_remote_only_branch_counts(vault: Path, tmp_path: Path) -> None:
    web = tmp_path / "src" / "ccat" / "web"
    _git(web, "update-ref", "refs/remotes/origin/feat/z", "HEAD")
    got = state(Checkout(repo="ccatobs/web", scope="ccat", path=web), "feat/z")
    assert got.target_remote == "origin/feat/z"
    assert got.action() == "switch"


def _status(*args: str) -> dict:
    result = runner.invoke(system_app, ["status", *args, "--json"])
    assert result.exit_code == 0, result.output
    (only,) = json.loads(result.output)["systems"]
    return only


def test_system_status_lists_checkouts_producers_first(vault: Path, tmp_path: Path) -> None:
    _git(tmp_path / "src" / "ccat" / "ops-db", "branch", "feat/x")
    data = _status("ccat", "--branch", "feat/x")
    assert [c["repo"] for c in data["checkouts"]] == [
        "ccatobs/ops-db",
        "ccatobs/data-center",
        "ccatobs/web",
    ]
    assert [c["action"] for c in data["checkouts"]] == ["switch", "default", "default"]
    # data-center and web have no feat/x: they belong on main, and are there.
    assert [c["at_target"] for c in data["checkouts"]] == [False, True, True]
    assert data["at_target"] == 2
    assert [r["repo"] for r in data["no_checkout"]] == ["ccatobs/archive"]
    assert "checkouts: {ccatobs/archive: <folder>}" in data["no_checkout"][0]["hint"]
    assert data["local_work"] == [] and data["warnings"] == []
    assert {(e["consumer"], e["pin"]) for e in data["pinned"]} == {
        ("ccatobs/data-center", "sha"),
        ("ccatobs/web", "image"),
    }


def test_system_status_names_each_checkout_with_local_work(vault: Path, tmp_path: Path) -> None:
    (tmp_path / "src" / "ccat" / "data-center" / "README.md").write_text("changed\n")
    data = _status("--branch", "main")
    assert [(w["repo"], w["work"]) for w in data["local_work"]] == [
        ("ccatobs/data-center", ["1 changed files"])
    ]
    assert [c["action"] for c in data["checkouts"]] == ["pull", "stop", "pull"]


def test_the_saved_target_is_the_default_comparison(vault: Path, tmp_path: Path) -> None:
    _git(tmp_path / "src" / "ccat" / "web", "branch", "feat/x")
    assert _status()["target"] is None
    result = runner.invoke(system_app, ["target", "feat/x"])  # the only map
    assert result.exit_code == 0, result.output
    assert load_target(vault, "ccat")["branch"] == "feat/x"
    data = _status("ccat")
    assert data["target"]["branch"] == "feat/x" and data["target"]["from"] == "saved"
    web = [c for c in data["checkouts"] if c["repo"] == "ccatobs/web"][0]
    assert web["at_target"] is False and web["action"] == "switch"
    assert _status("--branch", "main")["target"]["from"] == "--branch"
    assert load_target(vault, "ccat")["branch"] == "feat/x"  # --branch saves nothing
    assert runner.invoke(system_app, ["target", "--clear", "ccat"]).exit_code == 0
    assert load_target(vault, "ccat") is None


def test_the_text_status_reads_like_git_status(vault: Path, tmp_path: Path) -> None:
    (tmp_path / "src" / "ccat" / "web" / "scratch.txt").write_text("x\n")
    save_target(vault, "ccat", "main", datetime(2026, 10, 25, tzinfo=UTC))
    result = runner.invoke(system_app, ["status", "ccat"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "ccat · target main (saved, set 2026-10-25) · 3 of 3 checkouts on target"
    assert lines[1].split()[:4] == ["✓", "ccatobs/ops-db", "main", "clean"]
    assert lines[3].split()[:2] == ["✓", "ccatobs/web"] and "1 untracked files" in lines[3]
    assert "ccatobs/archive" in lines[4] and "no checkout" in lines[4]
    assert "pin: ccatobs/data-center pins ccatobs/ops-db by sha" in result.output
    assert runner.invoke(system_app, ["status", "ccat"]).output == result.output


def test_a_checkouts_entry_wins_over_the_search(vault: Path, tmp_path: Path) -> None:
    elsewhere = _checkout(tmp_path / "other", "ccatobs/archive")
    config = tmp_path / "xdg" / "lore" / "config.yml"
    config.write_text(config.read_text() + f"checkouts:\n  ccatobs/archive: {elsewhere}\n")
    found, warnings = locate(["ccatobs/archive", "ccatobs/web"])
    assert found["ccatobs/archive"].path == elsewhere
    assert found["ccatobs/archive"].source == "pinned"
    assert found["ccatobs/web"].source == "scope ccat"
    assert warnings == []
    data = _status("ccat")
    assert data["no_checkout"] == []
    assert "(pinned)" in runner.invoke(system_app, ["status", "ccat"]).output


@pytest.mark.parametrize("wrong", ["missing", "other-remote"])
def test_a_wrong_checkouts_entry_warns_and_falls_back(
    vault: Path, tmp_path: Path, wrong: str
) -> None:
    folder = tmp_path / "nowhere"
    if wrong == "other-remote":
        folder = _checkout(tmp_path / "other", "someone/else")
    config = tmp_path / "xdg" / "lore" / "config.yml"
    config.write_text(config.read_text() + f"checkouts:\n  ccatobs/web: {folder}\n")
    found, warnings = locate(["ccatobs/web"])
    assert found["ccatobs/web"].path == tmp_path / "src" / "ccat" / "web"
    assert len(warnings) == 1 and "searched the scope folders instead" in warnings[0]
    assert ("someone/else" in warnings[0]) == (wrong == "other-remote")


def test_the_checkouts_key_is_known_to_the_config(vault: Path, tmp_path: Path) -> None:
    import warnings

    from lore_core.root_config import load_root_config

    config = tmp_path / "xdg" / "lore" / "config.yml"
    config.write_text(config.read_text() + "checkouts:\n  ccatobs/web: /x\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert load_root_config(vault).checkouts == {"ccatobs/web": "/x"}


def test_the_page_warns_of_local_work(vault: Path, tmp_path: Path) -> None:
    (tmp_path / "src" / "ccat" / "web" / "scratch.txt").write_text("x\n")
    html = systems_page.write_page(vault).read_text()
    assert '<span class="tag warn">1 untracked files</span>' in html


def test_the_page_shows_the_target_and_marks_drift(vault: Path, tmp_path: Path) -> None:
    _git(tmp_path / "src" / "ccat" / "web", "branch", "feat/x")
    save_target(vault, "ccat", "feat/x", datetime(2026, 10, 25, tzinfo=UTC))
    html = systems_page.write_page(vault).read_text()
    assert "target <code>feat/x</code> (set 2026-10-25) · 2 of 3 checkouts on target" in html
    assert "off target: ccatobs/web" in html
    assert '<g class="node drift"><title>ccatobs/web: not on the target branch</title>' in html


def test_system_status_rejects_an_unknown_system(vault: Path) -> None:
    result = runner.invoke(system_app, ["status", "nope"])
    assert result.exit_code == 1


SKILL = Path(__file__).resolve().parent.parent / "skills" / "system" / "SKILL.md"


def _skill() -> str:
    return " ".join(SKILL.read_text(encoding="utf-8").split())


def test_system_skill_is_invocable_and_reads_the_status() -> None:
    import yaml

    raw = SKILL.read_text(encoding="utf-8")
    fm = yaml.safe_load(raw[4 : raw.find("\n---", 3)])
    assert fm["name"] == "lore:system" and fm["user_invocable"] is True
    text = _skill()
    assert "lore system status <system> --fetch --json" in text
    assert "lore system target <branch> <system>" in text
    assert "producers first" in text
    for action in ("`switch`", "`pull`", "`default`", "`stop`", "`blocked`"):
        assert action in text


def test_system_skill_asks_before_each_change_and_never_discards_work() -> None:
    text = _skill()
    assert "Ask before every change, one repo at a time" in text
    assert "A yes for one repo covers no other repo" in text
    assert "Never discard work" in text
    for command in ("`reset`", "`clean`", "`checkout -- <file>`", "a force push"):
        assert command in text
    assert "Never pass `--no-verify`" in text
    assert "Never move a pin" in text
    assert "Run `lore system status <system>` and show it" in text
