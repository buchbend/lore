"""`lore map scan`: candidate edges from the checkouts of a scope folder."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest
from lore_cli import hooks
from lore_cli.hooks import hook_app
from lore_cli.map_cmd import app
from lore_core import system_map_scan
from lore_core.system_map import load_maps
from lore_core.system_map_scan import find_checkouts, missing, scan
from typer.testing import CliRunner


def _checkout(folder: Path, slug: str) -> Path:
    path = folder / slug.split("/")[1]
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", f"git@github.com:{slug}.git"],
        check=True,
    )
    return path


@pytest.fixture
def scope(tmp_path: Path) -> Path:
    folder = tmp_path / "ccat"
    ops = _checkout(folder, "ccatobs/ops-db")
    (ops / "pyproject.toml").write_text('[project]\nname = "ops-db"\n')
    lib = _checkout(folder, "ccatobs/libx")
    (lib / "pyproject.toml").write_text('[project]\nname = "libx"\n')
    ui = _checkout(folder, "ccatobs/ui")
    (ui / "package.json").write_text('{"name": "@ccat/ui"}')

    dc = _checkout(folder, "ccatobs/data-center")
    (dc / "pyproject.toml").write_text(
        "[project]\n"
        'name = "data-center"\n'
        'dependencies = ["ops-db>=1.2", "requests", '
        '"libx @ git+https://github.com/ccatobs/libx.git@v1.2"]\n'
    )
    web = _checkout(folder / "apps", "ccatobs/web")
    (web / "package.json").write_text(
        json.dumps({"name": "web", "dependencies": {"@ccat/ui": "^1.0", "react": "^18"}})
    )
    (web / "Dockerfile").write_text(
        "FROM ghcr.io/ccatobs/ops-db:1.0 AS base\nFROM base\nFROM python:3.12\n"
    )
    (web / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: ghcr.io/ccatobs/data-center:latest\n"
        "  db:\n    image: postgres:16\n"
    )
    tools = _checkout(folder, "ccatobs/tools")
    (tools / ".gitmodules").write_text(
        '[submodule "vendor/libx"]\n\tpath = vendor/libx\n'
        "\turl = https://github.com/ccatobs/libx.git\n"
    )
    return folder


def _pairs(folder: Path) -> set[tuple[str, str, str]]:
    return {(c.consumer, c.producer, c.kind) for c in scan(folder)}


def test_checkouts_are_named_by_their_remote_not_their_folder(scope: Path) -> None:
    found = find_checkouts(scope)
    assert found["ccatobs/web"] == scope / "apps" / "web"
    assert len(found) == 6


def test_a_submodule_of_another_checkout_is_a_candidate(scope: Path) -> None:
    hit = [c for c in scan(scope) if c.kind == "submodule"]
    assert [(c.consumer, c.producer, c.source, c.pin) for c in hit] == [
        ("ccatobs/tools", "ccatobs/libx", ".gitmodules", "sha")
    ]


def test_a_pyproject_git_url_and_package_name_are_candidates(scope: Path) -> None:
    pairs = _pairs(scope)
    assert ("ccatobs/data-center", "ccatobs/libx", "git-url") in pairs
    assert ("ccatobs/data-center", "ccatobs/ops-db", "package") in pairs
    git = next(c for c in scan(scope) if c.kind == "git-url")
    assert git.pin == "tag"


def test_a_package_json_package_another_checkout_publishes_is_a_candidate(
    scope: Path,
) -> None:
    hit = next(c for c in scan(scope) if c.consumer == "ccatobs/web" and c.kind == "package")
    assert (hit.producer, hit.detail, hit.pin) == ("ccatobs/ui", "@ccat/ui", "package")


def test_dockerfile_from_and_compose_images_are_candidates(scope: Path) -> None:
    images = {(c.producer, c.source) for c in scan(scope) if c.kind == "image"}
    assert images == {
        ("ccatobs/ops-db", "Dockerfile"),
        ("ccatobs/data-center", "docker-compose.yml"),
    }


def test_a_dependency_without_a_checkout_is_left_out(scope: Path) -> None:
    details = " ".join(c.detail for c in scan(scope))
    for name in ("requests", "react", "python:3.12", "postgres:16", "base"):
        assert name not in details.split()


def test_the_scan_needs_no_network(scope: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("the scan opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert len(scan(scope)) == 6


def test_missing_lists_the_candidates_no_edge_holds(scope: Path, tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "ccat.md").write_text(
        "---\ntype: system-map\nrepos: [ccatobs/tools, ccatobs/libx]\nedges:\n"
        "  - consumer: ccatobs/tools\n    producer: ccatobs/libx\n    interface: vendored\n---\n"
    )
    left = missing(scan(scope), load_maps([wiki]))
    assert ("ccatobs/tools", "ccatobs/libx") not in {(c.consumer, c.producer) for c in left}
    assert len(left) == 5


def test_cli_scan_json_names_the_folder_candidates_and_the_tidy_switch(
    scope: Path, tmp_path: Path
) -> None:
    result = CliRunner().invoke(
        app, ["scan", "--scope", str(scope), "--json", "--wiki-dir", str(tmp_path / "none")]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["folder"] == str(scope)
    assert data["tidy"] is True
    assert len(data["candidates"]) == 6
    assert len(data["missing"]) == 6
    assert data["checkouts"]["ccatobs/web"] == str(scope / "apps" / "web")


def test_tidy_false_in_the_scope_config_reaches_the_scan_output(
    scope: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "xdg" / "lore" / "config.yml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"scopes:\n  ccat:\n    path: {scope}\n    map:\n      tidy: false\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert system_map_scan.scope_for(scope / "apps" / "web") == ("ccat", scope, False)
    result = CliRunner().invoke(
        app, ["scan", "--scope", str(scope), "--json", "--wiki-dir", str(tmp_path / "none")]
    )
    data = json.loads(result.output)
    assert (data["scope"], data["tidy"]) == ("ccat", False)


def test_a_repo_in_a_map_without_a_checkout_is_reported(scope: Path, tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "ccat.md").write_text(
        "---\ntype: system-map\nrepos: [ccatobs/tools, ccatobs/gone]\nedges:\n"
        "  - consumer: ccatobs/tools\n    producer: ccatobs/gone\n    interface: x\n---\n"
    )
    result = CliRunner().invoke(
        app, ["scan", "--scope", str(scope), "--json", "--wiki-dir", str(wiki)]
    )
    assert json.loads(result.output)["no_checkout"] == ["ccatobs/gone"]


def test_record_and_hint_name_the_count_and_the_skill(tmp_path: Path) -> None:
    system_map_scan.record_missing(tmp_path, "ccat", 3)
    assert system_map_scan.hint(tmp_path, "ccat") == (
        "System map: 3 candidate edges the map lacks. Run `/lore:map` to review them."
    )
    system_map_scan.record_missing(tmp_path, "ccat", 0)
    assert system_map_scan.hint(tmp_path, "ccat") is None
    assert system_map_scan.hint(tmp_path, "other") is None


def test_session_start_shows_the_hint_and_spawns_the_scan_without_waiting(
    scope: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / "wiki" / "ccat").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault))
    cfg = tmp_path / "xdg" / "lore" / "config.yml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"scopes:\n  ccat:\n    path: {scope}\n    wiki: ccat\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LORE_CURATOR_MODE", raising=False)
    system_map_scan.record_missing(vault, "ccat", 2)
    for name in dir(hooks):
        if name.startswith("_spawn_"):
            monkeypatch.setattr(hooks, name, lambda *a, **k: None)
    spawned: list[tuple] = []
    monkeypatch.setattr(hooks, "_spawn_map_scan", lambda *a: spawned.append(a))
    monkeypatch.setattr(
        system_map_scan, "scan", lambda *_a: pytest.fail("SessionStart ran the scan inline")
    )

    out = (
        CliRunner()
        .invoke(hook_app, ["session-start", "--cwd", str(scope / "tools"), "--plain"], input="")
        .output
    )

    assert "2 candidate edges the map lacks. Run `/lore:map`" in out
    assert len(spawned) == 1


def test_the_background_child_records_the_count_the_map_lacks(
    scope: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / "wiki" / "ccat").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault))
    cfg = tmp_path / "xdg" / "lore" / "config.yml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"scopes:\n  ccat:\n    path: {scope}\n    wiki: ccat\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = CliRunner().invoke(hook_app, ["map-scan", "--cwd", str(scope / "tools")])

    assert result.exit_code == 0, result.output
    assert system_map_scan.hint(vault, "ccat").startswith("System map: 6 candidate edges")


def test_ssh_urls_and_refs_with_a_slash_resolve_to_the_checkout(tmp_path: Path) -> None:
    folder = tmp_path / "s"
    _checkout(folder, "o/b")
    a = _checkout(folder, "o/a")
    (a / "pyproject.toml").write_text(
        '[project]\nname = "a"\ndependencies = ["b @ git+ssh://git@github.com/o/b.git@v1.0"]\n'
        '[project.optional-dependencies]\nx = ["b @ git+https://github.com/o/b@feature/x"]\n'
    )
    (a / "package.json").write_text(
        '{"dependencies": {"b": "git+ssh://git@github.com/o/b.git#v1"}}'
    )
    (a / ".gitmodules").write_text('[submodule "b"]\n\turl = ssh://git@github.com:22/o/b.git\n')
    hits = {(c.kind, c.source, c.pin) for c in scan(folder) if c.producer == "o/b"}
    assert hits == {
        ("git-url", "pyproject.toml", "tag"),
        ("git-url", "package.json", "tag"),
        ("submodule", ".gitmodules", "sha"),
    }
    refs = [c.detail for c in scan(folder) if c.source == "pyproject.toml"]
    assert len(refs) == 2


def test_a_malformed_file_in_one_checkout_does_not_stop_the_scan(scope: Path) -> None:
    (scope / "libx" / "pyproject.toml").write_text(
        '[project]\nname = "libx"\noptional-dependencies = "oops"\n'
    )
    assert len(scan(scope)) == 6


def test_canonical_repo_name_reads_ssh_and_git_urls() -> None:
    from lore_core.git import canonical_repo_name

    for url in (
        "ssh://git@github.com/o/b.git",
        "ssh://git@github.com:22/o/b.git",
        "git+ssh://git@github.com/o/b.git",
        "git://github.com/o/b.git",
        "git@github.com:o/b.git",
        "https://github.com/o/b",
    ):
        assert canonical_repo_name(url) == "o/b", url
