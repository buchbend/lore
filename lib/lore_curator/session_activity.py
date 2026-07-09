"""Mechanical activity collectors for session-note Activity section.

Phase 3 of the session-note revision: instead of asking the LLM to
hand-extract "what commits did I make?" / "what issues did I touch?"
(error-prone, expensive, redundant), Curator A scans the cwd repo's
git log and the GitHub issue state directly and renders the result
into the body's ``## Activity`` parent section plus the frontmatter
``projects:`` list.

Each collector is best-effort and silent on failure: missing git,
unauthenticated gh, network outage — return empty lists and let the
omit-when-empty body renderer drop the section. SessionStart latency
budgets and Curator A's mid-stream timing both forbid blocking on
external tools.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.gh import gh_issues
from lore_core.git import current_branch, current_repo
from lore_core.types import Turn

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


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

    branch = current_branch(repo_root)
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


# ---------------------------------------------------------------------------
# Turn-deterministic extractors
#
# These shape a slice of Turn objects into the inputs the activity collectors
# need. They were lifted out of session_filer.py so the buffer-and-flush
# heartbeat path (which has no LLM dependency) can call them without pulling
# in the full filer module's LLM-summary-merge dependencies.
# ---------------------------------------------------------------------------


# Each host names the file argument differently:
# - Claude Code:  Edit/Read/Write -> ``file_path``
# - Cursor:       edit_file       -> ``target_file``;  read_file -> ``target_file``
# - VSCode/MCP:   applyEdit       -> ``uri``;  many use generic ``path``
# - Older shapes: ``filename`` is occasionally seen in MCP server tools.
# Order matters - we return the first matching key - so prefer the most
# specific names first.
_FILE_PATH_INPUT_KEYS: tuple[str, ...] = (
    "file_path", "target_file", "path", "uri", "filename",
)


# Anchored-at-line-start SHA line: ``[<branch-or-paren-label> <sha>] <subject>``.
# The label can be a plain branch (``main``), a multi-token parenthesised marker
# (``(root-commit)``, ``(detached from origin/foo)``, ``(no branch, rebasing onto X)``),
# or a paren-prefixed combination. We accept anything between the opening ``[`` and
# the SHA's leading space (``[^\]]+\s``) so all real git output forms match while
# pre-commit-hook chatter that prints similar shapes mid-line is rejected by the
# ``^`` line anchor.
_COMMIT_SHA_LINE_RE = re.compile(
    r"^\[[^\]]+\s+([0-9a-f]{7,40})\]",
    re.MULTILINE,
)


def _file_path_from_tool_input(inp: object) -> str | None:
    """Return the first non-empty string under any known file-path key."""
    if not isinstance(inp, dict):
        return None
    for key in _FILE_PATH_INPUT_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _all_turn_text(turns: list[Turn]) -> str:
    """Concatenate the user/assistant text content of a chunk's turns.

    Used for free-text issue-reference extraction (``opened #42`` /
    ``closes #29``). Tool-result text is intentionally skipped — it's
    high-volume and rarely contains genuine issue actions; including
    it would add false-positives without much recall.
    """
    parts: list[str] = []
    for t in turns:
        if t.text:
            parts.append(t.text)
    return "\n".join(parts)


def _is_git_commit_command(command: str) -> bool:
    """Return True when ``command`` is a real ``git commit`` invocation.

    Filters out:
    - substring matches (``git log --grep='git commit'``, ``echo 'git commit' | …``)
    - plumbing variants (``git commit-tree``)
    - pipelines / redirects ahead of git (``echo x | git commit -F -``) —
      these reposition git relative to its input/output stream so we can't
      trust the regex match against tool_result. Rejected.
    - ``;`` / ``&&`` / ``||`` chains ARE accepted: they don't reposition
      git relative to its own arguments, and the regex against output still
      lands on git's own ``[branch sha]`` line. The
      ``t_chained_commits_one_call`` test exercises this deliberately.
    """
    if not command or not command.strip():
        return False
    # Reject pipelines / redirects outright. Bash subshells (``$()``) and
    # heredocs (``<<``) also disqualify; heredoc check uses ``<<`` which is
    # caught by the ``<`` member of the reject set.
    if any(ch in command for ch in ("|", "<", ">")):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    # Skip leading ``VAR=value`` env assigns.
    i = 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("="):
        head = tokens[i].split("=", 1)[0]
        if head and head.replace("_", "").isalnum():
            i += 1
            continue
        break
    if i >= len(tokens) or tokens[i] != "git":
        return False
    i += 1
    # Skip global flags. Two forms each: bare-arg (``-C path``) and
    # ``=``-suffixed (``--git-dir=/repo/.git``). shlex preserves the
    # latter as a single token, so a membership check would silently
    # miss them — and `git --git-dir=/repo/.git commit -m x` would be
    # rejected as not-a-commit. Handle both.
    _COMBINED_FLAGS = ("--git-dir=", "--work-tree=", "--namespace=", "--super-prefix=")
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"):
            i += 2
        elif tok.startswith(_COMBINED_FLAGS):
            i += 1
        else:
            break
    if i >= len(tokens):
        return False
    return tokens[i] == "commit"


def _commit_shas_from_bash_results(turns: list[Turn]) -> list[str]:
    """Extract commit SHAs the model itself produced via Bash ``git commit``
    tool calls, in tool_call order, deduplicated.

    Why this exists: a time-window ``git log --since/--until`` query (the
    previous attribution path) cannot tell two parallel sessions apart and
    drops commits that land between chunk windows. The transcript is the
    only place where session identity, commit identity, and ordering coexist
    authoritatively — the SHA the model saw in the tool_result IS the SHA
    the model made.

    Pairing strategy: tool_call and tool_result Turns are NOT necessarily
    adjacent (parallel tool_use blocks return their results in a single
    user message, in arbitrary order). Build a single ``{tool_call_id:
    ToolResult}`` map in one pass, then walk the tool_call Turns in order
    and look each up.

    Out of scope (silent drop, documented):
    - Bash commands that ``cd`` into another repo — SHA still captured
      but resolution scope downstream is ``handle.cwd``'s repo only.
    - Non-Bash MCP git tools (e.g. a hypothetical ``mcp__git__commit``) —
      different ``category`` so the regex never fires.
    - ``git cherry-pick`` / ``git revert`` / ``git commit-tree`` — only
      ``git commit`` is recognised; cherry-pick/revert produce the same
      ``[branch sha]`` line and would match if we widened the gate.
    """
    # 1. Index tool_results by tool_call_id (skip None — unmappable).
    results: dict[str, Any] = {}
    for t in turns:
        tr = t.tool_result
        if tr is None or tr.tool_call_id is None:
            continue
        results[tr.tool_call_id] = tr

    seen: set[str] = set()
    out: list[str] = []
    # 2. Walk tool_call Turns in index order; preserves call-order regardless
    #    of result-arrival order.
    for t in turns:
        tc = t.tool_call
        if tc is None or tc.category != "shell_exec" or tc.id is None:
            continue
        command = tc.input.get("command") if isinstance(tc.input, dict) else None
        if not isinstance(command, str) or not _is_git_commit_command(command):
            continue
        result = results.get(tc.id)
        if result is None or not isinstance(result.output, str):
            continue
        # Last anchored match per result handles --amend / hook-auto-amend
        # (two SHA lines: original + amended; we want the amended one).
        matches = _COMMIT_SHA_LINE_RE.findall(result.output)
        if not matches:
            continue
        sha = matches[-1]
        if sha in seen:
            continue
        seen.add(sha)
        out.append(sha)
    return out


def _files_modified_from_turns(turns: list[Turn]) -> list[str]:
    """Extract de-duplicated, ordered file paths from ``file_edit`` tool
    calls in the slice — i.e. files actually written/edited, not read.

    This is the load-bearing primitive for narrative-shape selection:
    a slice with zero edits cannot honestly carry a "what we worked on"
    section. Order is first-seen so frontmatter diffs stay readable;
    we don't sort.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in turns:
        tc = t.tool_call
        if tc is None or tc.category != "file_edit":
            continue
        path = _file_path_from_tool_input(tc.input)
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _files_read_from_turns(turns: list[Turn]) -> list[str]:
    """Extract de-duplicated, ordered file paths from ``file_read`` tool
    calls in the slice. Symmetric to :func:`_files_modified_from_turns`."""
    seen: set[str] = set()
    out: list[str] = []
    for t in turns:
        tc = t.tool_call
        if tc is None or tc.category != "file_read":
            continue
        path = _file_path_from_tool_input(tc.input)
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _files_touched_from_turns(turns: list[Turn]) -> list[str]:
    """Union of edits + reads (the historic v1 buffer-event semantics).

    Kept for backward compatibility with already-archived buffers in
    ``_done/`` whose JSONL events carry the union under ``files_touched``.
    New callers should prefer :func:`_files_modified_from_turns` because
    "touched" conflates two different intents and biases narrative tense
    toward edits even when only reads occurred.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in turns:
        tc = t.tool_call
        if tc is None or tc.category not in ("file_edit", "file_read"):
            continue
        path = _file_path_from_tool_input(tc.input)
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _collect_activity(
    *,
    cwd: Path,
    wiki_root: Path,
    turns: list[Turn],
    files_touched: list[str],
    logger: "RunLogger | None" = None,
) -> dict[str, Any]:
    """Run all Phase-3 collectors for a chunk and return the inputs the
    body renderer + frontmatter need.

    Returns a dict with keys ``commits``, ``issues_opened``,
    ``issues_closed`` (rendered bullet lines), ``projects``
    (ref strings), and ``commit_shas`` (the raw SHAs Curator-A's buffer
    needs to fold into its accumulator across heartbeats).

    Commit attribution is SHA-bound: extracts SHAs from this chunk's own
    Bash ``git commit`` tool_results and resolves them against the cwd's
    repo. No time-window fallback — under-attribution beats wrong
    attribution. See ``_commit_shas_from_bash_results`` and
    ``collect_commits_by_sha`` for the rationale.
    """
    from lore_core.git import git_repo_root

    repo_root = git_repo_root(cwd)
    repo = current_repo(cwd) or ""

    shas = _commit_shas_from_bash_results(turns)
    raw_commits = collect_commits_by_sha(repo_root, shas)

    if logger is not None:
        logger.emit(
            "commit-shas-captured",
            captured=len(raw_commits),
            dropped=max(0, len(shas) - len(raw_commits)),
            shas_seen=len(shas),
        )

    # Issue-reference extraction: union turn text + commit subjects + bodies
    # so `closes #29` lands whether the LLM wrote it in chat, in the commit
    # subject, or in the commit body trailer.
    commit_text = "\n".join(
        c.subject + ("\n" + c.body if c.body else "")
        for c in raw_commits
    )
    turn_text = _all_turn_text(turns)
    opened_refs, closed_refs = extract_issue_refs(turn_text + "\n" + commit_text)

    issues_opened, issues_closed = collect_issues_in_window(
        repo,
        referenced_opened=opened_refs,
        referenced_closed=closed_refs,
    )

    projects = collect_projects_for_session(
        cwd=cwd,
        files_touched=files_touched,
        wiki_root=wiki_root,
    )

    return {
        "commits": render_commits_section(raw_commits),
        "issues_opened": render_issue_section(issues_opened, repo=repo),
        "issues_closed": render_issue_section(issues_closed, repo=repo),
        "projects": projects,
        "commit_shas": shas,
    }
