"""`lore workflow after-merge`: board row to merged, resume line, tick, close."""

from __future__ import annotations

import io

import pytest
from lore_cli import workflow_cmd
from lore_workflow import after_merge

BOARD = """\
<!-- lore-orchestrate-epic:status v1 -->

| Feature | Issue | Tier | Batch | State | PR |
|---|---|---|---|---|---|
| One | buchbend/lore#645 | AFK | 1 | review | - |
| Two | buchbend/lore#647 | AFK | 1 | review | - |
| Far | other/repo#647 | AFK | 1 | review | - |

## Ledger

- [adr] open — x

## Notes

- none
"""
EPIC = "## Build order\n\n- [ ] buchbend/lore#645 — One\n- [ ] buchbend/lore#647 — Two\n- [ ] other/repo#647 — Far (blocked by #647)\n"


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def epic_body(self, repo, epic):
        self.calls.append(("body", repo, epic))
        return EPIC

    def set_epic_body(self, repo, epic, body):
        self.calls.append(("set", repo, epic, body))

    def close(self, repo, issue, comment):
        self.calls.append(("close", repo, issue, comment))


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(after_merge, "GH", fake)
    return fake


def _run(monkeypatch, board=BOARD, issue="647"):
    monkeypatch.setattr("sys.stdin", io.StringIO(board))
    return workflow_cmd.main(
        [
            "after-merge",
            "--board",
            "-",
            "--issue",
            issue,
            "--sha",
            "abc1234",
            "--epic",
            "buchbend/lore#644",
        ]
    )


def test_after_merge_updates_board_ticks_and_closes(monkeypatch, capsys, gh):
    assert _run(monkeypatch) == 0
    out = capsys.readouterr().out
    assert "| Two | buchbend/lore#647 | AFK | 1 | merged | - |" in out
    assert "| One | buchbend/lore#645 | AFK | 1 | review | - |" in out
    resume = [ln for ln in out.splitlines() if ln.startswith("- [resume]")]
    assert len(resume) == 1 and "#647" in resume[0]
    assert out.index("- [resume]") < out.index("## Notes")
    (_, _, _, body) = next(c for c in gh.calls if c[0] == "set")
    assert "- [x] buchbend/lore#647" in body and "- [ ] buchbend/lore#645" in body
    assert ("close", "buchbend/lore", 647, "Merged into epic/644 at abc1234") in gh.calls


def test_unknown_issue_exits_1_without_github(monkeypatch, capsys, gh):
    assert _run(monkeypatch, issue="999") == 1
    assert gh.calls == []
    assert capsys.readouterr().out == ""


def test_cross_repo_row_closes_in_its_own_repo(monkeypatch, capsys, gh):
    assert _run(monkeypatch, issue="other/repo#647") == 0
    out = capsys.readouterr().out
    assert "| Far | other/repo#647 | AFK | 1 | merged | - |" in out
    assert "| Two | buchbend/lore#647 | AFK | 1 | review | - |" in out
    (_, _, _, body) = next(c for c in gh.calls if c[0] == "set")
    assert "- [x] other/repo#647" in body and "- [ ] buchbend/lore#647" in body
    assert ("close", "other/repo", 647, "Merged into epic/644 at abc1234") in gh.calls
