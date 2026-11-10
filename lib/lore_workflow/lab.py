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

import hashlib
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
    """The name of the first rule *text* breaks, or None."""
    if len(text.split()) > MAX_WORDS:
        return f"at most {MAX_WORDS} words"
    missing = next((label for label in LABELS if not _label_re(label).search(text)), None)
    return f"a line starts with {missing}:" if missing else None


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _data(path: str) -> dict:
    p = Path(path)
    try:
        return {"path": path, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    except OSError:  # missing, a folder or unreadable: the path alone
        return {"path": path}


def add(
    text: str,
    cwd: Path,
    *,
    cmd: str | None = None,
    data: list[str] | None = None,
    thread: str | None = None,
    status: str = "open",
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
        "dirty": bool(_git(root, "status", "--porcelain")),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "host": socket.gethostname(),
        "status": status,
        **({"thread": thread} if thread else {}),
        **({"cmd": cmd} if cmd else {}),
        **({"data": [_data(d) for d in data]} if data else {}),
    }
    front = yaml.safe_dump(stamp, sort_keys=False, allow_unicode=True)
    entry = logbook.Entry(
        # The body, not the stamp, names the entry: the same entry twice is one entry.
        key=f"lore:{repo}:lab:{hashlib.sha256(body.encode()).hexdigest()[:12]}",
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


def _lab_dir(repo_root: Path) -> Path:
    return repo_root / "docs" / "lab"


def file_sink(entry: logbook.Entry, repo_root: Path) -> bool:
    """Write one file per entry under ``docs/lab/YYYY-MM/``; a repeated key writes nothing."""
    marker = f"<!-- {entry.key} -->"
    if any(marker in f.read_text(encoding="utf-8") for f in _lab_dir(repo_root).glob("*/*.md")):
        return True
    month_dir = _lab_dir(repo_root) / entry.at[:7]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", entry.ref).strip("-")[:60] or "entry"
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{entry.at.replace(':', '')}_{slug}-{entry.key[-6:]}.md"
    path.write_text(f"{entry.text}\n\n{marker}\n", encoding="utf-8")
    return True


def entries(repo_root: Path, thread: str | None = None) -> list[tuple[Path, dict, str]]:
    """(path, stamp, body) of each entry in the ``file`` sink, newest first."""
    from lore_core.schema import parse_frontmatter, strip_frontmatter

    out = []
    # The file name starts with the UTC time, so the name orders the entries.
    for f in sorted(_lab_dir(repo_root).glob("*/*.md"), key=lambda f: f.name, reverse=True):
        text = f.read_text(encoding="utf-8", errors="replace")
        stamp = parse_frontmatter(text)
        if thread is None or stamp.get("thread") == thread:
            out.append((f, stamp, strip_frontmatter(text)))
    return out


def question(body: str) -> str:
    """The text after the ``Question:`` label, or ""."""
    m = _label_re("Question").search(body)
    return m.group(1).strip() if m else ""
