"""Lab entries: one experiment, its question and its result (PRD 0020, ADR 0019).

``lore lab add`` checks an entry for the five labelled parts (``LABELS``),
writes the stamp and delivers the entry through the scope's ``lab.sinks``
(``[file]`` without the key). Delivery, the ``[lab]`` ledger line and the
retry are the logbook's (:mod:`lore_workflow.logbook`); only the ``file``
sink differs: it writes one file per entry under ``docs/lab/YYYY-MM/``.

An entry file is the stamp as YAML frontmatter, the five parts, then a
hidden ``<!-- <key> -->`` marker that makes a repeat a no-op. The command
sink gets the same text on stdin.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lore_workflow import logbook

LABELS = ("Question", "Setup", "Result", "Interpretation", "Next")
STATUSES = ("open", "concluded", "dead-end")
MAX_WORDS = 400


def _label_re(label: str) -> re.Pattern[str]:
    # `**Question:**`, `**Question**:` and a plain `Question:` all count.
    return re.compile(rf"^\s*(?:\*\*)?{label}(?:\*\*)?:(?:\*\*)?[ \t]*(.*)$", re.M | re.I)


def broken_rule(text: str) -> str | None:
    """What is wrong with *text*, by the first rule it breaks, or None."""
    if len(text.split()) > MAX_WORDS:
        return f"more than {MAX_WORDS} words"
    missing = next((label for label in LABELS if not _label_re(label).search(text)), None)
    return f"no line starts with {missing}:" if missing else None


def _git(root: Path, *args: str, strip: bool = True) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip() if strip else out.stdout


def _data(path: str, cwd: Path, root: Path) -> dict:
    """A relative *path* is relative to *cwd*; inside the repo it is recorded from *root*."""
    full = cwd / path
    if not Path(path).is_absolute():
        # The path as written first: resolve() follows a symlink out of the repo.
        for f, r in ((Path(os.path.abspath(full)), root), (full.resolve(), root.resolve())):
            if f.is_relative_to(r):
                path = f.relative_to(r).as_posix()
                break
    try:
        with open(full, "rb") as f:
            return {"path": path, "sha256": hashlib.file_digest(f, "sha256").hexdigest()}
    except OSError:  # missing, a folder or unreadable: the path alone
        return {"path": path}


def _dirty(root: Path, text_file: Path | None) -> bool:
    """Uncommitted changes, untracked files included, besides lab entries and *text_file*.

    Git's ignored files never count.
    """
    skip = None
    if text_file is not None:
        with contextlib.suppress(ValueError, OSError):
            skip = text_file.resolve().relative_to(root.resolve()).as_posix()
    status = _git(root, "status", "--porcelain", "-z", "--untracked-files=all", strip=False)
    tokens = iter(status.split("\0"))
    for line in tokens:
        if line[:1] in ("R", "C"):  # a rename or copy: its source path follows
            next(tokens, None)
        path = line[3:]
        if path and not path.startswith("docs/lab/") and path != skip:
            return True
    return False


def add(
    text: str,
    cwd: Path,
    *,
    cmd: str | None = None,
    data: list[str] | None = None,
    thread: str | None = None,
    status: str = "open",
    text_file: Path | None = None,
) -> tuple[logbook.Entry, dict[str, bool]]:
    """Stamp *text*, keep it in the ledger of *cwd*, deliver it to the lab sinks.

    Raises ValueError outside a git repo. The caller checks the content rules.
    """
    route, ledger, repo = logbook.where(cwd)
    root = route.repo_root
    body = text.strip()
    # Microseconds: the file name orders the entries, also two in one second.
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    stamp = {
        "date": at,
        "author": logbook._git_user_name(root),
        "repo": repo,
        "commit": _git(root, "rev-parse", "HEAD"),
        "dirty": _dirty(root, text_file),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "host": socket.gethostname(),
        "status": status,
        **({"thread": thread} if thread else {}),
        **({"cmd": cmd} if cmd else {}),
        **({"data": [_data(d, cwd, root) for d in data]} if data else {}),
    }
    front = yaml.safe_dump(stamp, sort_keys=False, allow_unicode=True)
    entry = logbook.Entry(
        # The body and the stamp without its date name the entry: the same add twice
        # is one entry, a new status or new data is a new one. A retry reuses the key.
        key=f"lore:{repo}:lab:{_digest(body, stamp)}",
        at=at,
        repo=repo,
        scope=route.scope or "",
        ref=thread or "",
        author=stamp["author"],
        text=f"---\n{front}---\n\n{body}",
    )
    with logbook._lock(ledger):
        logbook._write_status(ledger, entry, dict.fromkeys(route.lab_sinks, False), "lab")
        delivered = logbook.deliver(entry, list(route.lab_sinks), root, file_sink)
        logbook._write_status(ledger, entry, delivered, "lab")
    return entry, delivered


def _digest(body: str, stamp: dict) -> str:
    named = yaml.safe_dump({k: v for k, v in stamp.items() if k != "date"}, sort_keys=True)
    return hashlib.sha256(f"{named}\n{body}".encode()).hexdigest()[:12]


def _lab_dir(repo_root: Path) -> Path:
    return repo_root / "docs" / "lab"


def file_sink(entry: logbook.Entry, repo_root: Path) -> bool:
    """Write one file per entry under ``docs/lab/YYYY-MM/``; a repeated key writes nothing."""
    marker = f"<!-- {entry.key} -->"
    past = (_lab_dir(repo_root) / entry.at[:7]).glob("*.md")
    if any(marker in f.read_text(encoding="utf-8", errors="replace") for f in past):
        return True
    month_dir = _lab_dir(repo_root) / entry.at[:7]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", entry.ref).strip("-")[:60] or "entry"
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{entry.at.replace(':', '')}_{slug}-{entry.key[-6:]}.md"
    path.write_text(f"{entry.text}\n\n{marker}\n", encoding="utf-8")
    return True


def entries(
    repo_root: Path, thread: str | None = None, limit: int | None = None
) -> list[tuple[Path, dict, str]]:
    """(path, stamp, body) of each entry in the ``file`` sink, newest first.

    Parses files only until *limit* entries match."""
    from lore_core.schema import parse_frontmatter, strip_frontmatter

    out = []
    # The file name starts with the UTC time, so the name orders the entries.
    for f in sorted(_lab_dir(repo_root).glob("*/*.md"), key=lambda f: f.name, reverse=True):
        text = f.read_text(encoding="utf-8", errors="replace")
        stamp = parse_frontmatter(text)
        if thread is None or stamp.get("thread") == thread:
            out.append((f, stamp, strip_frontmatter(text)))
            if len(out) == limit:
                break
    return out


def question(body: str) -> str:
    """The text after the ``Question:`` label, or "". It may start on the next line."""
    m = _label_re("Question").search(body)
    if not m:
        return ""
    below = body[m.end() :].strip().partition("\n")[0]
    if any(_label_re(label).match(below) for label in LABELS):
        below = ""  # an empty question: the next line is the next label
    return (m.group(1) or below).strip()
