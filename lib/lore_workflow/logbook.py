"""Logbook entries and their sinks (PRD 0017, ADR 0017).

At a finish point the agent writes one paragraph. ``lore log add`` checks it,
stamps it, keeps it as a ``[logbook]`` line in the local ledger and delivers
it to every sink of the route in parallel. ``lore log flush`` retries the
sinks that did not confirm.

The ledger line records each sink's delivery, then the entry as JSON::

    - [logbook] file=delivered pm-ccat=undelivered — {"key":"lore:acme/widget:#471",...}

Sinks:

- ``file`` writes ``docs/logbook/YYYY-MM.md`` in the repo, newest entry first.
  A hidden ``<!-- <key> -->`` marker makes a repeat a no-op.
- ``command`` runs ``run`` through the shell with the entry text on stdin and
  ``LORE_ENTRY_*`` in the environment. A named ``secret`` arrives as
  ``LORE_SECRET`` and nowhere else. Exit code 0 means delivered.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from lore_workflow.ledger import (
    LedgerEntry,
    LedgerParseError,
    default_ledger_path,
    format_entry,
    parse_line,
    utc_now,
)

#: Seconds each sink may take before it counts as undelivered.
SINK_TIMEOUT_S = 30

GITATTRIBUTES_LINE = "docs/logbook/*.md merge=union"

#: (rule name, test). A test returns True when the text breaks the rule.
_RULES: tuple[tuple[str, object], ...] = (
    ("one paragraph", lambda t: re.search(r"\n\s*\n", t.strip()) is not None),
    ("at most 80 words", lambda t: len(t.split()) > 80),
    ("at least one Markdown link", lambda t: re.search(r"\[[^\]]+\]\([^)\s]+\)", t) is None),
    (
        "no line starts with todo: or decided:",
        lambda t: any(
            ln.strip().lower().startswith(("todo:", "decided:")) for ln in t.splitlines()
        ),
    ),
)


def broken_rule(text: str) -> str | None:
    """The name of the first content rule *text* breaks, or None."""
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


# ---------------------------------------------------------------------------
# Ledger lines
# ---------------------------------------------------------------------------


def _ledger_entry(entry: Entry, status: dict[str, bool]) -> LedgerEntry:
    return LedgerEntry(
        kind="logbook",
        # ASCII JSON: a raw U+2028 would split the line under `splitlines()`.
        text=json.dumps(asdict(entry), ensure_ascii=True, separators=(",", ":")),
        outcome=" ".join(f"{n}={'delivered' if ok else 'undelivered'}" for n, ok in status.items()),
    )


def _read_line(raw: str) -> tuple[Entry, dict[str, bool]] | None:
    """The entry and per-sink delivery of a ``[logbook]`` line, else None."""
    try:
        parsed = parse_line(raw)
        if parsed is None or parsed.kind != "logbook":
            return None
        entry = Entry(**json.loads(parsed.text))
    except (LedgerParseError, ValueError, TypeError):
        return None
    pairs = (token.partition("=") for token in (parsed.outcome or "").split())
    return entry, {name: state == "delivered" for name, _, state in pairs}


def pending(ledger: Path) -> list[tuple[Entry, dict[str, bool]]]:
    """Every ``[logbook]`` line of *ledger* with at least one undelivered sink."""
    if not ledger.is_file():
        return []
    lines = (_read_line(raw) for raw in ledger.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line is not None and not all(line[1].values())]


def _write_status(ledger: Path, entry: Entry, status: dict[str, bool]) -> None:
    """Replace the ``[logbook]`` line with *entry*'s key, or append one."""
    new = format_entry(_ledger_entry(entry, status))
    lines = (
        ledger.read_text(encoding="utf-8").splitlines()
        if ledger.exists()
        else ["# Lore ledger", ""]
    )
    for i, raw in enumerate(lines):
        found = _read_line(raw)
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


def month_file(repo_root: Path, month: str) -> Path:
    """The ``file`` sink's page for *month* (``YYYY-MM``)."""
    return repo_root / "docs" / "logbook" / f"{month}.md"


def _file_sink(entry: Entry, repo_root: Path) -> bool:
    month = entry.at[:7]
    path = month_file(repo_root, month)
    marker = f"<!-- {entry.key} -->"
    old = path.read_text(encoding="utf-8") if path.exists() else f"# Logbook {month}\n"
    if marker in old:
        return True
    heading, _, rest = old.partition("\n")
    header = " · ".join(p for p in (f"**{entry.at[:10]}**", entry.author, entry.ref) if p)
    block = f"{marker}\n{header}\n\n{entry.text}\n"
    rest = rest.strip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{heading}\n\n{block}" + (f"\n{rest}\n" if rest else ""), encoding="utf-8")

    attrs = repo_root / ".gitattributes"
    current = attrs.read_text(encoding="utf-8") if attrs.exists() else ""
    if GITATTRIBUTES_LINE not in current.splitlines():
        sep = "" if not current or current.endswith("\n") else "\n"
        attrs.write_text(f"{current}{sep}{GITATTRIBUTES_LINE}\n", encoding="utf-8")
    return True


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


def _deliver_one(entry: Entry, name: str, spec: dict | None, repo_root: Path) -> bool:
    kind = "file" if spec is None and name == "file" else (spec or {}).get("kind")
    try:
        if kind == "file":
            return _file_sink(entry, repo_root)
        if kind == "command" and spec and spec.get("run"):
            return _command_sink(entry, spec, repo_root)
    except Exception:  # noqa: BLE001 - a failed sink stays undelivered, never raises
        return False
    return False


def deliver(entry: Entry, names: list[str], repo_root: Path) -> dict[str, bool]:
    """Deliver *entry* to every sink in *names* at once; ``{name: delivered}``."""
    if not names:
        return {}
    specs = _sink_specs()
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {n: pool.submit(_deliver_one, entry, n, specs.get(n), repo_root) for n in names}
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
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return repo_root.name
    common = Path(out.stdout.strip())
    if out.returncode != 0 or not common.name:
        return repo_root.name
    # `<main>/.git` names the main checkout; a bare `gadget.git` names itself.
    return common.parent.name if common.name == ".git" else common.name.removesuffix(".git")


def _lock(ledger: Path, *, blocking: bool = True):
    """Hold the ledger's lock, so two flushes never deliver one entry twice."""
    from lore_core.lockfile import flocked

    return flocked(ledger.with_name(ledger.name + ".lock"), blocking=blocking)


def add(text: str, ref: str, cwd: Path) -> tuple[Entry, dict[str, bool]]:
    """Stamp *text*, keep it in the ledger of *cwd*, deliver it to the route's sinks.

    Raises ValueError outside a git repo. The caller checks the content rules.
    """
    from lore_core.git import current_repo
    from lore_core.route import resolve_route

    route = resolve_route(cwd)
    ledger = default_ledger_path(cwd)
    if ledger is None or route.repo_root is None:
        raise ValueError(f"{cwd} is not inside a git repository")
    repo = current_repo(route.repo_root) or _main_checkout_name(route.repo_root)
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


def flush(cwd: Path) -> list[tuple[Entry, dict[str, bool]]]:
    """Retry every undelivered sink in the ledger of *cwd*; return the new states."""
    from lore_core.route import resolve_route

    ledger = default_ledger_path(cwd)
    repo_root = resolve_route(cwd).repo_root
    if ledger is None or repo_root is None:
        return []
    results = []
    with _lock(ledger, blocking=False) as held:
        if not held:
            return []  # another flush or `log add` holds the ledger; it delivers
        for entry, status in pending(ledger):
            retry = [name for name, ok in status.items() if not ok]
            status = {**status, **deliver(entry, retry, repo_root)}
            _write_status(ledger, entry, status)
            results.append((entry, status))
    return results


def undelivered_count(cwd: Path) -> int:
    """How many ``[logbook]`` lines in the ledger of *cwd* wait for a sink."""
    ledger = default_ledger_path(cwd)
    return 0 if ledger is None else len(pending(ledger))
