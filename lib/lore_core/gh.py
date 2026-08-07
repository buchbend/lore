"""`gh` CLI helper — thin subprocess wrapper.

Fails silent: if `gh` is missing, unauthenticated, or the network is
down, the call returns None. Callers surface "not found" rather than
erroring — SessionStart must never block on gh.

The list helpers and their line formatters are gone. They existed to
count a repo's open issues and PRs for the SessionStart banner; the
banner dropped those counts, and nothing took their place. Their input,
the `issues:`/`prs:` filter keys in a repo's lore block, went with them.
"""

from __future__ import annotations

import json
import subprocess

GH_TIMEOUT_SECONDS = 10


def gh_issue_view(repo: str, number: int) -> dict | None:
    """Fetch one issue/epic's number, title, state. None on any failure."""
    cmd = ["gh", "issue", "view", str(number), "--repo", repo, "--json", "number,title,state"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
