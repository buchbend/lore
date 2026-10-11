"""``resolve_route`` — folder scopes and one settings file (PRD 0016, ADR 0016).

The route is a pure function over the person's config
(``~/.config/lore/config.yml``, under the fake ``$HOME`` from conftest) and a
temporary folder tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from lore_core.config import user_config_path
from lore_core.route import resolve_route


def _person(cfg: dict) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg))
    return path


def _repo(path: Path, remote: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


def test_cwd_below_a_scope_path_gets_that_scope(tmp_path: Path) -> None:
    _person({"scopes": {"ccatobs": {"path": str(tmp_path / "git" / "ccatobs"), "wiki": "ccat"}}})
    repo = _repo(tmp_path / "git" / "ccatobs" / "data-center")

    route = resolve_route(repo / "src")

    assert route.scope == "ccatobs"
    assert route.wiki == "ccat"


def test_two_scope_folders_contain_cwd_longer_folder_wins(tmp_path: Path) -> None:
    _person(
        {
            "scopes": {
                "git": {"path": str(tmp_path / "git"), "wiki": "private"},
                "ccatobs": {"path": str(tmp_path / "git" / "ccatobs"), "wiki": "ccat"},
            }
        }
    )
    repo = _repo(tmp_path / "git" / "ccatobs" / "dc")

    assert resolve_route(repo).scope == "ccatobs"


def test_no_folder_match_remote_list_matches_git_remote(tmp_path: Path) -> None:
    _person(
        {
            "scopes": {
                "ccatobs": {
                    "path": str(tmp_path / "git" / "ccatobs"),
                    "remote": ["github.com/ccatobs/*"],
                    "wiki": "ccat",
                }
            }
        }
    )
    repo = _repo(tmp_path / "elsewhere" / "dc", remote="git@github.com:ccatobs/data-center.git")

    route = resolve_route(repo)

    assert route.scope == "ccatobs"
    assert route.wiki == "ccat"


def test_path_match_wins_over_a_remote_match(tmp_path: Path) -> None:
    _person(
        {
            "scopes": {
                "mine": {"path": str(tmp_path / "git"), "wiki": "private"},
                "ccatobs": {"remote": ["github.com/ccatobs/*"], "wiki": "ccat"},
            }
        }
    )
    repo = _repo(tmp_path / "git" / "dc", remote="https://github.com/ccatobs/data-center")

    assert resolve_route(repo).scope == "mine"


def test_two_scopes_matching_by_remote_only_give_no_scope_and_a_note(tmp_path: Path) -> None:
    _person(
        {
            "scopes": {
                "a": {"remote": ["github.com/ccatobs/*"], "wiki": "ccat"},
                "b": {"remote": ["github.com/ccatobs/data-center"], "wiki": "other"},
            }
        }
    )
    repo = _repo(tmp_path / "dc", remote="git@github.com:ccatobs/data-center.git")

    route = resolve_route(repo)

    assert route.scope is None
    assert route.wiki is None
    assert any("'a'" in n and "'b'" in n for n in route.notes)


def test_lore_yml_tracker_wins_and_is_marked_as_lore_yml(tmp_path: Path) -> None:
    _person(
        {
            "defaults": {"tracker": "github"},
            "scopes": {
                "ccatobs": {
                    "path": str(tmp_path / "git"),
                    "wiki": "ccat",
                    "sources": ["ccatwiki"],
                }
            },
            "sources": {"ccatwiki": {}},
        }
    )
    repo = _repo(tmp_path / "git" / "dc")
    (repo / ".lore.yml").write_text("tracker: gitlab\n")
    (repo / "src").mkdir()

    route = resolve_route(repo / "src")

    assert route.tracker == "gitlab"
    assert route.origins["tracker"] == ".lore.yml"
    assert (route.wiki, route.origins["wiki"]) == ("ccat", "scopes.ccatobs")
    assert route.scope == "ccatobs"
    assert route.sources == ("ccatwiki",)
    assert route.origins["sources"] == "scopes.ccatobs"
    assert route.sinks == ("file",)
    assert route.origins["sinks"] == "code default"


def test_invariant_reporting_boundary_undefined_sink_in_lore_yml_gives_file_sink(
    tmp_path: Path,
) -> None:
    """ADR 0016 invariant: a repo file names sinks, only the person defines them.

    A cloned repo whose ``.lore.yml`` names a sink the person never defined
    must not route entries anywhere but the local ``file`` sink.
    """
    _person({"scopes": {"ccatobs": {"path": str(tmp_path / "git"), "wiki": "ccat"}}})
    repo = _repo(tmp_path / "git" / "dc")
    (repo / ".lore.yml").write_text("logbook: [pm-elsewhere]\n")

    route = resolve_route(repo)

    assert route.sinks == ("file",)
    assert any("pm-elsewhere" in n for n in route.notes)


def test_invariant_reporting_boundary_defined_sink_passes_through(tmp_path: Path) -> None:
    _person(
        {
            "scopes": {"ccatobs": {"path": str(tmp_path / "git"), "wiki": "ccat"}},
            "sinks": {"pm-ccat": {"kind": "command"}},
        }
    )
    repo = _repo(tmp_path / "git" / "dc")
    (repo / ".lore.yml").write_text("logbook: [file, pm-ccat]\n")

    assert resolve_route(repo).sinks == ("file", "pm-ccat")


def test_environment_variable_beats_every_file(tmp_path: Path, monkeypatch) -> None:
    _person({"scopes": {"ccatobs": {"path": str(tmp_path / "git"), "wiki": "ccat"}}})
    repo = _repo(tmp_path / "git" / "dc")
    (repo / ".lore.yml").write_text("wiki: private\n")
    monkeypatch.setenv("LORE_WIKI", "scratch")

    route = resolve_route(repo)

    assert route.wiki == "scratch"
    assert route.origins["wiki"] == "env LORE_WIKI"


def test_person_defaults_apply_outside_every_scope(tmp_path: Path) -> None:
    _person({"defaults": {"wiki": "private"}, "scopes": {}})

    route = resolve_route(tmp_path)

    assert (route.wiki, route.scope, route.origins["wiki"]) == ("private", None, "defaults")


# ---------------------------------------------------------------------------
# Readers resolve through the route (no attachments.json)
# ---------------------------------------------------------------------------


def _two_wiki_vault(tmp_path: Path, monkeypatch) -> Path:
    vault = tmp_path / "vault"
    for w in ("ccat", "private"):
        (vault / "wiki" / w).mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault))
    return vault


def test_style_resolution_uses_the_route_wiki(tmp_path: Path, monkeypatch) -> None:
    from lore_cli.style_cmd import _wiki_dir

    vault = _two_wiki_vault(tmp_path, monkeypatch)
    _person({"scopes": {"ccatobs": {"path": str(tmp_path / "git"), "wiki": "ccat"}}})
    repo = _repo(tmp_path / "git" / "dc")
    monkeypatch.chdir(repo)

    assert _wiki_dir(None) == vault / "wiki" / "ccat"


def test_mcp_default_wiki_uses_the_route_wiki(tmp_path: Path, monkeypatch) -> None:
    from lore_mcp.server import _resolve_wiki_with_active_scope_fallback

    vault = _two_wiki_vault(tmp_path, monkeypatch)
    _person({"scopes": {"ccatobs": {"path": str(tmp_path / "git"), "wiki": "ccat"}}})
    repo = _repo(tmp_path / "git" / "dc")
    monkeypatch.chdir(repo)

    resolved = _resolve_wiki_with_active_scope_fallback(None)
    assert resolved is not None and resolved.resolve() == (vault / "wiki" / "ccat").resolve()


def test_route_is_cached_per_process_until_a_config_file_changes(
    tmp_path: Path, monkeypatch
) -> None:
    from lore_core import route as route_mod

    _person({"scopes": {"a": {"remote": ["github.com/ccatobs/*"], "wiki": "ccat"}}})
    repo = _repo(tmp_path / "dc", remote="git@github.com:ccatobs/dc.git")
    assert resolve_route(repo).wiki == "ccat"

    calls: list[Path] = []
    real = route_mod._remote_id
    monkeypatch.setattr(route_mod, "_remote_id", lambda cwd: calls.append(cwd) or real(cwd))
    assert resolve_route(repo).wiki == "ccat"
    assert calls == []  # served from the cache: no git call

    _person({"scopes": {"a": {"remote": ["github.com/ccatobs/*"], "wiki": "private"}}})
    assert resolve_route(repo).wiki == "private"
    (repo / ".lore.yml").write_text("tracker: gitlab\n")
    assert resolve_route(repo).tracker == "gitlab"


# ---------------------------------------------------------------------------
# Review round 1 (PR #477)
# ---------------------------------------------------------------------------


def test_lore_yml_wiki_and_scope_are_ignored_with_a_note(tmp_path: Path) -> None:
    """ADR 0016 Holds: a repo's `.lore.yml` names sinks, sources or a tracker.

    An old offer file carries `wiki:` and `scope:`. They must not route the
    repo, so a person who declined it is not routed by a committed file.
    """
    _person(
        {
            "scopes": {
                "org": {"path": str(tmp_path / "git"), "wiki": "ccat", "logbook": ["file", "pm"]}
            },
            "sinks": {"pm": {"kind": "command"}},
        }
    )
    repo = _repo(tmp_path / "git" / "dc")
    (repo / ".lore.yml").write_text("wiki: private\nscope: lore\nbackend: github\n")

    route = resolve_route(repo)

    assert (route.scope, route.origins["scope"]) == ("org", f"path {tmp_path / 'git'}")
    assert route.sinks == ("file", "pm")
    assert (route.wiki, route.origins["wiki"]) == ("ccat", "scopes.org")
    assert any("'wiki'" in n and "'scope'" in n and "lore setup scope" in n for n in route.notes)


def test_wiki_that_is_not_a_wiki_name_is_rejected(tmp_path: Path) -> None:
    _person({"defaults": {"wiki": "../../x"}})
    repo = _repo(tmp_path / "dc")

    route = resolve_route(repo)

    assert route.wiki is None
    assert route.origins["wiki"] == "rejected"
    assert any("../../x" in n for n in route.notes)


def test_no_git_call_outside_a_repo(tmp_path: Path, monkeypatch) -> None:
    from lore_core import route as route_mod

    _person({"scopes": {"a": {"remote": ["github.com/ccatobs/*"], "wiki": "ccat"}}})
    calls: list[Path] = []
    monkeypatch.setattr(route_mod, "_remote_id", lambda cwd: calls.append(cwd))

    plain = tmp_path / "plain"
    plain.mkdir()

    assert resolve_route(plain).scope is None
    assert calls == []


def test_remote_match_ignores_case(tmp_path: Path) -> None:
    _person({"scopes": {"ccatobs": {"remote": ["github.com/ccatobs/*"], "wiki": "ccat"}}})
    repo = _repo(tmp_path / "dc", remote="git@github.com:CCATObs/data-center.git")

    assert resolve_route(repo).scope == "ccatobs"


def test_scope_list_keys_accept_a_plain_string(tmp_path: Path) -> None:
    """The route and the schema agree: a string is a one-item list."""
    from lore_core.config_schema import validate_raw
    from lore_core.root_config import RootConfig

    raw = {
        "scopes": {
            "ccatobs": {
                "path": str(tmp_path / "git"),
                "remote": "github.com/ccatobs/*",
                "logbook": "file",
                "sources": "ccatwiki",
                "wiki": "ccat",
            }
        }
    }
    assert validate_raw(RootConfig, raw) == []
    _person(raw)
    route = resolve_route(_repo(tmp_path / "git" / "dc"))
    assert (route.sinks, route.sources) == (("file",), ("ccatwiki",))
