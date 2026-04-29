"""Mechanical activity collectors for session-note Activity section.

Phase 3 of the session-note revision: instead of asking the LLM to
hand-extract "what commits did I make?" / "what issues did I touch?"
(error-prone, expensive, redundant), Curator A scans the cwd repo's
git log and the GitHub issue state directly and renders the result
into the body's ``## Activity`` parent section plus the frontmatter
``plans:`` / ``projects:`` lists.

Each collector is best-effort and silent on failure: missing git,
unauthenticated gh, network outage — return empty lists and let the
omit-when-empty body renderer drop the section. SessionStart latency
budgets and Curator A's mid-stream timing both forbid blocking on
external tools.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lore_core.gh import gh_issues
from lore_core.git import current_repo


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitRef:
    """One commit landed in the work window."""

    short_hash: str
    subject: str
    branch: str  # current branch when collected; same for every commit in a session
    repo: str   # canonical "org/name" or "" if outside a known remote


def collect_commits_in_window(
    repo_root: Path | None,
    *,
    since: datetime,
    until: datetime,
    timeout_seconds: float = 5.0,
) -> list[CommitRef]:
    """Return commits authored in ``[since, until]`` inside ``repo_root``.

    Uses ``git log --since/--until`` rather than ``HEAD~N`` so the
    window is time-bounded (matches the chunk's actual work span).
    Returns newest first. Empty list on any error.
    """
    if repo_root is None or not Path(repo_root).exists():
        return []
    try:
        since_str = since.replace(tzinfo=UTC).isoformat() if since.tzinfo is None else since.isoformat()
        until_str = until.replace(tzinfo=UTC).isoformat() if until.tzinfo is None else until.isoformat()
        result = subprocess.run(
            [
                "git", "-C", str(repo_root),
                "log",
                f"--since={since_str}",
                f"--until={until_str}",
                "--no-decorate",
                "--pretty=format:%h\t%s",
            ],
            capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    branch = _current_branch(repo_root, timeout_seconds)
    repo_name = current_repo(repo_root) or ""
    out: list[CommitRef] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        out.append(CommitRef(
            short_hash=sha.strip(),
            subject=subject.strip(),
            branch=branch,
            repo=repo_name,
        ))
    return out


def _current_branch(repo_root: Path, timeout_seconds: float) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def render_commits_section(commits: list[CommitRef]) -> list[str]:
    """Render one bullet per commit for ``### Commits``.

    Format: ``- `<sha>` <subject> (<repo>/<branch>)`` — repo+branch are
    omitted when empty (rare; only happens outside a tracked remote).
    """
    lines: list[str] = []
    for c in commits:
        location = ""
        if c.repo and c.branch:
            location = f" ({c.repo}/{c.branch})"
        elif c.branch:
            location = f" ({c.branch})"
        lines.append(f"- `{c.short_hash}` {c.subject}{location}")
    return lines


# ---------------------------------------------------------------------------
# Issues — opened / closed in the work window, scoped to references
# ---------------------------------------------------------------------------


# Match `<verb> #<N>` where verb is opens/opened/closes/closed/fix/fixes/fixed.
# This catches both commit-message conventions (``fixes #29``) and free turn
# text (``opened #42``). The action determines opened vs closed.
_ISSUE_REF_RE = re.compile(
    r"\b(opens?|opened|closes?|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+#(\d+)\b",
    re.IGNORECASE,
)
_OPEN_VERBS = {"opens", "opened"}


def extract_issue_refs(text: str) -> tuple[set[int], set[int]]:
    """Scan ``text`` for ``<verb> #<N>`` patterns. Returns ``(opened, closed)``.

    Idempotent: multiple references to the same #N collapse. Both sets
    are integers (issue numbers) — formatting and gh state lookup
    happen at the rendering step.
    """
    opened: set[int] = set()
    closed: set[int] = set()
    if not text:
        return opened, closed
    for verb, num in _ISSUE_REF_RE.findall(text):
        try:
            n = int(num)
        except ValueError:
            continue
        if verb.lower() in _OPEN_VERBS:
            opened.add(n)
        else:
            closed.add(n)
    return opened, closed


def collect_issues_in_window(
    repo: str,
    *,
    referenced_opened: set[int],
    referenced_closed: set[int],
) -> tuple[list[dict], list[dict]]:
    """Return ``(opened, closed)`` issue dicts for the cwd repo.

    Filters by intersection with ``referenced_*`` so only issues
    actually mentioned by this session show up. This avoids the
    "everything someone closed today" false-positive that a pure
    ``--search "closed:>=<since>"`` would produce.

    Each issue dict has ``number``, ``title``, ``state`` (raw gh
    output). Empty list on missing gh / unauthenticated / network
    error.
    """
    if not repo:
        return [], []
    opened: list[dict] = []
    closed: list[dict] = []
    if referenced_opened:
        # gh's `--search` is more accurate than --state for created-window
        # filtering; but for our intersection-with-references model, just
        # fetching the issues by number is cleaner. Use one --search per
        # batch with explicit numbers.
        all_open = gh_issues(repo, "--state open")
        opened = [i for i in all_open if int(i.get("number") or 0) in referenced_opened]
    if referenced_closed:
        all_closed = gh_issues(repo, "--state closed")
        closed = [i for i in all_closed if int(i.get("number") or 0) in referenced_closed]
    return opened, closed


def render_issue_section(issues: list[dict], *, repo: str) -> list[str]:
    """Render one bullet per issue for ``### Issues opened/closed``."""
    lines: list[str] = []
    for issue in issues:
        number = issue.get("number")
        title = issue.get("title") or ""
        suffix = f" ({repo})" if repo else ""
        lines.append(f"- #{number} {title}{suffix}".rstrip())
    return lines


# ---------------------------------------------------------------------------
# Plans — frontmatter plans: list
# ---------------------------------------------------------------------------


# Body wikilink form: ``[[plan/<slug>]]`` or ``[[plan/<slug>#sN]]``.
_PLAN_WIKILINK_RE = re.compile(r"\[\[plan/([A-Za-z0-9][\w-]*)(?:#s(\d+))?\]\]")
# Trailer form: ``Plan: <slug>#s<N>`` on its own line.
_PLAN_TRAILER_RE = re.compile(
    r"^Plan:\s*([A-Za-z0-9][\w-]*)#s(\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def collect_plans_advanced(
    *,
    repo_root: Path | None,
    body_text: str,
    wiki_root: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    timeout_seconds: float = 5.0,
) -> list[str]:
    """Collect plan refs this chunk advanced.

    Sources:
    - Git ``Plan: <slug>#s<N>`` trailers in commits within ``[since, until]``.
      Without a window the trailer scan is skipped — a windowless walk
      would attribute long-lived trailers (e.g. a recent feature commit)
      to every later session that happens to share the last-200-commit
      slice with it.
    - ``[[plan/<slug>(#sN)?]]`` wikilinks in the chunk's body text
      (Curator A may have emitted them in narrative bullets).

    Refs are validated against ``wiki_root/plans/<slug>.md``. Hallucinated
    plans (slug doesn't exist) are silently dropped. Step-less wikilinks
    (``[[plan/foo]]``) become ``"foo"`` (no anchor); step-bearing become
    ``"foo#s2"``. Deduped; insertion order preserved.
    """
    plans_dir = wiki_root / "plans"
    refs: list[str] = []
    seen: set[str] = set()

    have_window = since is not None and until is not None
    if (
        plans_dir.is_dir()
        and repo_root is not None
        and Path(repo_root).exists()
        and have_window
    ):
        known_slugs = {p.stem.lower() for p in plans_dir.glob("*.md")}
        if known_slugs:
            for slug, step_num in _scan_window_trailers(
                Path(repo_root),
                since=since,
                until=until,
                timeout_seconds=timeout_seconds,
            ):
                if slug.lower() not in known_slugs:
                    continue
                ref = f"{slug}#s{step_num}"
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)

    # Body wikilinks — accept step-less form too.
    for slug, step_num in _PLAN_WIKILINK_RE.findall(body_text or ""):
        if not (plans_dir / f"{slug}.md").exists():
            continue
        ref = f"{slug}#s{step_num}" if step_num else slug
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)

    return refs


def _scan_window_trailers(
    repo_root: Path,
    *,
    since: datetime,
    until: datetime,
    timeout_seconds: float,
) -> list[tuple[str, str]]:
    """Yield ``(slug, step_num)`` from ``Plan:`` trailers in window commits.

    Single ``git log --since/--until`` call (vs. one per slug previously),
    so cost is independent of how many plans the wiki has. Returns ``[]``
    on any subprocess error — Curator A must never block on git.
    """
    since_iso = (since.replace(tzinfo=UTC) if since.tzinfo is None else since).isoformat()
    until_iso = (until.replace(tzinfo=UTC) if until.tzinfo is None else until).isoformat()
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo_root),
                "log",
                f"--since={since_iso}",
                f"--until={until_iso}",
                "--no-decorate",
                # NUL terminator between commits so trailers can't bleed
                # across boundaries when one body ends mid-line.
                "--pretty=format:%B%x00",
            ],
            capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    out: list[tuple[str, str]] = []
    for body in result.stdout.split("\x00"):
        if not body.strip():
            continue
        for m in _PLAN_TRAILER_RE.finditer(body):
            out.append((m.group(1), m.group(2)))
    return out


# ---------------------------------------------------------------------------
# Projects — frontmatter projects: list
# ---------------------------------------------------------------------------


def collect_projects_for_session(
    *,
    cwd: Path | str,
    files_touched: list[str],
    wiki_root: Path,
) -> list[str]:
    """Collect project notes this session relates to.

    Two sources:
    - The cwd's git repo (via ``current_repo``). The matching project
      note is the one whose filename equals the repo's tail (e.g. repo
      ``buchbend/lore`` → ``wiki/<wiki>/projects/lore.md``).
    - Repo prefixes inferred from absolute paths in ``files_touched``
      (``/home/x/git/foo/src/a.py`` → ``foo``). Only when the project
      note exists.

    Validated against ``wiki_root/projects/<slug>.md``. Missing project
    notes are dropped silently. Deduped; insertion order preserved.
    """
    projects_dir = wiki_root / "projects"
    refs: list[str] = []
    seen: set[str] = set()
    if not projects_dir.is_dir():
        return refs

    repo = current_repo(cwd)
    if repo:
        slug = repo.rsplit("/", 1)[-1]
        if (projects_dir / f"{slug}.md").exists() and slug not in seen:
            seen.add(slug)
            refs.append(slug)

    # Repo prefix from absolute paths. Hand-rolled: any path under a dir
    # that contains a ``.git/`` sibling counts as that dir's repo. We
    # avoid actually calling git here (cost) — only pattern-match common
    # roots.
    for raw_path in files_touched:
        if not isinstance(raw_path, str) or not raw_path.startswith("/"):
            continue
        slug = _project_slug_from_abs_path(raw_path, projects_dir)
        if slug and slug not in seen:
            seen.add(slug)
            refs.append(slug)

    return refs


def _project_slug_from_abs_path(abs_path: str, projects_dir: Path) -> str | None:
    """Infer the project-note slug for an absolute file path.

    Walks parent directories looking for one whose basename matches a
    project note in ``projects_dir``. Returns None when no segment
    matches an existing project note (avoids creating phantom links).
    """
    p = Path(abs_path).parent
    for parent in [p, *p.parents]:
        candidate = parent.name
        if not candidate:
            continue
        if (projects_dir / f"{candidate}.md").exists():
            return candidate
    return None
