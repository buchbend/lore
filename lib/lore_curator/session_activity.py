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
    body: str = ""  # full commit body (post-subject) — Step 3 trailer scan reads this


def collect_commits_by_sha(
    repo_root: Path | None,
    shas: list[str],
    *,
    timeout_seconds: float = 5.0,
) -> list[CommitRef]:
    """Resolve a list of SHAs into ``CommitRef`` records inside ``repo_root``.

    SHAs that don't resolve (rebased away, wrong repo, typo) are silently
    dropped — fail-soft is the contract because under-attribution beats
    wrong-attribution and the upstream extractor (``_commit_shas_from_bash_results``)
    captures intent regardless of post-hoc repo state.

    Empty input MUST short-circuit: bare ``git show`` with no args defaults
    to ``HEAD``, which would re-introduce the parallel-session bleed in a
    different shape.

    Two-pass design (architect-recommended):
      1. ``git cat-file --batch-check`` filters phantoms in O(1) git invocations
         without aborting the run on the first missing ref (which ``git show``
         would do).
      2. ``git log --no-walk -z`` over survivors yields ``%h\\t%s\\t%B`` per
         commit, NUL-separated so commit bodies containing literal tabs or
         newlines parse cleanly.

    Out-of-cwd-repo commits are documented v1 risk — Bash commands that
    ``cd`` into another repo will have their SHAs captured by the extractor
    but dropped here when this resolver runs against ``handle.cwd``'s repo
    only.
    """
    if not shas or repo_root is None or not Path(repo_root).exists():
        return []

    # Pass 1: filter phantoms with batch-check. Stdin: one SHA per line.
    # Stdout: ``<sha> <type> <size>`` for resolvable, ``<sha> missing`` for not.
    try:
        check = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "--batch-check"],
            input="\n".join(shas) + "\n",
            capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if check.returncode != 0:
        return []

    survivors: list[str] = []
    for line in check.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "commit":
            survivors.append(parts[0])
    if not survivors:
        return []

    # Pass 2: hydrate survivors. ``--no-walk`` shows only the named commits
    # (not their ancestors). ``-z`` adds a NUL terminator after each entry's
    # %B body so multi-line bodies don't bleed into the next record. Field
    # separator is ASCII unit-separator (``%x1f``), not tab — commit subjects
    # legitimately contain tabs and would corrupt a tab-separated parse.
    try:
        log = subprocess.run(
            [
                "git", "-C", str(repo_root),
                "log", "--no-walk", "-z",
                "--pretty=format:%H%x1f%h%x1f%s%x1f%B",
                *survivors,
            ],
            capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if log.returncode != 0:
        return []

    branch = _current_branch(repo_root, timeout_seconds)
    repo_name = current_repo(repo_root) or ""

    # Map full-SHA → CommitRef so we can return in the order the caller asked.
    by_full_sha: dict[str, CommitRef] = {}
    for record in log.stdout.split("\x00"):
        record = record.strip("\n")
        if not record:
            continue
        # Split into [full_sha, short_hash, subject, body]; body may contain
        # tabs/newlines and is the remainder.
        parts = record.split("\x1f", 3)
        if len(parts) < 3:
            continue
        full_sha = parts[0].strip()
        short_hash = parts[1].strip()
        subject = parts[2]
        body = parts[3] if len(parts) >= 4 else ""
        # Strip a single trailing newline that git's %B always appends; keep
        # internal newlines intact.
        if body.endswith("\n"):
            body = body[:-1]
        by_full_sha[full_sha] = CommitRef(
            short_hash=short_hash, subject=subject,
            branch=branch, repo=repo_name, body=body,
        )

    # Order results by the caller's input order (preserves call-site ordering
    # from the extractor). Map short / abbreviated SHAs to full via prefix.
    out: list[CommitRef] = []
    seen_full: set[str] = set()
    for sha in shas:
        for full, ref in by_full_sha.items():
            if full.startswith(sha) and full not in seen_full:
                seen_full.add(full)
                out.append(ref)
                break
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


# Body wikilink form: ``[[plan/<slug>]]``, ``[[plan/<slug>#step-N]]``, or
# legacy ``[[plan/<slug>#sN]]``. The anchor (``step-N`` / ``sN`` / empty) is
# captured in group 2 verbatim so the emitted ref matches whatever the user
# wrote; canonicalization happens at the writer boundary, not here.
_PLAN_WIKILINK_RE = re.compile(
    r"\[\[plan/([A-Za-z0-9][\w-]*)(?:#(step-\d+|s\d+))?\]\]"
)
# Trailer form: ``Plan: <slug>#step-<N>`` (canonical) or ``Plan: <slug>#s<N>``
# (legacy). Group 2 captures the full anchor verbatim.
_PLAN_TRAILER_RE = re.compile(
    r"^Plan:\s*([A-Za-z0-9][\w-]*)#(step-\d+|s\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def collect_plans_advanced(
    *,
    body_text: str,
    wiki_root: Path,
    commit_bodies: list[str] | None = None,
) -> list[str]:
    """Collect plan refs this chunk advanced.

    Sources:
    - ``Plan: <slug>#step-<N>`` trailers (or legacy ``#s<N>``) inside
      ``commit_bodies`` — i.e. the bodies of commits this chunk's Bash
      tool_results already attributed via SHA. No separate ``git log``
      call: SHA-bound coherence by construction. Without ``commit_bodies``
      the trailer scan yields nothing.
    - ``[[plan/<slug>(#step-N)?]]`` wikilinks in the chunk's body text
      (Curator A may have emitted them in narrative bullets).

    Refs are validated against ``wiki_root/plans/<slug>.md``. Hallucinated
    plans (slug doesn't exist) are silently dropped. Step-less wikilinks
    (``[[plan/foo]]``) become ``"foo"`` (no anchor); step-bearing become
    ``"foo#step-2"``. Deduped; insertion order preserved.
    """
    plans_dir = wiki_root / "plans"
    refs: list[str] = []
    seen: set[str] = set()

    if plans_dir.is_dir() and commit_bodies:
        known_slugs = {p.stem.lower() for p in plans_dir.glob("*.md")}
        if known_slugs:
            for body in commit_bodies:
                if not body:
                    continue
                for m in _PLAN_TRAILER_RE.finditer(body):
                    slug = m.group(1)
                    anchor = m.group(2)
                    if slug.lower() not in known_slugs:
                        continue
                    ref = f"{slug}#{anchor}"
                    if ref not in seen:
                        seen.add(ref)
                        refs.append(ref)

    # Body wikilinks — accept step-less form too. ``anchor`` is the
    # full step ID (``step-N`` or legacy ``sN``) or "" when absent.
    for slug, anchor in _PLAN_WIKILINK_RE.findall(body_text or ""):
        if not (plans_dir / f"{slug}.md").exists():
            continue
        ref = f"{slug}#{anchor}" if anchor else slug
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)

    return refs


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
