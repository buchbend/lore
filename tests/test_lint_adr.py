"""`lore lint adr` — decision records state their strength (ADR 0015, #444).

The verb reads each ADR numbered 0014 or higher under a repo's `docs/adr/`.
It fails on a record without a `## Holds` section, and on an `Invariant`
line whose test file or test function does not exist. ADRs 0001 to 0013
predate the section and are skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lore_cli.__main__ import app
from typer.testing import CliRunner

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent


def _adr(repo: Path, name: str, holds: str | None) -> Path:
    path = repo / "docs" / "adr" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# ADR\n\n- **Status:** Accepted\n\n"
    if holds is not None:
        body += f"## Holds\n\n{holds}\n\n"
    body += "## Context\n\nBackground.\n"
    path.write_text(body, encoding="utf-8")
    return path


def _lint(repo: Path, *extra: str):
    return runner.invoke(app, ["lint", "adr", str(repo), *extra])


def test_an_adr_without_holds_fails_and_names_the_file(tmp_path: Path) -> None:
    _adr(tmp_path, "0014-no-holds.md", holds=None)
    result = _lint(tmp_path)
    assert result.exit_code == 1, result.output
    assert "docs/adr/0014-no-holds.md:1" in result.output
    assert "Holds" in result.output


def _test_file(repo: Path) -> None:
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_real.py").write_text(
        "def test_exists():\n    pass\n\n\n"
        "class TestGroup:\n    def test_method(self):\n        pass\n",
        encoding="utf-8",
    )


def test_an_invariant_naming_a_missing_test_function_fails_and_names_the_line(
    tmp_path: Path,
) -> None:
    _test_file(tmp_path)
    _adr(
        tmp_path,
        "0016-bad-test.md",
        "- **Default:** fine.\n"
        "- **Invariant** (test: `tests/test_real.py::test_gone`): the parser rejects it.",
    )
    result = _lint(tmp_path)
    assert result.exit_code == 1, result.output
    # Line 8: title, blank, status, blank, `## Holds`, blank, Default, Invariant.
    assert "docs/adr/0016-bad-test.md:8" in result.output
    assert "test_gone" in result.output


def test_an_invariant_naming_a_missing_test_file_fails(tmp_path: Path) -> None:
    _adr(
        tmp_path,
        "0016-bad-file.md",
        "- **Invariant** (test: `tests/test_nowhere.py::test_x`): it holds.",
    )
    result = _lint(tmp_path)
    assert result.exit_code == 1, result.output
    assert "tests/test_nowhere.py" in result.output


def test_an_invariant_without_a_test_reference_fails(tmp_path: Path) -> None:
    _adr(tmp_path, "0016-no-test.md", "- **Invariant:** it holds.")
    result = _lint(tmp_path)
    assert result.exit_code == 1, result.output
    assert "docs/adr/0016-no-test.md:7" in result.output


def test_an_invariant_with_an_existing_test_passes(tmp_path: Path) -> None:
    _test_file(tmp_path)
    _adr(
        tmp_path,
        "0016-good.md",
        "- **Invariant** (test: `tests/test_real.py::test_exists`): it holds.\n"
        "- **Invariant** (test: tests/test_real.py::TestGroup::test_method): so does this.\n"
        "- **Invariant** (test: `tests/test_real.py::test_exists[case-a]`): per case.",
    )
    result = _lint(tmp_path)
    assert result.exit_code == 0, result.output


def test_a_holds_section_of_defaults_and_incidentals_passes(tmp_path: Path) -> None:
    _adr(tmp_path, "0014-soft.md", "- **Default:** do this.\n- **Incidental:** built so.")
    result = _lint(tmp_path)
    assert result.exit_code == 0, result.output


def test_an_empty_holds_section_fails(tmp_path: Path) -> None:
    _adr(tmp_path, "0014-empty.md", "Nothing sorted here.")
    result = _lint(tmp_path)
    assert result.exit_code == 1, result.output
    assert "docs/adr/0014-empty.md:5" in result.output


def test_adrs_before_0014_are_skipped(tmp_path: Path) -> None:
    _adr(tmp_path, "0013-old.md", holds=None)
    result = _lint(tmp_path, "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["skipped"] == ["docs/adr/0013-old.md"]
    assert report["checked"] == []


def test_json_report_lists_each_problem(tmp_path: Path) -> None:
    _adr(tmp_path, "0014-no-holds.md", holds=None)
    result = _lint(tmp_path, "--json")
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["ok"] is False
    assert report["problems"][0]["path"] == "docs/adr/0014-no-holds.md"
    assert report["problems"][0]["line"] == 1


def test_this_repos_adrs_pass() -> None:
    result = _lint(REPO_ROOT)
    assert result.exit_code == 0, result.output


def test_bare_lint_still_runs_the_vault_lint_not_the_adr_check() -> None:
    """The `adr` sub-verb must not swallow the vault lint's own help."""
    result = runner.invoke(app, ["lint", "--help"])
    assert result.exit_code == 0
    # CI renders Rich help with colour codes that split option names.
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "adr" in output
    assert "--check-only" in output


# --- the ADR template carries the sections the lint reads -----------------

ADR_FORMAT = REPO_ROOT / "lore-workflow" / "skills" / "domain-modeling" / "ADR-FORMAT.md"
ADR_0015 = REPO_ROOT / "docs" / "adr" / "0015-decision-records-state-their-strength.md"


def _strength_sections(text: str) -> list[str]:
    """The `Holds`, `Revisit if` and `Amendments` stubs ADR 0015 shows in its fence."""
    fence = text[text.index("```md\n## Holds") + len("```md\n") :]
    fence = fence[: fence.index("```")]
    return ["## " + part.strip() for part in fence.split("## ") if part.strip()]


def test_the_adr_template_carries_the_sections_adr_0015_shows() -> None:
    template = ADR_FORMAT.read_text(encoding="utf-8")
    sections = _strength_sections(ADR_0015.read_text(encoding="utf-8"))
    assert [s.splitlines()[0] for s in sections] == [
        "## Holds",
        "## Revisit if",
        "## Amendments",
    ]
    for section in sections:
        assert section in template


def test_the_adr_template_keeps_the_three_criteria_gate() -> None:
    template = ADR_FORMAT.read_text(encoding="utf-8")
    for criterion in ("Hard to reverse", "Surprising without context", "real trade-off"):
        assert criterion in template


def test_the_adr_template_says_text_outside_holds_is_background() -> None:
    template = ADR_FORMAT.read_text(encoding="utf-8")
    assert "outside `Holds` is background" in template
    assert "Mechanism detail stays out of `Decision`" in template
