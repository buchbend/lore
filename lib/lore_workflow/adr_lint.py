"""`lore lint adr` — check that decision records state their strength (ADR 0015).

Each ADR numbered 0014 or higher carries a `## Holds` section. The section
sorts the decision into `Invariant`, `Default` and `Incidental` lines. An
invariant line names the test that enforces it, as `test: <path>::<name>`.
This module checks the section exists and each named test exists. ADRs 0001
to 0013 predate the section and are skipped.

Stdlib only, no GitHub I/O: the check reads files under the repo root.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

#: The first ADR number the section is required for (ADR 0015).
FIRST_CHECKED = 14

_NUMBER_RE = re.compile(r"^(\d{4})-.*\.md$")

# `- **Invariant** (test: ...)`, `- **Default:** ...`, `- **Incidental:** ...`
_STRENGTH_RE = re.compile(r"^\s*[-*]\s+\*\*(Invariant|Default|Incidental)\b")

# `test: tests/x.py::test_y`, with or without backticks around the reference.
_TEST_REF_RE = re.compile(r"test:\s*`?([^\s`)]+::[^\s`)]+)`?")

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass
class AdrLintReport:
    checked: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _holds_line(lines: list[str]) -> int | None:
    """1-based line number of the `## Holds` heading, or None."""
    for number, line in enumerate(lines, 1):
        if line.strip().lower() == "## holds":
            return number
    return None


def _holds_body(lines: list[str], start: int) -> list[tuple[int, str]]:
    """The numbered lines after the `## Holds` heading, up to the next heading."""
    body = []
    for number in range(start + 1, len(lines) + 1):
        line = lines[number - 1]
        if line.startswith("#"):
            break
        body.append((number, line))
    return body


def _test_exists(repo_root: Path, file_part: str, names: list[str]) -> str | None:
    """None when ``file_part::names`` names a real test, else the reason it does not."""
    test_file = repo_root / file_part
    if not test_file.is_file():
        return f"test file `{file_part}` does not exist"
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return f"test file `{file_part}` does not parse: {exc.msg}"
    scope: list[ast.stmt] = tree.body
    for name in names:
        found = next(
            (node for node in scope if isinstance(node, _DEFS) and node.name == name),
            None,
        )
        if found is None:
            return f"test `{'::'.join(names)}` not found in `{file_part}`"
        scope = found.body if isinstance(found, ast.ClassDef) else []
    return None


def _check_invariant(repo_root: Path, rel: str, number: int, line: str) -> Problem | None:
    match = _TEST_REF_RE.search(line)
    if match is None:
        return Problem(
            rel,
            number,
            "invariant names no test; write `(test: <path>::<name>)` or make it a Default",
        )
    file_part, *names = match.group(1).split("::")
    # A parametrized id (`test_x[case]`) names the function `test_x`.
    names = [name.split("[", 1)[0] for name in names]
    if not names:
        return Problem(rel, number, f"test reference `{match.group(1)}` names no test")
    reason = _test_exists(repo_root, file_part, names)
    return Problem(rel, number, reason) if reason else None


def _lint_one(repo_root: Path, path: Path, rel: str) -> list[Problem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = _holds_line(lines)
    if start is None:
        return [Problem(rel, 1, "missing `## Holds` section (ADR 0015)")]
    problems = []
    strengths = 0
    for number, line in _holds_body(lines, start):
        kind = _STRENGTH_RE.match(line)
        if kind is None:
            continue
        strengths += 1
        if kind.group(1) == "Invariant":
            problem = _check_invariant(repo_root, rel, number, line)
            if problem is not None:
                problems.append(problem)
    if strengths == 0:
        problems.append(
            Problem(rel, start, "`## Holds` has no Invariant, Default or Incidental line")
        )
    return problems


def lint_adrs(repo_root: Path) -> AdrLintReport:
    """Lint every ADR under ``repo_root/docs/adr``; see the module docstring."""
    report = AdrLintReport()
    adr_dir = repo_root / "docs" / "adr"
    if not adr_dir.is_dir():
        return report
    for path in sorted(adr_dir.glob("*.md")):
        match = _NUMBER_RE.match(path.name)
        if not match:
            continue
        rel = path.relative_to(repo_root).as_posix()
        if int(match.group(1)) < FIRST_CHECKED:
            report.skipped.append(rel)
            continue
        report.checked.append(rel)
        report.problems.extend(_lint_one(repo_root, path, rel))
    return report
