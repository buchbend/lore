"""Tests for the deterministic risk level (`lore workflow risk`, PRD 0015 § Build)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from lore_cli import workflow_cmd
from lore_core.root_config import RiskConfig
from lore_workflow.risk import assess, parse_unified_diff, python_fan_in


def _file_diff(path: str, added: list[str], *, new: bool = False, deleted: int = 0) -> str:
    head = f"diff --git a/{path} b/{path}\n"
    if new:
        head += "new file mode 100644\n--- /dev/null\n"
    else:
        head += f"--- a/{path}\n"
    head += f"+++ b/{path}\n@@ -1,{deleted} +1,{len(added)} @@\n"
    body = "".join(f"-old {i}\n" for i in range(deleted)) + "".join(f"+{ln}\n" for ln in added)
    return head + body


def _level(diff: str, **kw) -> tuple[str, list[str]]:
    result = assess(parse_unified_diff(diff), RiskConfig(), **kw)
    return result.level, result.reasons


def test_parse_unified_diff_counts_lines_and_status() -> None:
    diff = _file_diff("a.py", ["x", "+++ not a header"], deleted=1) + _file_diff(
        "db/migrations/0002_add.py", ["y"], new=True
    )
    files = parse_unified_diff(diff)
    assert [(f.path, f.added, f.deleted, f.status) for f in files] == [
        ("a.py", 2, 1, "M"),
        ("db/migrations/0002_add.py", 1, 0, "A"),
    ]
    assert files[0].added_lines == ["x", "+++ not a header"]


def test_small_plain_diff_is_low() -> None:
    level, reasons = _level(_file_diff("lib/util.py", ["x = 1"]))
    assert level == "low"
    assert reasons == []


def test_over_line_threshold_is_high() -> None:
    level, reasons = _level(_file_diff("lib/util.py", ["x"] * 401))
    assert level == "high"
    assert any("401 changed lines" in r for r in reasons)


def test_over_file_threshold_is_high() -> None:
    diff = "".join(_file_diff(f"lib/m{i}.py", ["x"]) for i in range(11))
    level, reasons = _level(diff)
    assert level == "high"
    assert any("11 changed files" in r for r in reasons)


@pytest.mark.parametrize(
    ("path", "added", "new", "needle"),
    [
        ("app/migrations/0003_users.py", ["pass"], True, "migration"),
        ("alembic/versions/abc123_add.py", ["pass"], True, "migration"),
        ("db/tables.sql", ["CREATE TABLE t (id int);"], True, "schema"),
        ("api/user_schema.json", ["{}"], False, "schema"),
        ("lib/lore_core/config_schema.py", ["x = 1"], False, "config schema"),
        ("web/views.py", ["@router.post('/users')", "def create(): ..."], False, "API route"),
        ("web/app.py", ["@app.get('/health')"], False, "API route"),
        ("lib/auth/tokens.py", ["x = 1"], False, "sensitive"),
        ("deploy/secrets.env", ["X=1"], False, "sensitive"),
    ],
)
def test_structural_signals_are_high(path, added, new, needle) -> None:
    level, reasons = _level(_file_diff(path, added, new=new))
    assert level == "high"
    assert any(needle in r for r in reasons), reasons


def test_sensitive_paths_come_from_config() -> None:
    cfg = RiskConfig(sensitive_paths=["billing/**"])
    result = assess(parse_unified_diff(_file_diff("billing/charge.py", ["x"])), cfg)
    assert result.level == "high"
    assert "billing/**" in result.reasons[0]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def fanin_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root, "lib/pkg/__init__.py", "")
    _write(root, "lib/pkg/core.py", "X = 1\n")
    _write(root, "lib/pkg/types.py", "Y = 1\n")
    for i in range(9):
        _write(root, f"lib/pkg/user{i}.py", "from pkg.core import X\nfrom types import Z\n")
    _write(root, "lib/pkg/rel.py", "from . import core\nfrom .types import Y\n")
    return root


def test_python_fan_in_resolves_absolute_and_relative_imports(fanin_repo: Path) -> None:
    fan_in = python_fan_in(fanin_repo)
    assert fan_in["lib/pkg/core.py"] == 10
    # `from types import Z` is the stdlib module, not lib/pkg/types.py.
    assert fan_in["lib/pkg/types.py"] == 1


def test_top_decile_fan_in_file_is_high(fanin_repo: Path) -> None:
    diff = _file_diff("lib/pkg/core.py", ["X = 2"])
    level, reasons = _level(diff, root=fanin_repo)
    assert level == "high"
    assert any("fan-in" in r and "lib/pkg/core.py" in r for r in reasons)

    level, _ = _level(_file_diff("lib/pkg/user3.py", ["x"]), root=fanin_repo)
    assert level == "low"


def test_fan_in_skipped_without_python_files_says_so(tmp_path: Path) -> None:
    result = assess(parse_unified_diff(_file_diff("a.py", ["x"])), RiskConfig(), root=tmp_path)
    assert result.level == "low"
    assert any("fan-in" in n and "skipped" in n for n in result.notes)


# --- CLI -------------------------------------------------------------------


def _stub_gh(tmp_path: Path, monkeypatch, script: str) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\n" + script)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_cli_pr_high_prints_level_and_reason(tmp_path, monkeypatch, capsys) -> None:
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text(_file_diff("lib/big.py", ["x"] * 500))
    _stub_gh(tmp_path, monkeypatch, f'[ "$1 $2 $3" = "pr diff 42" ] && cat {diff_file}\n')
    monkeypatch.chdir(tmp_path)
    rc = workflow_cmd.main(["risk", "--pr", "42"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "high"
    assert "500 changed lines" in out


def test_cli_json_low(tmp_path, monkeypatch, capsys) -> None:
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text(_file_diff("README.md", ["hello"]))
    monkeypatch.chdir(tmp_path)
    rc = workflow_cmd.main(["risk", "--diff", str(diff_file), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["level"] == "low"
    assert payload["reasons"] == []
    assert payload["files"] == 1
    assert payload["lines"] == 1


def test_cli_gh_failure_exits_nonzero_naming_it(tmp_path, monkeypatch, capsys) -> None:
    _stub_gh(tmp_path, monkeypatch, 'echo "HTTP 404: Not Found" >&2\nexit 1\n')
    monkeypatch.chdir(tmp_path)
    rc = workflow_cmd.main(["risk", "--pr", "7"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "gh pr diff 7" in err
    assert "HTTP 404" in err


def test_cli_range_reads_git_diff(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "a.txt").write_text("one\n")
    git("add", ".")
    git("commit", "-qm", "one")
    (repo / "auth").mkdir()
    (repo / "auth" / "login.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "two")
    monkeypatch.chdir(repo)
    rc = workflow_cmd.main(["risk", "--range", "HEAD~1..HEAD"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "high"
    assert "auth/login.py" in out


def test_cli_needs_exactly_one_input(capsys) -> None:
    assert workflow_cmd.main(["risk"]) == 1
    assert "one of --pr, --range or --diff" in capsys.readouterr().err
