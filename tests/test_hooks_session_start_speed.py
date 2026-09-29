"""`lore hook session-start` wall time and network independence (#424).

The hook runs as a fresh process on every Claude Code session start, so these
tests drive the real CLI in a subprocess against a realistic vault:

* a project repo holding a copy of lore's own ``lib/`` tree, so a code-map
  refresh costs what it costs on a real repository;
* a wiki that is its own git repo with an ``origin`` remote reached over a
  fake ssh transport, so the test controls how slow (or dead) the network is.

Both tests are marked ``slow`` and run in CI's plain ``pytest -q``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import lore_cli
import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only timing"),
]

LIB_DIR = Path(lore_cli.__file__).resolve().parents[1]
BUDGET_S = 2.0


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _ssh_script(path: Path, body: str) -> Path:
    """Write a fake ``core.sshCommand``.

    Git calls it as ``<script> <host> "git-upload-pack '/repo'"``; the script
    drops the host and runs the command locally after *body*.
    """
    path.write_text(f'#!/bin/sh\n{body}\nshift\nexec sh -c "$*"\n')
    path.chmod(0o755)
    return path


def _build_vault(tmp_path: Path, ssh_body: str) -> tuple[Path, Path]:
    """Return ``(lore_root, project)`` for an attached project and a pulled wiki."""
    from lore_core.state.attachments import Attachment, AttachmentsFile

    project = tmp_path / "project"
    shutil.copytree(LIB_DIR, project / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    _git(project, "init", "-q", "-b", "main")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")

    lore_root = tmp_path / "vault"
    wiki = lore_root / "wiki" / "demo"
    wiki.mkdir(parents=True)
    for i in range(50):
        (wiki / f"note{i}.md").write_text(f"---\ntype: concept\ntitle: n{i}\n---\n\nbody\n")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(wiki, "init", "-q", "-b", "main")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "init")
    _git(wiki, "remote", "add", "origin", str(remote))
    _git(wiki, "push", "-q", "-u", "origin", "main")
    # From here on every fetch goes through the fake ssh transport.
    _git(wiki, "remote", "set-url", "origin", f"ssh://example.invalid{remote}")
    ssh = _ssh_script(tmp_path / "ssh.sh", ssh_body)
    _git(wiki, "config", "core.sshCommand", str(ssh))

    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki="demo",
            scope="demo:proj",
            attached_at=datetime.now(UTC),
            source="manual",
        )
    )
    af.save()
    return lore_root, project


def _run_hook(lore_root: Path, project: Path, *, timeout: float) -> tuple[float, str]:
    env = dict(os.environ)
    for var in ("LORE_CURATOR_MODE", "CLAUDE_SESSION_ID", "LORE_SUPPRESS_CAPTURE"):
        env.pop(var, None)
    env["LORE_ROOT"] = str(lore_root)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(LIB_DIR), env.get("PYTHONPATH")]))
    payload = json.dumps({"session_id": "speed-test", "hook_event_name": "SessionStart"})
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "lore_cli", "hook", "session-start"],
        cwd=project,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    return elapsed, proc.stdout


@pytest.fixture()
def reap_ssh(tmp_path: Path):
    """Kill the background pull a test leaves sleeping in the fake ssh.

    The detached child may reach the fake ssh only after the test ends, so
    wait briefly for it, then kill its whole process group (the child runs
    in a session of its own).
    """
    pids = tmp_path / "ssh.pids"
    yield f"echo $$ >> {pids}"
    deadline = time.monotonic() + 3
    while not pids.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if pids.exists():
        for line in pids.read_text().split():
            with contextlib.suppress(ProcessLookupError, PermissionError, ValueError):
                os.killpg(os.getpgid(int(line)), signal.SIGKILL)


def test_session_start_under_two_seconds_on_warm_cache(tmp_path: Path, reap_ssh: str) -> None:
    # A one-second network round trip on every fetch.
    lore_root, project = _build_vault(tmp_path, f"{reap_ssh}\nsleep 1")
    _run_hook(lore_root, project, timeout=60)  # warm the caches

    elapsed, out = _run_hook(lore_root, project, timeout=60)

    assert json.loads(out)["systemMessage"]
    assert elapsed < BUDGET_S, f"session-start took {elapsed:.2f}s (budget {BUDGET_S}s)"


def test_session_start_prints_banner_when_wiki_remote_hangs(tmp_path: Path, reap_ssh: str) -> None:
    # The remote never answers: a fetch would block until git's own timeout.
    lore_root, project = _build_vault(tmp_path, f"{reap_ssh}\nsleep 60")

    elapsed, out = _run_hook(lore_root, project, timeout=15)

    assert json.loads(out)["systemMessage"]
    assert elapsed < BUDGET_S
