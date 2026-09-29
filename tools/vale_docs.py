#!/usr/bin/env python3
"""Run Vale over the repo docs with the Lore config and fail on new errors (PRD 0015).

CI runs this step after pytest:

    python3 tools/vale_docs.py --config "$(lore style vale-config)"

The step counts error-level alerts only. Docs written before the step existed
hold long sentences (rule 6), so `tools/vale-docs-baseline.json` records the
error count per file. A file fails when its count rises above its baseline, and
a file without an entry fails on its first error. Fix a baselined file, then
run `--write-baseline` to lower its count; the count only goes down.

Stdlib only. Needs the `vale` binary on PATH.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "tools" / "vale-docs-baseline.json"


def _errors(alerts: list[dict]) -> list[dict]:
    return [a for a in alerts if a.get("Severity") == "error"]


def over_baseline(
    alerts_by_file: dict[str, list[dict]], baseline: dict[str, int]
) -> dict[str, list[dict]]:
    """The files whose error count rises above their baseline, with their errors."""
    over = {}
    for path, alerts in alerts_by_file.items():
        errors = _errors(alerts)
        if len(errors) > baseline.get(path, 0):
            over[path] = errors
    return over


def run_vale(config: str, paths: list[str]) -> dict[str, list[dict]]:
    result = subprocess.run(
        ["vale", "--output=JSON", "--minAlertLevel=error", "--config", config, *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        sys.exit(f"vale could not run (exit {result.returncode}):\n{result.stderr}")
    return json.loads(result.stdout or "{}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="Vale config (`lore style vale-config`).")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--write-baseline", action="store_true", help="Record the current error counts."
    )
    parser.add_argument("paths", nargs="*", default=["docs"])
    args = parser.parse_args(argv)

    alerts = run_vale(args.config, args.paths)
    if args.write_baseline:
        counts = {path: len(_errors(a)) for path, a in sorted(alerts.items()) if _errors(a)}
        args.baseline.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.baseline} ({len(counts)} file(s))")
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    over = over_baseline(alerts, baseline)
    for path, errors in sorted(over.items()):
        print(f"{path}: {len(errors)} error(s), baseline {baseline.get(path, 0)}")
        for error in errors:
            print(f"  {path}:{error['Line']}: {error['Check']}: {error['Message']}")
    checked = len(alerts)
    print(f"vale docs: {len(over)} file(s) over baseline, {checked} file(s) with alerts")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
