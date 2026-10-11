"""Search sources: one list per source (PRD 0017, ADR 0013).

No network and no live wiki: DokuWiki answers from a recorded JSON-RPC
fixture through a patched ``urlopen``; ``command`` sources run a real
shell one-liner; ``gh`` is a stub binary on PATH.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest
import yaml
from lore_core import sources
from lore_core.config_schema import secret_key_errors

FIXTURE = Path(__file__).parent / "fixtures" / "dokuwiki_search_pages.json"


@pytest.fixture
def cfg_home(tmp_path, monkeypatch):
    """Isolated XDG config home plus a helper that writes config.yml."""
    home = tmp_path / "xdg"
    (home / "lore").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    monkeypatch.chdir(tmp_path)

    def write(config: dict, secrets: dict | None = None) -> None:
        (home / "lore" / "config.yml").write_text(yaml.safe_dump(config))
        if secrets is not None:
            (home / "lore" / "secrets.yml").write_text(yaml.safe_dump(secrets))

    return write


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(monkeypatch, payloads: dict[str, object]):
    """Answer each JSON-RPC method from `payloads`; return the request log."""
    seen: list[dict] = []

    def urlopen(req, timeout=None):
        body = json.loads(req.data)
        seen.append({"url": req.full_url, "auth": req.get_header("Authorization"), **body})
        return _FakeResponse(json.dumps(payloads[body["method"]]).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return seen


DOKU = {"kind": "dokuwiki", "url": "https://wiki.example.org", "secret": "team-wiki"}


def test_dokuwiki_search_posts_jsonrpc_with_bearer_token(cfg_home, monkeypatch):
    cfg_home({"sources": {"team": DOKU}}, {"team-wiki": "s3cret"})
    seen = _fake_urlopen(monkeypatch, {"core.searchPages": json.loads(FIXTURE.read_text())})

    result = sources.search_sources("receiver", k=5, cwd=Path.cwd(), github_repos=[])

    team = next(s for s in result if s["name"] == "team")
    assert team["kind"] == "dokuwiki"
    assert team["note"] is None
    assert team["hits"][0] == {
        "ref": "team:team:receiver:alignment",
        "title": "Receiver alignment",
        "url": "https://wiki.example.org/doku.php?id=team:receiver:alignment",
        "snippet": "Steps to align the <b>receiver</b> before a run",
    }
    assert seen[0]["url"] == "https://wiki.example.org/lib/exe/jsonrpc.php"
    assert seen[0]["auth"] == "Bearer s3cret"
    assert seen[0]["method"] == "core.searchPages"
    assert seen[0]["params"]["query"] == "receiver"


def test_dokuwiki_read_returns_core_getpage_text(cfg_home, monkeypatch):
    cfg_home({"sources": {"team": DOKU}}, {"team-wiki": "s3cret"})
    seen = _fake_urlopen(
        monkeypatch,
        {"core.getPage": {"jsonrpc": "2.0", "id": 1, "result": "====== Alignment ======"}},
    )

    text = sources.read_source("team:team:receiver:alignment", cwd=Path.cwd())

    assert text == "====== Alignment ======"
    assert seen[0]["method"] == "core.getPage"
    assert seen[0]["params"] == {"page": "team:receiver:alignment"}


def test_read_source_ignores_refs_of_undefined_sources(cfg_home):
    cfg_home({"sources": {"team": DOKU}})
    assert sources.read_source("nope:page", cwd=Path.cwd()) is None
    assert sources.read_source("acme/widgets#4", cwd=Path.cwd()) is None


def test_command_source_gets_json_on_stdin_and_returns_stdout_hits(cfg_home, tmp_path):
    stdin_log = tmp_path / "stdin.json"
    script = tmp_path / "src.py"
    script.write_text(
        "import sys, json\n"
        f"d = sys.stdin.read(); open({str(stdin_log)!r}, 'w').write(d)\n"
        "print(json.dumps([{'ref': 'x:1', 'title': 'T', 'url': 'u', 'snippet': 's'}]))\n"
    )
    cfg_home({"sources": {"mine": {"kind": "command", "run": f"{sys.executable} {script}"}}})

    result = sources.search_sources("abc", k=3, cwd=Path.cwd(), github_repos=[])

    mine = next(s for s in result if s["name"] == "mine")
    assert mine["hits"] == [{"ref": "x:1", "title": "T", "url": "u", "snippet": "s"}]
    assert json.loads(stdin_log.read_text()) == {"op": "search", "query": "abc", "k": 3}


def test_slow_source_yields_empty_list_and_note_others_still_return(cfg_home):
    cfg_home(
        {
            "sources": {
                "slow": {"kind": "command", "run": "sleep 5", "timeout": 0.3},
                "fast": {"kind": "command", "run": 'echo \'[{"ref":"f:1","title":"F"}]\''},
            }
        }
    )

    start = time.monotonic()
    result = sources.search_sources("q", k=5, cwd=Path.cwd(), github_repos=[])

    assert time.monotonic() - start < 3
    by = {s["name"]: s for s in result}
    assert by["slow"]["hits"] == [] and "timed out" in by["slow"]["note"]
    assert by["fast"]["hits"][0]["ref"] == "f:1"


def test_github_failure_gives_empty_list_and_note(cfg_home, tmp_path, monkeypatch):
    cfg_home({})
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    result = sources.search_sources("q", k=5, cwd=Path.cwd(), github_repos=["acme/widgets"])

    gh = next(s for s in result if s["name"] == "github")
    assert gh["kind"] == "github" and gh["hits"] == []
    assert "acme/widgets" in gh["note"]


def test_route_sources_limit_the_lists_and_no_route_queries_all(cfg_home):
    one = {"kind": "command", "run": "echo '[]'"}
    cfg_home({"sources": {"a": one, "b": one}, "defaults": {"sources": ["a"]}})
    names = [s["name"] for s in sources.search_sources("q", 5, Path.cwd(), [])]
    assert names == ["a"]

    cfg_home({"sources": {"a": one, "b": one}})
    names = [s["name"] for s in sources.search_sources("q", 5, Path.cwd(), [])]
    assert names == ["github", "a", "b"]


def test_source_definition_holding_a_secret_value_fails_validation():
    raw = {"sources": {"team": {"kind": "dokuwiki", "url": "u", "token": "abc"}}}
    errors = secret_key_errors(raw)
    assert errors and "sources.team.token" in errors[0]
    assert secret_key_errors({"sources": {"team": DOKU}}) == []
