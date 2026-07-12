"""Deterministic verification of the refs a fact carries.

A rendered note line earns authoritative phrasing only from a ref that code
could check, so what counts as evidence is decided here — never by a model, and
never by the absence of a failure.

Three verdicts, and the asymmetry between them is the point:

``VERIFIED``
    A check ran and succeeded. Commits, tags and files are checked *exactly*:
    against the session's own frontmatter facts (what capture already recorded)
    and then against local git and the filesystem. PRs and issues are checked
    best-effort through ``gh``.
``MISSING``
    A check ran and came back empty — the commit, tag or file does not exist.
    The renderer demotes the line, so a hallucinated ref costs authority
    instead of buying it.
``UNCHECKED``
    Nothing could be checked: offline, no ``gh``, no repo to ask, a value that
    is not a sha / tag / number. Never a check mark. A failed ``gh`` call is
    always this and never ``MISSING`` — GitHub being unreachable is not
    evidence that a PR is fake (positive evidence only; see ``docs/adr/0004``).

Nothing here raises: verification degrades the phrasing of a note, it never
fails a render. And nothing here calls an LLM.

Ref values are model-authored strings. They are matched against a strict
pattern per type before they may become an argument to ``git`` or ``gh``, so a
value like ``--upload-pack=…`` is an unverifiable ref, not an option.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = ["VERIFIED", "UNCHECKED", "MISSING", "verify_refs"]

VERIFIED = "verified"
UNCHECKED = "unchecked"
MISSING = "missing"

_TIMEOUT_SECONDS = 10

# Argument gates. A value that does not match is unverifiable by construction —
# it never reaches a subprocess.
_SHA_RE = re.compile(r"\A[0-9a-f]{7,40}\Z")
_TAG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/+-]{0,99}\Z")
_NUMBER_RE = re.compile(r"\A#?(\d{1,9})\Z")


def _run(cmd: list[str], *, cwd: Path | None = None) -> int | None:
    """Run ``cmd`` and return its exit code, or ``None`` if it could not run.

    ``None`` is the "no evidence either way" answer — a missing binary, a
    timeout, a broken sandbox. Callers must never read it as a negative.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    return result.returncode


def _sha_verified(value: str, commits: Sequence[str]) -> bool:
    """Whether a captured commit sha and the ref's sha are the same commit."""
    return any(c and (c.lower().startswith(value) or value.startswith(c.lower())) for c in commits)


def _verify_commit(value: str, commits: Sequence[str], repo_root: Path | None) -> str:
    value = value.strip().lower()
    if not _SHA_RE.match(value):
        return UNCHECKED
    if _sha_verified(value, commits):
        return VERIFIED
    if repo_root is None:
        return UNCHECKED
    # `^{commit}` refuses a sha that resolves to a blob or a tree: a fact that
    # points at "a commit" must point at one.
    code = _run(["git", "cat-file", "-e", f"{value}^{{commit}}"], cwd=repo_root)
    if code is None:
        return UNCHECKED
    return VERIFIED if code == 0 else MISSING


def _verify_tag(value: str, repo_root: Path | None) -> str:
    value = value.strip()
    if not _TAG_RE.match(value) or repo_root is None:
        return UNCHECKED
    code = _run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{value}"], cwd=repo_root)
    if code is None:
        return UNCHECKED
    return VERIFIED if code == 0 else MISSING


def _verify_file(value: str, files: Sequence[str], repo_root: Path | None) -> str:
    value = value.strip()
    if not value:
        return UNCHECKED
    if value in files:
        return VERIFIED  # capture recorded touching it; it existed in this session
    path = Path(value)
    if not path.is_absolute():
        if repo_root is None:
            return UNCHECKED
        path = repo_root / path
    try:
        return VERIFIED if path.exists() else MISSING
    except OSError:
        return UNCHECKED


def _numbers(values: Sequence[str]) -> set[str]:
    out = set()
    for v in values:
        match = _NUMBER_RE.match(str(v).strip())
        if match:
            out.add(match.group(1))
    return out


def _verify_number(
    kind: str,
    value: str,
    known: Sequence[str],
    repo_root: Path | None,
    repo: str,
) -> str:
    """PRs and issues: frontmatter first, then ``gh``, best-effort.

    ``gh`` failing means only that ``gh`` failed — an unreachable API, no auth,
    no network. That is never evidence the ref is fake, so a non-zero exit
    demotes to ``UNCHECKED``, never to ``MISSING``.
    """
    match = _NUMBER_RE.match(value.strip())
    if not match:
        return UNCHECKED
    number = match.group(1)
    if number in _numbers(known):
        return VERIFIED
    if repo_root is None and not repo:
        return UNCHECKED
    cmd = ["gh", kind, "view", number, "--json", "number"]
    if repo:
        cmd += ["--repo", repo]
    return VERIFIED if _run(cmd, cwd=repo_root) == 0 else UNCHECKED


def verify_refs(
    refs: Iterable[tuple[str, str]],
    *,
    commits: Sequence[str] = (),
    prs: Sequence[str] = (),
    issues: Sequence[str] = (),
    files: Sequence[str] = (),
    repo_root: Path | None = None,
    repo: str = "",
) -> dict[tuple[str, str], str]:
    """Verdict for every ``(type, value)`` ref, checking each distinct ref once.

    ``commits`` / ``prs`` / ``issues`` / ``files`` are the session's own
    deterministic frontmatter facts — the offline evidence, and the only source
    consulted when there is no repo to ask. ``repo_root`` gates every git and
    filesystem check (absent: nothing local is checked, so nothing is promoted);
    ``repo`` (``owner/name``) is passed to ``gh``.
    """
    verdicts: dict[tuple[str, str], str] = {}
    for ref in refs:
        if ref in verdicts:
            continue
        kind, value = ref[0], ref[1]
        if kind == "commit":
            verdict = _verify_commit(value, commits, repo_root)
        elif kind == "tag":
            verdict = _verify_tag(value, repo_root)
        elif kind == "file":
            verdict = _verify_file(value, files, repo_root)
        elif kind == "pr":
            verdict = _verify_number("pr", value, prs, repo_root, repo)
        elif kind == "issue":
            verdict = _verify_number("issue", value, issues, repo_root, repo)
        else:
            verdict = UNCHECKED  # a ref type code cannot check is never evidence
        verdicts[ref] = verdict
    return verdicts
