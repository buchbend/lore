"""Deterministic code-map generator: a local, gitignored ``CODEMAP.md``.

A navigation substrate for context-hungry work so a session reads a ready
index instead of re-exploring the repo. One gitignore-aware discovery pass
(``git ls-files``, with a plain-walk fallback for non-git trees) feeds two
layers written into a single ``CODEMAP.md``:

1. **Repository inventory** — per-directory file counts, total sizes, and the
   dominant extensions. Bounded (``MAX_DIR_ROWS``) so a huge tree renders a
   digestible top slice, not thousands of rows.
2. **Ranked Python symbols** — functions/classes/methods from the discovered
   ``.py`` files, ranked by how widely each name is referenced across the tree
   (a cheap deterministic proxy for graph centrality). Parser is stdlib
   ``ast`` only; multi-language symbols (tree-sitter) are a separate feature
   and plug in behind :func:`extract_symbols` without disturbing discovery,
   ranking, or IO.

**Fingerprint + no-op fast path.** The map embeds a fingerprint. For a git
tree it is a hash over ``git ls-files -s`` blob SHAs, so any tracked-file
change (once staged) flips it; for a non-git tree it hashes file contents.
A re-run whose fingerprint matches the existing map exits "up to date" without
rewriting the file — cheap enough to fire from a background SessionStart hook.

ponytail: git fingerprint rides the *index* blob SHAs, so an unstaged
working-tree edit does not trip regeneration until ``git add``. That is the
documented ceiling; hashing the working tree with ``git hash-object`` would
close it at the cost of an exec per file — add that only if stale-until-staged
maps become a real problem.

Output is gitignored: lore's SessionStart hook keeps it fresh, so it is a local
navigation cache, never a committed artifact.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# The generated map lives at the target repo root under this name (gitignored).
MAP_FILENAME = "CODEMAP.md"

# Directories the non-git walk fallback never descends into: VCS internals,
# caches, virtualenvs, vendored JS deps, and ``.claude`` (whose worktrees hold
# full checkouts that must not be mapped many times over). The git path needs
# none of this — ``git ls-files`` already respects ``.gitignore``.
IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "node_modules",
        ".claude",
    }
)

# Cap on inventory directory rows so a huge repo renders a bounded top slice.
MAX_DIR_ROWS = 60
# Top-extension columns shown per directory and repo-wide.
_TOP_EXTS = 3

_TEMP_PREFIX = ".codemap."

# Fingerprint embedded as an HTML comment: invisible in rendered Markdown,
# trivially machine-readable on the next run.
_FINGERPRINT_RE = re.compile(r"<!--\s*fingerprint:\s*([0-9a-f]+)\s*-->")

# One rendered symbol row — parsed back to compute the added/removed delta.
_ROW_RE = re.compile(
    r"^\|\s*\d+\s*\|\s*`([^`]+)`\s*\|\s*(\w+)\s*\|\s*`([^`]+):(\d+)`\s*\|\s*(\d+)\s*\|"
)


@dataclass(frozen=True)
class Symbol:
    """One symbol definition with its cross-repo reference count."""

    name: str
    qualname: str
    kind: str  # "function" | "class" | "method"
    relpath: str
    lineno: int
    refs: int = 0

    @property
    def identity(self) -> str:
        """Stable identity for delta comparison: file + qualified name.

        Excludes the line number so moving a symbol within its file is not
        reported as a remove + add.
        """
        return f"{self.relpath}::{self.qualname}"


@dataclass(frozen=True)
class DirStat:
    """Inventory row for one directory: file count, total bytes, top exts."""

    path: str
    file_count: int
    total_bytes: int
    top_exts: tuple[tuple[str, int], ...]


@dataclass
class Inventory:
    """The all-files repository inventory layer."""

    total_files: int
    total_bytes: int
    ext_counts: list[tuple[str, int]]  # repo-wide, most-common first
    dirs: list[DirStat]  # bounded to MAX_DIR_ROWS, ranked by file_count


@dataclass
class Discovery:
    """One discovery pass: the mapped files, a fingerprint, and its source."""

    files: list[str]  # relpaths, sorted, posix
    fingerprint: str
    source: str  # "git" | "walk"


@dataclass
class CodeMap:
    """Parsed, ranked map of a tree plus its fingerprint and inventory."""

    root: Path
    fingerprint: str
    source: str
    inventory: Inventory
    symbols: list[Symbol] = field(default_factory=list)
    symbol_file_counts: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class GenerateResult:
    """Outcome of a :func:`generate` call.

    ``status`` is ``created`` (no map existed), ``updated`` (fingerprint
    changed), or ``up-to-date`` (no-op fast path). ``added``/``removed`` carry
    the symbol-identity delta versus the previous map.
    """

    status: str
    wrote: bool
    fingerprint: str
    path: Path
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Discovery — one gitignore-aware pass, git first with a plain-walk fallback
# ---------------------------------------------------------------------------


def _git_ls_files(root: Path) -> list[tuple[str, str]] | None:
    """Return ``[(relpath, blob_sha)]`` for tracked files, or None if not git.

    Uses ``git ls-files -s -z`` so paths are NUL-terminated and never quoted.
    Each record is ``<mode> <sha> <stage>\\t<path>``. Returns None on any git
    failure (not a repo, git absent) so the caller falls back to a plain walk.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-s", "-z"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    entries: list[tuple[str, str]] = []
    for record in proc.stdout.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if len(parts) < 2 or not path:
            continue
        entries.append((path, parts[1]))
    return entries


def _walk_files(root: Path) -> list[str]:
    """Fallback discovery: walk *root*, pruning IGNORE_DIRS, return relpaths."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for filename in filenames:
            # Never map our own output or its atomic-write temp files, else a
            # non-git walk churns its fingerprint every run.
            if filename == MAP_FILENAME or filename.startswith(_TEMP_PREFIX):
                continue
            rel = (Path(dirpath) / filename).relative_to(root).as_posix()
            found.append(rel)
    return sorted(found)


def _hash_contents(root: Path, relpaths: list[str]) -> str:
    """Merkle-style content hash over *relpaths* (path + content SHA)."""
    digest = hashlib.sha256()
    for rel in sorted(relpaths):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            body = (root / rel).read_bytes()
        except OSError:
            body = b""
        digest.update(hashlib.sha256(body).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _hash_blob_shas(entries: list[tuple[str, str]]) -> str:
    """Fingerprint over git blob SHAs (path + index SHA)."""
    digest = hashlib.sha256()
    for path, sha in sorted(entries):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def discover(root: Path) -> Discovery:
    """Enumerate mapped files once, gitignore-aware, with a fingerprint.

    Prefers ``git ls-files`` (respects ``.gitignore``, fingerprint from blob
    SHAs); falls back to a pruned filesystem walk with a content-hash
    fingerprint for non-git trees.
    """
    root = Path(root)
    entries = _git_ls_files(root)
    if entries is not None:
        files = sorted(path for path, _ in entries)
        return Discovery(files, _hash_blob_shas(entries), "git")
    files = _walk_files(root)
    return Discovery(files, _hash_contents(root, files), "walk")


# ---------------------------------------------------------------------------
# Python symbol layer (ported from ccat-agent-workflow scripts/code_map.py)
# ---------------------------------------------------------------------------


def extract_symbols(relpath: str, source: str) -> list[Symbol]:
    """Extract definition symbols from one Python *source* via the stdlib AST.

    Returns functions, classes, and methods with qualified names and 1-based
    line numbers; ``refs`` is filled in later during ranking. A file that does
    not parse contributes no symbols rather than raising, so one malformed file
    never breaks the map.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    symbols: list[Symbol] = []

    def walk(node: ast.AST, prefix: str, parent_is_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if parent_is_class else "function"
                qualname = f"{prefix}{child.name}"
                symbols.append(Symbol(child.name, qualname, kind, relpath, child.lineno))
                walk(child, f"{qualname}.", parent_is_class=False)
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}{child.name}"
                symbols.append(Symbol(child.name, qualname, "class", relpath, child.lineno))
                walk(child, f"{qualname}.", parent_is_class=True)

    walk(tree, "", parent_is_class=False)
    return symbols


def _count_references(source: str, counter: Counter[str]) -> None:
    """Add *source*'s Load-context identifier uses to *counter*.

    ``Name`` Load nodes count call sites and value uses; ``Attribute`` Load
    nodes count ``obj.method`` accesses. Store contexts are excluded so the
    count reflects usage, not definition.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            counter[node.id] += 1
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            counter[node.attr] += 1


# ---------------------------------------------------------------------------
# Inventory layer
# ---------------------------------------------------------------------------


def _ext_of(relpath: str) -> str:
    """File extension incl. dot (``.py``), or ``(none)`` when there is none."""
    suffix = Path(relpath).suffix
    return suffix if suffix else "(none)"


def build_inventory(root: Path, relpaths: list[str]) -> Inventory:
    """Group discovered files by directory into a bounded inventory."""
    root = Path(root)
    dir_counts: Counter[str] = Counter()
    dir_bytes: Counter[str] = Counter()
    dir_exts: dict[str, Counter[str]] = {}
    ext_counts: Counter[str] = Counter()
    total_bytes = 0

    for rel in relpaths:
        parent = Path(rel).parent.as_posix()
        directory = "(root)" if parent == "." else parent
        try:
            size = (root / rel).stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        dir_counts[directory] += 1
        dir_bytes[directory] += size
        ext = _ext_of(rel)
        ext_counts[ext] += 1
        dir_exts.setdefault(directory, Counter())[ext] += 1

    ranked_dirs = sorted(dir_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_DIR_ROWS]
    dirs = [
        DirStat(
            path=directory,
            file_count=count,
            total_bytes=dir_bytes[directory],
            top_exts=tuple(dir_exts[directory].most_common(_TOP_EXTS)),
        )
        for directory, count in ranked_dirs
    ]
    return Inventory(
        total_files=len(relpaths),
        total_bytes=total_bytes,
        ext_counts=ext_counts.most_common(),
        dirs=dirs,
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_code_map(root: Path) -> CodeMap:
    """Discover *root* once, then build the inventory and ranked symbol layers."""
    root = Path(root)
    discovery = discover(root)

    sources: dict[str, str] = {}
    for rel in discovery.files:
        if rel.endswith(".py"):
            try:
                sources[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    references: Counter[str] = Counter()
    for source in sources.values():
        _count_references(source, references)

    symbols: list[Symbol] = []
    file_counts: Counter[str] = Counter()
    for relpath in sorted(sources):
        for symbol in extract_symbols(relpath, sources[relpath]):
            symbols.append(
                Symbol(
                    symbol.name,
                    symbol.qualname,
                    symbol.kind,
                    symbol.relpath,
                    symbol.lineno,
                    refs=references.get(symbol.name, 0),
                )
            )
            file_counts[relpath] += 1

    symbols.sort(key=lambda s: (-s.refs, s.relpath, s.lineno, s.qualname))
    ranked_files = sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    return CodeMap(
        root=root,
        fingerprint=discovery.fingerprint,
        source=discovery.source,
        inventory=build_inventory(root, discovery.files),
        symbols=symbols,
        symbol_file_counts=ranked_files,
    )


def read_fingerprint(text: str) -> str | None:
    """Return the fingerprint embedded in a rendered map, or None if absent."""
    match = _FINGERPRINT_RE.search(text)
    return match.group(1) if match else None


def read_symbol_ids(text: str) -> set[str]:
    """Reconstruct the symbol-identity set from a previously rendered map."""
    ids: set[str] = set()
    for line in text.splitlines():
        match = _ROW_RE.match(line)
        if match:
            qualname, _kind, relpath, _lineno, _refs = match.groups()
            ids.add(f"{relpath}::{qualname}")
    return ids


def _fmt_bytes(n: int) -> str:
    """Human-ish byte size (deterministic, no locale)."""
    size = float(n)
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{int(size)}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def _exts_str(top_exts: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{ext} ({count})" for ext, count in top_exts)


def render(code_map: CodeMap) -> str:
    """Render *code_map* to the CODEMAP.md Markdown text."""
    inv = code_map.inventory
    lines: list[str] = [
        "# Code Map",
        "",
        "<!-- Generated by `lore codemap` — local, gitignored; do not edit by hand. -->",
        f"<!-- fingerprint: {code_map.fingerprint} -->",
        f"<!-- discovery: {code_map.source} -->",
        "",
        "Deterministic index of this repository, most-referenced Python symbols",
        "first. Navigation aid, not a context pack: locate a symbol here, then open",
        "the cited file and line. Regenerated on tracked-file changes; an unchanged",
        "tree is a no-op.",
        "",
        (
            f"Files: {inv.total_files} · Size: {_fmt_bytes(inv.total_bytes)} · "
            f"Symbols: {len(code_map.symbols)} · Discovery: {code_map.source}"
        ),
        "",
        "## Repository inventory",
        "",
        "| Directory | Files | Size | Top extensions |",
        "| :--- | ---: | ---: | :--- |",
    ]
    for d in inv.dirs:
        lines.append(
            f"| `{d.path}` | {d.file_count} | {_fmt_bytes(d.total_bytes)} "
            f"| {_exts_str(d.top_exts)} |"
        )

    lines += [
        "",
        "## Ranked symbols",
        "",
        "| Rank | Symbol | Kind | Location | Refs |",
        "| ---: | :--- | :--- | :--- | ---: |",
    ]
    for rank, symbol in enumerate(code_map.symbols, start=1):
        lines.append(
            f"| {rank} | `{symbol.qualname}` | {symbol.kind} "
            f"| `{symbol.relpath}:{symbol.lineno}` | {symbol.refs} |"
        )

    lines += ["", "## Python files", "", "| File | Symbols |", "| :--- | ---: |"]
    for relpath, count in code_map.symbol_file_counts:
        lines.append(f"| `{relpath}` | {count} |")
    lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically: temp file in the same dir, then replace.

    Same-directory temp keeps ``os.replace`` on one filesystem (atomic), which
    makes concurrent SessionStart hooks safe — a reader always sees either the
    old or the new complete file, never a torn one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def generate(root: Path, *, out_path: Path | None = None, quiet: bool = False) -> GenerateResult:
    """Build the map for *root* and write it if the fingerprint changed.

    No-op fast path: if the existing map's embedded fingerprint already matches
    the freshly computed one, return ``up-to-date`` without touching the file.
    In ``quiet`` mode any error is swallowed and reported as an ``up-to-date``
    no-op so a background hook never disrupts a session.
    """
    root = Path(root)
    map_path = Path(out_path) if out_path is not None else root / MAP_FILENAME

    try:
        code_map = build_code_map(root)
    except Exception:
        if quiet:
            return GenerateResult("up-to-date", False, "", map_path)
        raise

    new_text = render(code_map)
    new_fingerprint = code_map.fingerprint

    old_text = ""
    if map_path.exists():
        old_text = map_path.read_text(encoding="utf-8")
        if read_fingerprint(old_text) == new_fingerprint:
            return GenerateResult("up-to-date", False, new_fingerprint, map_path)

    status = "updated" if old_text else "created"
    old_ids = read_symbol_ids(old_text)
    new_ids = {symbol.identity for symbol in code_map.symbols}
    added = tuple(sorted(new_ids - old_ids))
    removed = tuple(sorted(old_ids - new_ids))

    _atomic_write(map_path, new_text)
    return GenerateResult(status, True, new_fingerprint, map_path, added, removed)


def main(argv: list[str] | None = None) -> int:
    """CLI: refresh the code map, printing a status line unless ``--quiet``."""
    parser = argparse.ArgumentParser(
        description="Generate a deterministic, gitignored CODEMAP.md for a repository."
    )
    parser.add_argument("root", nargs="?", help="Repository root to map (default: cwd).")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Silent on no-op; swallow errors and exit 0 (for the SessionStart hook).",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path.cwd()
    result = generate(root, quiet=args.quiet)

    if args.quiet:
        return 0
    if result.status == "up-to-date":
        print(f"{MAP_FILENAME} up to date")
    elif result.status == "created":
        print(f"{MAP_FILENAME} created: {len(result.added)} symbols indexed")
    else:
        print(f"{MAP_FILENAME} updated: +{len(result.added)} / -{len(result.removed)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
