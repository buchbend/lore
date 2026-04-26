"""Cross-integration launcher — read TOML integration registry, exec configured agent.

Reads `lib/lore_cli/integrations.d/<integration>.toml` (or any file dropped under
`$LORE_INTEGRATIONS_DIR`) to learn how to invoke each AI agent integration.
The launcher itself never touches Lore-internal state — it just turns a gathered
context block into the right argv/stdin shape for the chosen integration.

Used by `lore resume <topic> --launch <integration>` per the CLI-first thesis:
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

DEFAULT_INTEGRATIONS_DIR = Path(__file__).parent / "integrations.d"


@dataclass(frozen=True)
class IntegrationConfig:
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


def _integrations_dirs() -> list[Path]:
    """Search order: $LORE_INTEGRATIONS_DIR (user override), then bundled integrations.d/."""
    dirs: list[Path] = []
    env = os.environ.get("LORE_INTEGRATIONS_DIR")
    if env:
        dirs.append(Path(env).expanduser())
    dirs.append(DEFAULT_INTEGRATIONS_DIR)
    return [d for d in dirs if d.is_dir()]


def list_integrations() -> list[str]:
    """Return all integration names known across the search dirs (deduplicated)."""
    seen: set[str] = set()
    for d in _integrations_dirs():
        for f in sorted(d.glob("*.toml")):
            seen.add(f.stem)
    return sorted(seen)


def load_integration(name: str) -> IntegrationConfig | None:
    """Load `<name>.toml` from the first integrations dir that contains it."""
    for d in _integrations_dirs():
        path = d / f"{name}.toml"
        if path.is_file():
            data = tomllib.loads(path.read_text())
            return IntegrationConfig(
                name=name,
                binary=str(data.get("binary", "")),
                context_format=str(data.get("context_format", "stdin")),
                context_flag=str(data.get("context_flag", "")),
                extra_args=[str(a) for a in (data.get("extra_args") or [])],
                source_path=path,
            )
    return None


def build_invocation(
    integration: IntegrationConfig,
    context_text: str,
    user_message: str | None = None,
) -> tuple[list[str], str | None]:
    """Compute the (argv, stdin_text) for invoking the integration.

    Returns argv as a list and the text to pipe via stdin (or None when
    nothing should be piped).
    """
    argv: list[str] = [integration.binary, *integration.extra_args]
    stdin_text: str | None = None

    if integration.context_format == "flag":
        argv.extend([integration.context_flag, context_text])
        if user_message:
            argv.append(user_message)
    elif integration.context_format == "stdin":
        stdin_text = context_text
        if user_message:
            argv.append(user_message)
    elif integration.context_format in ("prepend", "append"):
        # Combine into the initial user message
        if user_message:
            combined = (
                f"{context_text}\n\n{user_message}"
                if integration.context_format == "prepend"
                else f"{user_message}\n\n{context_text}"
            )
        else:
            combined = context_text
        argv.append(combined)

    return argv, stdin_text


def launch(
    integration_name: str,
    context_text: str,
    user_message: str | None = None,
    *,
    dry_run: bool = False,
) -> int:
    """Resolve integration, build invocation, exec.

    Returns the exit status. On `dry_run`, prints the would-be argv +
    stdin shape to stderr and returns 0 without executing.
    """
    integration = load_integration(integration_name)
    if integration is None:
        print(
            f"lore: no integration '{integration_name}' "
            f"(known: {', '.join(list_integrations()) or 'none'})",
            file=sys.stderr,
        )
        return 2
    ok, msg = integration.is_valid()
    if not ok:
        print(
            f"lore: integration '{integration_name}' invalid "
            f"({integration.source_path}): {msg}",
            file=sys.stderr,
        )
        return 2

    argv, stdin_text = build_invocation(integration, context_text, user_message)

    if dry_run:
        print(f"would exec: {argv[0]} (+{len(argv) - 1} args)", file=sys.stderr)
        print(f"  source: {integration.source_path}", file=sys.stderr)
        print(f"  format: {integration.context_format}", file=sys.stderr)
        if stdin_text is not None:
            print(f"  stdin:  {len(stdin_text)} chars", file=sys.stderr)
        return 0

    if shutil.which(argv[0]) is None:
        print(
            f"lore: binary '{argv[0]}' not on PATH — install the integration or "
            f"override $LORE_INTEGRATIONS_DIR/{integration_name}.toml",
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
