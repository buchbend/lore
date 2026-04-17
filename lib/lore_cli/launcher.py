"""Cross-host launcher — read TOML host registry, exec configured agent.

Reads `lib/lore_cli/hosts.d/<host>.toml` (or any file dropped under
`$LORE_HOSTS_DIR`) to learn how to invoke each agent host. The launcher
itself never touches Lore-internal state — it just turns a gathered
context block into the right argv/stdin shape for the chosen host.

Used by `lore resume <topic> --launch <host>` per the CLI-first thesis:
the CLI gathers, then *becomes* the agent process so cold-start is
warm-start without any in-session token spend on retrieval.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOSTS_DIR = Path(__file__).parent / "hosts.d"


@dataclass(frozen=True)
class HostConfig:
    name: str
    binary: str
    context_format: str  # flag | stdin | append | prepend
    context_flag: str
    extra_args: list[str]
    source_path: Path

    def is_valid(self) -> tuple[bool, str]:
        if self.context_format not in ("flag", "stdin", "append", "prepend"):
            return False, f"unknown context_format: {self.context_format}"
        if self.context_format == "flag" and not self.context_flag:
            return False, "context_format=flag requires context_flag"
        if not self.binary:
            return False, "binary not set"
        return True, ""


def _hosts_dirs() -> list[Path]:
    """Search order: $LORE_HOSTS_DIR (user override), then bundled hosts.d/."""
    dirs: list[Path] = []
    env = os.environ.get("LORE_HOSTS_DIR")
    if env:
        dirs.append(Path(env).expanduser())
    dirs.append(DEFAULT_HOSTS_DIR)
    return [d for d in dirs if d.is_dir()]


def list_hosts() -> list[str]:
    """Return all host names known across the search dirs (deduplicated)."""
    seen: set[str] = set()
    for d in _hosts_dirs():
        for f in sorted(d.glob("*.toml")):
            seen.add(f.stem)
    return sorted(seen)


def load_host(name: str) -> HostConfig | None:
    """Load `<name>.toml` from the first hosts dir that contains it."""
    for d in _hosts_dirs():
        path = d / f"{name}.toml"
        if path.is_file():
            data = tomllib.loads(path.read_text())
            return HostConfig(
                name=name,
                binary=str(data.get("binary", "")),
                context_format=str(data.get("context_format", "stdin")),
                context_flag=str(data.get("context_flag", "")),
                extra_args=[str(a) for a in (data.get("extra_args") or [])],
                source_path=path,
            )
    return None


def build_invocation(
    host: HostConfig,
    context_text: str,
    user_message: str | None = None,
) -> tuple[list[str], str | None]:
    """Compute the (argv, stdin_text) for invoking the host.

    Returns argv as a list and the text to pipe via stdin (or None when
    nothing should be piped).
    """
    argv: list[str] = [host.binary, *host.extra_args]
    stdin_text: str | None = None

    if host.context_format == "flag":
        argv.extend([host.context_flag, context_text])
        if user_message:
            argv.append(user_message)
    elif host.context_format == "stdin":
        stdin_text = context_text
        if user_message:
            argv.append(user_message)
    elif host.context_format in ("prepend", "append"):
        # Combine into the initial user message
        if user_message:
            combined = (
                f"{context_text}\n\n{user_message}"
                if host.context_format == "prepend"
                else f"{user_message}\n\n{context_text}"
            )
        else:
            combined = context_text
        argv.append(combined)

    return argv, stdin_text


def launch(
    host_name: str,
    context_text: str,
    user_message: str | None = None,
    *,
    dry_run: bool = False,
) -> int:
    """Resolve host, build invocation, exec.

    Returns the exit status. On `dry_run`, prints the would-be argv +
    stdin shape to stderr and returns 0 without executing.
    """
    host = load_host(host_name)
    if host is None:
        print(
            f"lore: no host '{host_name}' (known: {', '.join(list_hosts()) or 'none'})",
            file=sys.stderr,
        )
        return 2
    ok, msg = host.is_valid()
    if not ok:
        print(f"lore: host '{host_name}' invalid ({host.source_path}): {msg}", file=sys.stderr)
        return 2

    argv, stdin_text = build_invocation(host, context_text, user_message)

    if dry_run:
        print(f"would exec: {argv[0]} (+{len(argv) - 1} args)", file=sys.stderr)
        print(f"  source: {host.source_path}", file=sys.stderr)
        print(f"  format: {host.context_format}", file=sys.stderr)
        if stdin_text is not None:
            print(f"  stdin:  {len(stdin_text)} chars", file=sys.stderr)
        return 0

    if shutil.which(argv[0]) is None:
        print(
            f"lore: binary '{argv[0]}' not on PATH — install the host or "
            f"override $LORE_HOSTS_DIR/{host_name}.toml",
            file=sys.stderr,
        )
        return 127

    if stdin_text is not None:
        result = subprocess.run(argv, input=stdin_text, text=True, check=False)
        return result.returncode
    # No stdin — exec replaces this process so the user gets a clean
    # interactive session.
    os.execvp(argv[0], argv)
    return 0  # unreachable
