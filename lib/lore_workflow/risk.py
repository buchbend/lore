"""Deterministic risk level for a diff (PRD 0015 § Build).

``lore workflow risk`` prints ``low`` or ``high`` with the reasons. The build
skill picks the review depth from the level; an agent may raise it and does
not lower it. The level is ``high`` when one of these holds:

- more than ``max_lines`` changed lines (added plus deleted), or more than
  ``max_files`` changed files;
- a changed Python file sits in the top ``fanin_top_fraction`` of import
  fan-in (see :func:`python_fan_in`);
- the diff touches a migration (``*/migrations/*``, ``alembic/versions/*``),
  a schema file (``*.sql``, ``*schema*.json|yaml|yml``, ``schema.py``), a
  config schema (``config_schema.py``, ``*config*schema*``), or adds a line
  with a route decorator (``@app.get(``, ``@router.post(``, ``@bp.route(``);
- the diff touches a path that matches a ``sensitive_paths`` glob.

Thresholds and globs come from ``workflow.risk`` in the root config
(:class:`lore_core.root_config.RiskConfig`).

Fan-in
======

The codemap ranks symbols, not files, and keeps no import graph. Fan-in is
therefore computed here from the same file list the codemap uses
(:func:`lore_core.codemap.discover`): the number of distinct Python files
that import a module. Each ``.py`` file is known by every dotted suffix of
its path (``lib/lore_core/git.py`` is ``lib.lore_core.git`` and
``lore_core.git``). A one-part name counts only for a file at the root or
directly under ``src/`` or ``lib/``, so ``import types`` never lands on
``lore_core/types.py``. A name that fits two files counts for neither. The
top fraction is ``ceil(fraction * N)`` files over all N Python files, ties
included; a file with fan-in 0 never counts.

Standard library only, plus the codemap discovery pass.
"""

from __future__ import annotations

import ast
import math
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


class RiskInputError(RuntimeError):
    """Raised when the diff cannot be read (gh or git failed)."""


@dataclass
class FileDiff:
    """One file of a unified diff."""

    path: str
    status: str = "M"  # A added, M modified, D deleted, R renamed
    added: int = 0
    deleted: int = 0
    added_lines: list[str] = field(default_factory=list)


@dataclass
class RiskResult:
    level: str
    reasons: list[str]
    notes: list[str]
    files: int
    lines: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "reasons": self.reasons,
            "notes": self.notes,
            "files": self.files,
            "lines": self.lines,
        }


# ---------------------------------------------------------------------------
# Diff input
# ---------------------------------------------------------------------------


def _strip_prefix(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse ``git diff`` / ``gh pr diff`` output into per-file counts."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            _, _, b_side = line.rpartition(" b/")
            current = FileDiff(path=b_side)
            files.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith("+"):
                current.added += 1
                current.added_lines.append(line[1:])
            elif line.startswith("-"):
                current.deleted += 1
            continue
        if line.startswith("new file mode"):
            current.status = "A"
        elif line.startswith("deleted file mode"):
            current.status = "D"
        elif line.startswith("rename to "):
            current.status = "R"
            current.path = line[len("rename to ") :]
        elif line.startswith("+++ ") and not line.endswith("/dev/null"):
            current.path = _strip_prefix(line[4:].strip())
    return files


def _run(cmd: list[str], cwd: Path | None, label: str) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise RiskInputError(f"{label} failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise RiskInputError(f"{label} failed: {detail}")
    return proc.stdout


def diff_for_pr(pr: int, *, repo: str | None = None, cwd: Path | None = None) -> str:
    """Unified diff of a pull request, via ``gh pr diff``."""
    cmd = ["gh", "pr", "diff", str(pr)]
    if repo:
        cmd += ["--repo", repo]
    return _run(cmd, cwd, f"gh pr diff {pr}")


def diff_for_range(rev_range: str, *, cwd: Path | None = None) -> str:
    """Unified diff of a revision range such as ``main..HEAD``, via ``git diff``."""
    cmd = ["git", "diff", "--no-color", "--no-ext-diff", "-M", rev_range]
    return _run(cmd, cwd, f"git diff {rev_range}")


# ---------------------------------------------------------------------------
# Fan-in
# ---------------------------------------------------------------------------

_SRC_ROOTS = frozenset({"src", "lib"})


def _module_parts(relpath: str) -> list[str]:
    parts = relpath[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _module_index(py_files: Iterable[str]) -> dict[str, str]:
    """Map each unambiguous dotted name to its file (see module docstring)."""
    claims: dict[str, set[str]] = {}
    for rel in py_files:
        parts = _module_parts(rel)
        for start in range(len(parts)):
            suffix = parts[start:]
            if len(suffix) == 1 and not (
                len(parts) == 1 or (len(parts) == 2 and parts[0] in _SRC_ROOTS)
            ):
                continue
            claims.setdefault(".".join(suffix), set()).add(rel)
    return {name: next(iter(owners)) for name, owners in claims.items() if len(owners) == 1}


def _import_targets(rel: str, tree: ast.AST, index: dict[str, str]) -> set[str]:
    """Files that *rel* imports, resolved through *index*."""
    package = _module_parts(rel) if rel.endswith("__init__.py") else _module_parts(rel)[:-1]
    found: set[str] = set()

    def resolve(*candidates: str) -> None:
        for name in candidates:
            if name in index:
                found.add(index[name])
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolve(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                if keep < 0:
                    continue
                base = ".".join(package[:keep] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            for alias in node.names:
                full = f"{base}.{alias.name}" if base else alias.name
                resolve(full, base)
    found.discard(rel)
    return found


def python_fan_in(root: Path, files: list[str] | None = None) -> dict[str, int]:
    """Return ``{relpath: number of Python files that import it}``.

    *files* defaults to the codemap discovery pass over *root*.
    """
    if files is None:
        from lore_core.codemap import discover

        files = discover(root).files
    py_files = sorted(f for f in files if f.endswith(".py"))
    index = _module_index(py_files)
    fan_in = dict.fromkeys(py_files, 0)
    for rel in py_files:
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        for target in _import_targets(rel, tree, index):
            fan_in[target] += 1
    return fan_in


def top_fan_in(fan_in: dict[str, int], fraction: float) -> set[str]:
    """Files in the top *fraction* of fan-in, ties included, fan-in above 0."""
    if not fan_in:
        return set()
    ranked = sorted(fan_in.values(), reverse=True)
    k = max(1, math.ceil(fraction * len(ranked)))
    threshold = max(ranked[k - 1], 1)
    return {path for path, n in fan_in.items() if n >= threshold}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

_MIGRATION_GLOBS = ("*/migrations/*", "migrations/*", "alembic/versions/*", "*/alembic/versions/*")
_SCHEMA_GLOBS = (
    "*.sql",
    "*schema*.json",
    "*schema*.yaml",
    "*schema*.yml",
    "schema.py",
    "*/schema.py",
)
_CONFIG_SCHEMA_GLOBS = ("config_schema.py", "*/config_schema.py", "*config*schema*")
_ROUTE_RE = re.compile(
    r"^\s*@\w+(\.\w+)*\.(get|post|put|patch|delete|head|options|route|api_route|websocket)\("
)


def _matches(path: str, patterns: Iterable[str]) -> str | None:
    """First glob in *patterns* that *path* matches; ``**/`` also matches at root."""
    for pattern in patterns:
        if fnmatch(path, pattern):
            return pattern
        if pattern.startswith("**/") and fnmatch(path, pattern[3:]):
            return pattern
    return None


def assess(diffs: list[FileDiff], cfg: Any, *, root: Path | None = None) -> RiskResult:
    """Grade *diffs* against *cfg* (a :class:`RiskConfig`).

    *root* is the repo root for the fan-in signal; None skips that signal.
    """
    reasons: list[str] = []
    notes: list[str] = []
    lines = sum(d.added + d.deleted for d in diffs)
    if lines > cfg.max_lines:
        reasons.append(f"{lines} changed lines (> {cfg.max_lines})")
    if len(diffs) > cfg.max_files:
        reasons.append(f"{len(diffs)} changed files (> {cfg.max_files})")

    for d in diffs:
        if _matches(d.path, _MIGRATION_GLOBS):
            reasons.append(f"migration: {d.path}")
        elif _matches(d.path, _CONFIG_SCHEMA_GLOBS):
            reasons.append(f"config schema: {d.path}")
        elif _matches(d.path, _SCHEMA_GLOBS):
            reasons.append(f"schema file: {d.path}")
        route = next((ln.strip() for ln in d.added_lines if _ROUTE_RE.match(ln)), None)
        if route:
            reasons.append(f"public API route added in {d.path}: {route}")
        pattern = _matches(d.path, cfg.sensitive_paths)
        if pattern:
            reasons.append(f"sensitive path: {d.path} (matches {pattern})")

    changed_py = [d.path for d in diffs if d.path.endswith(".py") and d.status != "A"]
    if root is None:
        notes.append("fan-in signal skipped: no repo root")
    elif changed_py:
        fan_in = python_fan_in(root)
        if not fan_in:
            notes.append("fan-in signal skipped: the codemap finds no Python files")
        else:
            top = top_fan_in(fan_in, cfg.fanin_top_fraction)
            for path in changed_py:
                if path in top:
                    reasons.append(
                        f"high fan-in: {path} is imported by {fan_in[path]} files "
                        f"(top {cfg.fanin_top_fraction:.0%})"
                    )

    level = "high" if reasons else "low"
    return RiskResult(level=level, reasons=reasons, notes=notes, files=len(diffs), lines=lines)
