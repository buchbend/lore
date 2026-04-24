"""Thin wrapper for fire-and-forget subprocesses that captures exit metadata.

Invoked by ``_spawn_detached`` instead of the curator command directly.
Writes a JSON sidecar with pid, start/end timestamps, and exit code.

Usage::

    python -m lore_cli._proc_wrapper /path/to/meta.json -- cmd arg1 arg2
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        print(f"usage: {sys.argv[0]} <meta.json> -- <cmd> [args...]", file=sys.stderr)
        return 2

    meta_path = Path(sys.argv[1])
    cmd = sys.argv[3:]
    pid = os.getpid()
    start_ts = time.time()
    meta = {"pid": pid, "start_ts": start_ts, "cmd": cmd, "exit_code": None}

    try:
        meta_path.write_text(json.dumps(meta))
    except OSError:
        pass

    try:
        rc = subprocess.call(cmd)
    except Exception:
        rc = 1

    meta["exit_code"] = rc
    meta["end_ts"] = time.time()
    try:
        meta_path.write_text(json.dumps(meta))
    except OSError:
        pass

    return rc


if __name__ == "__main__":
    sys.exit(main())
