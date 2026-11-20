"""Logbook entries and their sinks (PRD 0017, ADR 0017).

At a finish point the agent writes an entry in four labelled parts (``LABELS``,
issue #500). ``lore log add`` checks it,
stamps it, keeps it as a ``[logbook]`` line in the local ledger and delivers
it to every sink of the route in parallel. ``lore log flush`` retries the
sinks that did not confirm.

The ledger line records each sink's delivery, then the entry as JSON. A sink
a person gave up with ``lore log drop`` reads ``dropped`` (issue #665)::

    - [logbook] file=delivered pm-ccat=undelivered — {"key":"lore:acme/widget:#471",...}

Sinks:

- ``file`` writes one file per entry under ``docs/logbook/YYYY-MM/`` in the repo.
  A hidden ``<!-- <key> -->`` marker makes a repeat in the same month a no-op.
- ``command`` runs ``run`` through the shell with the entry text on stdin and
  ``LORE_ENTRY_*`` in the environment. A named ``secret`` arrives as
  ``LORE_SECRET`` and nowhere else. Exit code 0 means delivered.

Lab entries (:mod:`lore_workflow.lab`, PRD 0020) share :func:`deliver`, the
ledger lines (as ``[lab]``) and :func:`flush`; only their ``file`` sink differs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from lore_workflow.ledger import (
    LEDGER_FILENAME,
    LedgerEntry,
    LedgerParseError,
    default_ledger_path,
    find_git_dir,
    format_entry,
    parse_line,
    utc_now,
)

#: Seconds each sink may take before it counts as undelivered.
SINK_TIMEOUT_S = 30

#: The labelled parts of an entry, in order (issue #500, entries explain the reasoning).
LABELS = ("Why", "What we did", "Consequences", "Not done yet")

#: (finding, test). A test returns True when the finding holds for the text.
_RULES: tuple[tuple[str, object], ...] = (
    ("more than 300 words", lambda t: len(t.split()) > 300),
    ("no Markdown link", lambda t: re.search(r"\[[^\]]+\]\([^)\s]+\)", t) is None),
    (
        "a line starts with todo: or decided:",
        lambda t: any(
            ln.strip().lower().startswith(("todo:", "decided:")) for ln in t.splitlines()
        ),
    ),
    *(
        (
            f"no line starts with {label}:",
            # `**Why:**`, `**Why**:` and a plain `Why:` all count.
            lambda t, label=label: (
                re.search(rf"^\s*(?:\*\*)?{re.escape(label)}(?:\*\*)?:", t, re.M | re.I) is None
            ),
        )
        for label in LABELS
    ),
)


def broken_rule(text: str) -> str | None:
    """What is wrong with *text*, by the first content rule it breaks, or None."""
    return next((name for name, breaks in _RULES if breaks(text)), None)  # type: ignore[operator]


@dataclass(frozen=True)
class Entry:
    key: str
    at: str
    repo: str
    scope: str
    ref: str
    author: str
    text: str


#: The state of one sink: True delivered, False undelivered, None dropped (issue #665).
State = bool | None

_WORDS = {True: "delivered", False: "undelivered", None: "dropped"}

#: Writes *entry* to the ``file`` sink of a repo; True when delivered.
FileSink = Callable[[Entry, Path], bool]


# ---------------------------------------------------------------------------
# Ledger lines
# ---------------------------------------------------------------------------


def _ledger_entry(entry: Entry, status: dict[str, State], kind: str) -> LedgerEntry:
    return LedgerEntry(
        kind=kind,
        # ASCII JSON: a raw U+2028 would split the line under `splitlines()`.
        text=json.dumps(asdict(entry), ensure_ascii=True, separators=(",", ":")),
        outcome=" ".join(f"{n}={_WORDS[ok]}" for n, ok in status.items()),
    )


def _read_line(raw: str, kind: str = "logbook") -> tuple[Entry, dict[str, State]] | None:
    """The entry and per-sink delivery of a ``[<kind>]`` line, else None."""
    try:
        parsed = parse_line(raw)
        if parsed is None or parsed.kind != kind:
            return None
        entry = Entry(**json.loads(parsed.text))
    except (LedgerParseError, ValueError, TypeError):
        return None
    pairs = (token.partition("=") for token in (parsed.outcome or "").split())
    states = {w: ok for ok, w in _WORDS.items()}
    return entry, {name: states.get(state, False) for name, _, state in pairs}


def pending(ledger: Path, kind: str = "logbook") -> list[tuple[Entry, dict[str, State]]]:
    """Every ``[<kind>]`` entry of *ledger* and its archives with an undelivered sink.

    The archives are the ``lore-ledger.*.done.md`` files beside *ledger* (issue #659).
    Lines with one key merge into one entry. Per sink, delivered beats dropped
    and dropped beats undelivered, so no flush sends one entry to one sink twice.
    """
    merged: dict[str, tuple[Entry, dict[str, State]]] = {}
    for f in (ledger, *sorted(ledger.parent.glob("lore-ledger.*.done.md"))):
        if not f.is_file():
            continue
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = _read_line(raw, kind)
            if line is None:
                continue
            entry, status = line
            first, seen = merged.get(entry.key, (entry, {}))
            merged[entry.key] = (first, {n: _best(seen, status, n) for n in {**seen, **status}})
    return [line for line in merged.values() if False in line[1].values()]


def _best(a: dict[str, State], b: dict[str, State], name: str) -> State:
    """Delivered beats dropped, dropped beats undelivered."""
    both = (a.get(name, False), b.get(name, False))
    return True if True in both else None if None in both else False


def _write_status(
    ledger: Path, entry: Entry, status: dict[str, State], kind: str = "logbook"
) -> None:
    """Replace the ``[<kind>]`` line with *entry*'s key, or append one."""
    new = format_entry(_ledger_entry(entry, status, kind))
    lines = (
        ledger.read_text(encoding="utf-8").splitlines()
        if ledger.exists()
        else ["# Lore ledger", ""]
    )
    for i, raw in enumerate(lines):
        found = _read_line(raw, kind)
        if found is not None and found[0].key == entry.key:
            lines[i] = new
            break
    else:
        lines.append(new)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


def _sink_specs() -> dict[str, dict]:
    """The ``sinks`` section of the person's config (ADR 0016 reporting boundary)."""
    from lore_core.config import user_config_path
    from lore_core.config_schema import _read_raw

    sinks = _read_raw(user_config_path()).get("sinks")
    return (
        {n: s for n, s in sinks.items() if isinstance(s, dict)} if isinstance(sinks, dict) else {}
    )


def _logbook_dir(repo_root: Path) -> Path:
    return repo_root / "docs" / "logbook"


def _file_sink(entry: Entry, repo_root: Path) -> bool:
    # One file per entry: a union merge of one shared file mixed the lines that two
    # entries had in common, such as `**Not done yet:**` (review of PR #501, entries
    # explain the reasoning).
    month = entry.at[:7]
    month_dir = _logbook_dir(repo_root) / month
    marker = f"<!-- {entry.key} -->"
    old = _logbook_dir(repo_root) / f"{month}.md"
    files = [*month_dir.glob("*.md"), *([old] if old.is_file() else [])]
    if any(marker in f.read_text(encoding="utf-8") for f in files):
        return True
    # The marker, not the name, identifies the entry, so the cut stays under NAME_MAX.
    slug = re.sub(r"[^A-Za-z0-9]+", "-", entry.key.removeprefix("lore:")).strip("-")[:80]
    header = " · ".join(p for p in (f"**{entry.at[:10]}**", entry.author, entry.ref) if p)
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{entry.at.replace(':', '')}_{slug}.md"
    path.write_text(f"{marker}\n{header}\n\n{entry.text}\n", encoding="utf-8")
    return True


def month_text(repo_root: Path, month: str) -> str:
    """The entries of *month* (``YYYY-MM``), newest first, or "" when there are none.

    The monthly file that the sink wrote before PR #501 (entries explain the
    reasoning) follows the entry files, without its own heading.
    """
    root = _logbook_dir(repo_root)
    parts = [
        f.read_text(encoding="utf-8") for f in sorted((root / month).glob("*.md"), reverse=True)
    ]
    if (root / f"{month}.md").is_file():
        old = (root / f"{month}.md").read_text(encoding="utf-8")
        parts.append(old.partition("\n")[2].lstrip("\n") if old.startswith("# ") else old)
    return f"# Logbook {month}\n\n" + "\n".join(parts) if parts else ""


def _command_sink(entry: Entry, spec: dict, repo_root: Path) -> bool:
    from lore_core.secrets import SecretsError, secret_env

    try:
        env = secret_env(str(spec["secret"]) if spec.get("secret") else None)
    except SecretsError:
        return False
    env.update(
        LORE_ENTRY_KEY=entry.key,
        LORE_ENTRY_AT=entry.at,
        LORE_ENTRY_REPO=entry.repo,
        LORE_ENTRY_SCOPE=entry.scope,
        LORE_ENTRY_REF=entry.ref,
    )
    done = subprocess.run(
        str(spec["run"]),
        shell=True,  # the person's own config defines `run`, as a shell line
        input=entry.text,
        text=True,
        env=env,
        cwd=repo_root,
        capture_output=True,
        timeout=SINK_TIMEOUT_S,
        check=False,
    )
    return done.returncode == 0


def _deliver_one(
    entry: Entry, name: str, spec: dict | None, repo_root: Path, file_sink: FileSink
) -> bool:
    kind = "file" if spec is None and name == "file" else (spec or {}).get("kind")
    try:
        if kind == "file":
            return file_sink(entry, repo_root)
        if kind == "command" and spec and spec.get("run"):
            return _command_sink(entry, spec, repo_root)
    except Exception:  # noqa: BLE001 - a failed sink stays undelivered, never raises
        return False
    return False


def deliver(
    entry: Entry, names: list[str], repo_root: Path, file_sink: FileSink | None = None
) -> dict[str, bool]:
    """Deliver *entry* to every sink in *names* at once; ``{name: delivered}``.

    *file_sink* writes a ``file`` sink; the logbook's own by default.
    """
    if not names:
        return {}
    specs = _sink_specs()
    sink = file_sink or _file_sink
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {
            n: pool.submit(_deliver_one, entry, n, specs.get(n), repo_root, sink) for n in names
        }
    return {n: f.result() for n, f in futures.items()}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _git_user_name(cwd: Path) -> str:
    if os.environ.get("GIT_AUTHOR_NAME"):
        return os.environ["GIT_AUTHOR_NAME"]
    try:
        out = subprocess.run(
            ["git", "config", "user.name"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _main_checkout_name(repo_root: Path) -> str:
    """The folder name of the main checkout, also from a linked worktree."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],  # relative to cwd; git >= 2.5
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return repo_root.name
    if out.returncode != 0 or not out.stdout.strip():
        return repo_root.name
    common = (repo_root / out.stdout.strip()).resolve()
    # `<main>/.git` names the main checkout; a bare `gadget.git` names itself.
    return common.parent.name if common.name == ".git" else common.name.removesuffix(".git")


def _lock(ledger: Path, *, blocking: bool = True):
    """Hold the ledger's lock, so two flushes never deliver one entry twice."""
    from lore_core.lockfile import flocked

    return flocked(ledger.with_name(ledger.name + ".lock"), blocking=blocking)


def where(cwd: Path):
    """(route, ledger path, repo name) of *cwd*; ValueError outside a git repo."""
    from lore_core.git import current_repo
    from lore_core.route import resolve_route

    route = resolve_route(cwd)
    ledger = default_ledger_path(cwd)
    if ledger is None or route.repo_root is None:
        raise ValueError(f"{cwd} is not inside a git repository")
    return route, ledger, current_repo(route.repo_root) or _main_checkout_name(route.repo_root)


def add(text: str, ref: str, cwd: Path) -> tuple[Entry, dict[str, bool]]:
    """Stamp *text*, keep it in the ledger of *cwd*, deliver it to the route's sinks.

    Raises ValueError outside a git repo. The caller checks the content rules.
    """
    route, ledger, repo = where(cwd)
    # `owner/repo#n` in its own repo keys as `#n`, so both spellings dedupe.
    key_ref = ref.removeprefix(repo) if ref.startswith(f"{repo}#") else ref
    entry = Entry(
        key=f"lore:{repo}:{key_ref}",
        at=utc_now(),
        repo=repo,
        scope=route.scope or "",
        ref=ref,
        author=_git_user_name(route.repo_root),
        text=text.strip(),
    )
    with _lock(ledger):
        _write_status(ledger, entry, dict.fromkeys(route.sinks, False))
        status = deliver(entry, list(route.sinks), route.repo_root)
        _write_status(ledger, entry, status)
    return entry, status


def _checkouts(cwd: Path) -> list[tuple[Path, Path]]:
    """(git dir, worktree root) of every checkout of the repo of *cwd* (issue #658).

    The checkout of *cwd* comes first, then the main checkout, then each linked
    worktree. A checkout whose folder Lore cannot find (a bare repo, a
    ``--separate-git-dir`` repo, a submodule, a deleted worktree) falls back to
    the root of *cwd*, so its entries still deliver.
    """
    from lore_core.route import resolve_route

    git_dir = find_git_dir(cwd)
    root = resolve_route(cwd).repo_root
    if git_dir is None or root is None:
        return []
    found = {git_dir.resolve(): root}
    common_file = git_dir / "commondir"
    if common_file.is_file():
        common = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
        # A `--separate-git-dir` or submodule common dir does not name its checkout.
        found.setdefault(common, common.parent if common.name == ".git" else root)
    else:
        common = git_dir.resolve()
    for wt in sorted((common / "worktrees").glob("*/")):
        try:
            # `gitdir` names `<worktree>/.git`, relative to its own folder or absolute.
            dotgit = wt / (wt / "gitdir").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        found.setdefault(wt.resolve(), dotgit.parent if dotgit.parent.is_dir() else root)
    return list(found.items())


def flush(cwd: Path) -> list[tuple[Entry, dict[str, State]]] | None:
    """Retry every undelivered sink in the ledgers of every checkout of the repo of *cwd*.

    Retries the ``[logbook]`` lines, then the ``[lab]`` lines, of each checkout,
    in the ledger and its archives; a ``file`` sink writes into that checkout.
    Skips a ledger whose lock another flush or `log add` holds.

    Returns the new states, or None when every ledger was locked.
    """
    from lore_workflow import lab

    checkouts = _checkouts(cwd)
    results = []
    locked = 0
    for git_dir, repo_root in checkouts:
        ledger = git_dir / LEDGER_FILENAME
        with _lock(ledger, blocking=False) as held:
            if not held:
                locked += 1
                continue
            for kind, file_sink in (("logbook", _file_sink), ("lab", lab.file_sink)):
                for entry, status in pending(ledger, kind):
                    retry = [name for name, ok in status.items() if ok is False]
                    status = {**status, **deliver(entry, retry, repo_root, file_sink)}
                    _write_status(ledger, entry, status, kind)
                    results.append((entry, status))
    return None if checkouts and locked == len(checkouts) else results


def undelivered_count(cwd: Path, kind: str = "logbook") -> int:
    """How many ``[<kind>]`` entries in the ledgers of every checkout wait for a sink."""
    return sum(len(pending(g / LEDGER_FILENAME, kind)) for g, _ in _checkouts(cwd))


def drop(key: str, cwd: Path, sink: str | None = None) -> list[tuple[Entry, dict[str, State]]]:
    """Give up *sink* of the entry *key*, or each undelivered sink; return the new states.

    Writes ``<sink>=dropped`` to the ledger of each checkout that holds the
    entry (issue #665). Raises ValueError when no ledger holds an undelivered
    *sink* of *key*.
    """
    from lore_workflow.ledger import DELIVERY_KINDS

    results = []
    for git_dir, _ in _checkouts(cwd):
        ledger = git_dir / LEDGER_FILENAME
        with _lock(ledger):
            for kind in DELIVERY_KINDS:
                for entry, status in pending(ledger, kind):
                    names = [n for n, ok in status.items() if ok is False and sink in (None, n)]
                    if entry.key != key or not names:
                        continue
                    status = {**status, **dict.fromkeys(names, None)}
                    _write_status(ledger, entry, status, kind)
                    results.append((entry, status))
    if not results:
        where = f" sink {sink!r} of" if sink else ""
        raise ValueError(f"no undelivered entry with{where} key {key!r}")
    return results
