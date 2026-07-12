"""Machine-readable counts + `--json` output for the roadmap validator (#223).

`/orchestrate-epic` reads roadmap size (feature rows, distinct repos, dependency
edges) to plan batches; deriving it from validator prose is scraping. These
counts come straight off the already-parsed rows — no second table parse.
"""

from __future__ import annotations

import json
from pathlib import Path

from lore_cli import workflow_cmd
from lore_workflow import roadmap_validator as mod

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "roadmaps"


def _counts(name: str) -> mod.RoadmapCounts:
    return mod.roadmap_counts(mod.validate_roadmap((FIXTURES / name).read_text(encoding="utf-8")))


def test_counts_well_formed() -> None:
    # 3 features, one repo (widget), edges = 0 + 1 + 2 blocked-by tokens.
    c = _counts("well-formed.md")
    assert (c.rows, c.repos, c.edges) == (3, 1, 3)


def test_counts_cross_repo() -> None:
    # 3 features across two repos, edges = 0 + 1 + 1.
    c = _counts("cross-repo.md")
    assert (c.rows, c.repos, c.edges) == (3, 2, 2)


def test_counts_empty_when_columns_bad() -> None:
    # Wrong columns → no rows parsed → all-zero counts, no crash.
    c = _counts("malformed-columns.md")
    assert (c.rows, c.repos, c.edges) == (0, 0, 0)


def test_validate_roadmap_json_ok(capsys) -> None:
    rc = workflow_cmd.main(["validate-roadmap", "--json", str(FIXTURES / "well-formed.md")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["rows"] == 3
    assert payload["repos"] == 1
    assert payload["edges"] == 3
    assert payload["problems"] == []


def test_validate_roadmap_json_invalid(capsys) -> None:
    rc = workflow_cmd.main(["validate-roadmap", "--json", str(FIXTURES / "cyclic.md")])
    assert rc != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["problems"]
    assert any(p["kind"] == "cycle" for p in payload["problems"])


def test_validate_roadmap_human_path_still_works(capsys) -> None:
    # Without --json the existing human/exit-coded path is unchanged.
    rc = workflow_cmd.main(["validate-roadmap", str(FIXTURES / "well-formed.md")])
    assert rc == 0
    assert "roadmap OK" in capsys.readouterr().out
