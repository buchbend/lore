"""`gh` CLI helpers — thin subprocess wrappers used by hooks + CLI.

Fails silent: if `gh` is missing, unauthenticated, or the network is
down, every call returns an empty list. Callers surface "no issues
matched" rather than erroring — SessionStart must never block on gh.
"""

from __future__ import annotations

import json
import shlex
import subprocess

GH_TIMEOUT_SECONDS = 10


def split_filter(raw: str | None) -> list[str]:
    """Split a filter string (e.g. from CLAUDE.md `issues:`) into argv."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def run_gh(kind: str, repo: str, filter_args: list[str]) -> list[dict]:
    """Call `gh <kind> list` for `repo`. Returns [] on any failure.

    `kind` is `"issue"` or `"pr"`.
    """
    fields = "number,title,state" if kind == "issue" else "number,title,state,isDraft"
    cmd = ["gh", kind, "list", "--repo", repo, "--json", fields, *filter_args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout) or []
    except json.JSONDecodeError:
        return []


def gh_issues(repo: str, filter_str: str) -> list[dict]:
    return run_gh("issue", repo, split_filter(filter_str))


def gh_prs(repo: str, filter_str: str) -> list[dict]:
    return run_gh("pr", repo, split_filter(filter_str))


def format_issue_line(issue: dict) -> str:
    number = issue.get("number")
    title = issue.get("title") or ""
    return f"- #{number} {title}".rstrip()


def format_pr_line(pr: dict) -> str:
    number = pr.get("number")
    title = pr.get("title") or ""
    draft = " [draft]" if pr.get("isDraft") else ""
    return f"- #{number}{draft} {title}".rstrip()
