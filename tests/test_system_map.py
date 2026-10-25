"""The system map: the reader, `lore map`, `lore map check` and the neighbours block."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_cli import map_cmd
from lore_cli.hooks import hook_app
from lore_cli.map_cmd import app
from lore_core import system_map
from lore_core.system_map import edges_for, load_maps, neighbours_block, problems
from typer.testing import CliRunner

CCAT = """\
---
type: system-map
repos: [ccatobs/ops-db, ccatobs/data-center, ccatobs/web]
edges:
  - consumer: ccatobs/data-center
    producer: ccatobs/ops-db
    interface: "SQLAlchemy models"
    pin: sha
    breaks_when: "a migration changes a table data-center reads"
  - consumer: ccatobs/web
    producer: ccatobs/data-center
    interface: "REST API"
---

# CCAT data system

Prose for humans.
"""

TOOLS = """\
---
type: system-map
repos: [ccatobs/ops-db, ccatobs/tools]
edges:
  - consumer: ccatobs/tools
    producer: ccatobs/ops-db
    interface: "the ops-db CLI"
    pin: package
---
"""


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    root = tmp_path / "vault" / "wiki" / "ccat"
    (root / "systems").mkdir(parents=True)
    (root / "systems" / "ccat.md").write_text(CCAT)
    (root / "systems" / "tools.md").write_text(TOOLS)
    (root / "systems" / "readme.md").write_text("---\ntype: topic\n---\n\nNot a map.\n")
    return root


def test_reader_returns_required_and_optional_fields(wiki: Path) -> None:
    maps = load_maps([wiki])
    assert [m.name for m in maps] == ["ccat", "tools"]
    edge = edges_for(maps, "ccatobs/data-center")[0]
    assert (edge.consumer, edge.producer, edge.interface) == (
        "ccatobs/data-center",
        "ccatobs/ops-db",
        "SQLAlchemy models",
    )
    assert edge.pin == "sha"
    assert edge.breaks_when == "a migration changes a table data-center reads"
    assert edge.system == "ccat"
    rest = edges_for(maps, "ccatobs/web")[0]
    assert (rest.pin, rest.breaks_when) == (None, None)


def test_a_repo_in_two_maps_gets_the_edges_of_both(wiki: Path) -> None:
    edges = edges_for(load_maps([wiki]), "ccatobs/ops-db")
    assert [(e.system, e.consumer) for e in edges] == [
        ("ccat", "ccatobs/data-center"),
        ("tools", "ccatobs/tools"),
    ]


def test_a_note_without_the_system_map_type_is_no_map(wiki: Path) -> None:
    assert "readme" not in [m.name for m in load_maps([wiki])]
    assert load_maps([wiki.parent / "missing"]) == []


def test_check_passes_a_valid_map(wiki: Path) -> None:
    assert [p for m in load_maps([wiki]) for p in problems(m)] == []


@pytest.mark.parametrize(
    ("edge", "expected"),
    [
        ("  - producer: a/b\n    interface: x\n", "edge 1 lacks consumer"),
        ("  - consumer: a/b\n    interface: x\n", "edge 1 lacks producer"),
        ("  - consumer: a/b\n    producer: a/c\n", "edge 1 lacks interface"),
        (
            "  - consumer: a/b\n    producer: a/c\n    interface: x\n    pin: branch\n",
            "edge 1 (a/b <- a/c): unknown pin 'branch'",
        ),
        (
            "  - consumer: a/b\n    producer: z/z\n    interface: x\n",
            "edge 1 (a/b <- z/z): repo z/z is not in repos",
        ),
    ],
)
def test_check_names_the_note_and_the_edge_and_exits_1(
    wiki: Path, edge: str, expected: str
) -> None:
    bad = wiki / "systems" / "bad.md"
    bad.write_text(f"---\ntype: system-map\nrepos: [a/b, a/c]\nedges:\n{edge}---\n")
    found = [p for m in load_maps([wiki]) for p in problems(m)]
    assert found == [f"{bad}: {expected}"]
    result = CliRunner().invoke(app, ["check", "--wiki-dir", str(wiki)])
    assert result.exit_code == 1
    assert expected in result.output


def test_cli_check_exits_0_on_valid_maps(wiki: Path) -> None:
    result = CliRunner().invoke(app, ["check", "--wiki-dir", str(wiki)])
    assert result.exit_code == 0, result.output
    assert "2 maps, 3 edges: ok" in result.output


def test_cli_prints_the_edges_of_one_repo_from_both_maps(wiki: Path) -> None:
    result = CliRunner().invoke(app, ["ccatobs/ops-db", "--wiki-dir", str(wiki)])
    assert result.exit_code == 0, result.output
    assert "ccatobs/data-center <- ccatobs/ops-db: SQLAlchemy models" in result.output
    assert "ccatobs/tools <- ccatobs/ops-db: the ops-db CLI" in result.output


def test_cli_without_a_repo_prints_every_map(wiki: Path) -> None:
    result = CliRunner().invoke(app, ["--wiki-dir", str(wiki)])
    assert result.exit_code == 0, result.output
    assert "## ccat (" in result.output
    assert "## tools (" in result.output
    assert "ccatobs/web <- ccatobs/data-center: REST API" in result.output


def test_cli_json_prints_a_list_of_edges(wiki: Path) -> None:
    result = CliRunner().invoke(app, ["ccatobs/web", "--json", "--wiki-dir", str(wiki)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "system": "ccat",
            "note": str(wiki / "systems" / "ccat.md"),
            "consumer": "ccatobs/web",
            "producer": "ccatobs/data-center",
            "interface": "REST API",
            "pin": None,
            "breaks_when": None,
        }
    ]


def test_neighbours_of_a_consumer_show_producer_interface_pin_and_breaks_when(
    wiki: Path,
) -> None:
    edges = edges_for(load_maps([wiki]), "ccatobs/data-center")
    block = neighbours_block(edges, "ccatobs/data-center")
    assert block.startswith("## Neighbours (system map)\n")
    assert (
        "- uses ccatobs/ops-db: SQLAlchemy models; pin: sha; "
        "breaks when: a migration changes a table data-center reads"
    ) in block.splitlines()
    assert "- used by ccatobs/web: REST API" in block.splitlines()


def test_neighbours_over_the_budget_list_what_fits_and_count_the_rest() -> None:
    edges = [
        system_map.Edge(
            consumer=f"o/c{i}", producer="o/p", interface="x" * 80, system="s", note=Path("s.md")
        )
        for i in range(12)
    ]
    block = neighbours_block(edges, "o/p")
    assert len(block) <= system_map.NEIGHBOURS_BUDGET_CHARS
    listed = [line for line in block.splitlines() if line.startswith("- used by")]
    assert 0 < len(listed) < 12
    assert block.endswith(f"… {12 - len(listed)} more: `lore map o/p`")


def test_no_edge_no_block() -> None:
    assert neighbours_block([], "o/p") is None


def _session_start(repo: Path, monkeypatch: pytest.MonkeyPatch, slug: str) -> str:
    monkeypatch.setattr(system_map, "current_repo", lambda _cwd: slug)
    return (
        CliRunner()
        .invoke(hook_app, ["session-start", "--cwd", str(repo), "--plain", "--probe"], input="")
        .output
    )


@pytest.fixture
def repo(tmp_path: Path, wiki: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "CONTEXT.md").write_text("- **Ledger** — the working list.\n")
    return root


def test_session_start_puts_the_neighbours_before_the_glossary(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _session_start(repo, monkeypatch, "ccatobs/data-center")
    assert "- uses ccatobs/ops-db: SQLAlchemy models; pin: sha" in out
    assert out.index("## Neighbours") < out.index("## Glossary")


def test_session_start_without_an_edge_adds_no_block(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "Neighbours" not in _session_start(repo, monkeypatch, "someone/else")


def test_map_is_a_hidden_command() -> None:
    from lore_cli.__main__ import app as root

    result = CliRunner().invoke(root, ["--help"])
    assert " map " not in result.output
    assert map_cmd.app is not None
