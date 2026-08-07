"""Build the transcript ledger's linkage block. Zero LLM, zero network.

The block answers "where did this session work, and what did it touch?" —
``repo``, ``branch``, ``prs``, ``issues``, ``commits``, ``files``. It is
the personal layer's only index: the owner drills their own archive with
it, and the SessionStart recap renders from it.

Two depths, because the two sources cost very different amounts:

* **shallow** (``turns=None``) — git state and the branch name only.
  Four ``git`` invocations (``rev-parse --abbrev-ref``, ``rev-parse
  --show-toplevel``, ``remote get-url upstream``, ``remote get-url
  origin``), and it runs on every capture hook including each
  ``UserPromptSubmit``. Returns ``repo``/``branch``/``prs``/``issues``
  and nothing else, so a caller merging it over a stored block never
  clobbers a deep pass's results.
  ponytail: not memoised. The hook is a fresh process per invocation, so
  an in-process cache would save nothing; a cross-process one would have
  to invalidate on every branch switch. Give it a stamped cache in
  ``.lore/`` only if the ~15 ms shows up in hook telemetry.
* **deep** (``turns`` supplied) — adds ``commits`` and ``files`` read out
  of the transcript itself, and widens the ref set with the commit
  subjects. Costs a full transcript parse, so callers run it at a session
  boundary or in a migration, not per prompt.

Refs are classified by syntax (``lore_core.linkage``), never by asking
GitHub: a bare ``#42`` stays an issue. Under-claiming beats a network
round-trip per ref.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.git import git_repo_root
from lore_core.linkage import extract_linkage

if TYPE_CHECKING:
    from lore_core.types import Turn

__all__ = ["build_linkage"]

# ---------------------------------------------------------------------------
# Turn-deterministic extractors. Moved in from the session-note activity
# collectors (PRD 0013) — every other public helper there had no caller left.
# ---------------------------------------------------------------------------

# Each host names the file argument differently:
# - Claude Code:  Edit/Read/Write -> ``file_path``
# - Cursor:       edit_file       -> ``target_file``;  read_file -> ``target_file``
# - VSCode/MCP:   applyEdit       -> ``uri``;  many use generic ``path``
# - Older shapes: ``filename`` is occasionally seen in MCP server tools.
# Order matters - we return the first matching key - so prefer the most
# specific names first.
_FILE_PATH_INPUT_KEYS: tuple[str, ...] = (
    "file_path",
    "target_file",
    "path",
    "uri",
    "filename",
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
      lands on git's own ``[branch sha]`` line.
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

    Why this exists: a time-window ``git log --since/--until`` query cannot
    tell two parallel sessions apart and drops commits that land between
    chunk windows. The transcript is the only place where session identity,
    commit identity, and ordering coexist authoritatively — the SHA the
    model saw in the tool_result IS the SHA the model made.

    Pairing strategy: tool_call and tool_result Turns are NOT necessarily
    adjacent (parallel tool_use blocks return their results in a single
    user message, in arbitrary order). Build a single ``{tool_call_id:
    ToolResult}`` map in one pass, then walk the tool_call Turns in order
    and look each up.

    Out of scope (silent drop, documented):
    - Bash commands that ``cd`` into another repo — SHA still captured
      but resolution scope downstream is the caller's repo only.
    - Non-Bash MCP git tools (e.g. a hypothetical ``mcp__git__commit``) —
      different ``category`` so the regex never fires.
    - ``git cherry-pick`` / ``git revert`` / ``git commit-tree`` — only
      ``git commit`` is recognised.
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


#: Per-entry caps. The ledger sits on the capture hot path — it is read
#: five-plus times per hook — so one runaway session must not double the
#: file every reader parses. First-seen order is kept, so the caps drop
#: the tail of a long session, not its opening.
#: ponytail: fixed caps; make them config keys only if a real session
#: loses linkage that someone actually went looking for.
MAX_FILES = 50
MAX_COMMITS = 20


def build_linkage(
    cwd: Path | str | None,
    *,
    turns: list[Turn] | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Return the linkage block for a session working in ``cwd``.

    Keys present depend on the depth — see the module docstring. Callers
    merge the result over the entry's stored block rather than replacing
    it.

    ``branch`` overrides git's current branch, for a caller reading a
    session that ran on a branch the checkout has since left.
    """
    commit_texts: list[str] = []
    commits: list[str] = []
    files: list[str] = []

    if turns is not None:
        commits = _commit_shas_from_bash_results(turns)[:MAX_COMMITS]
        # Resolve the repo root once, not once per file: this ran a
        # `git rev-parse --show-toplevel` per edited file, so a 50-file
        # session spawned 50 subprocesses on every session boundary.
        repo_root = git_repo_root(Path(cwd)) if cwd else None
        files = [
            _repo_relative(p, repo_root) for p in _files_modified_from_turns(turns)[:MAX_FILES]
        ]
        commit_texts = [_all_turn_text(turns)]

    linkage = extract_linkage(cwd=cwd, commit_texts=commit_texts, branch=branch)

    # An epic is an issue on GitHub; the ledger indexes both under
    # ``issues`` so "which sessions touched #362" answers for an epic too.
    issues = sorted(set(linkage.issues) | set(linkage.epics))

    block: dict[str, Any] = {
        "repo": linkage.repo,
        "branch": linkage.branch,
        "prs": list(linkage.prs),
        "issues": issues,
    }
    if turns is not None:
        block["commits"] = commits
        block["files"] = files
    return block


def _repo_relative(path: str, repo_root: Path | None) -> str:
    """Strip the checkout prefix so the same file matches across worktrees.

    Falls back to the path as written when it lies outside the repo (or
    when there is no repo) — a wrong-looking absolute path is still a
    usable pointer; a silently dropped one is not.
    """
    if repo_root is None:
        return path
    try:
        return str(Path(path).relative_to(repo_root))
    except ValueError:
        return path
