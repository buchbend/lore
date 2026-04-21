"""Shared harness for Curator C LLM passes.

Every pass in Phase B (adjacent-concept merge, auto-supersession,
orphan wikilink repair) goes through these helpers:

- ``validate_llm_response`` — schema-check an LLM tool-call response.
  Returns the validated dict or None (with a hook-event warning). Never
  raises.
- ``ProposalOnlyError`` — raised when a guarded pass attempts to mutate
  a pre-existing note during a proposal-only phase. Enforces the
  convention that v1 LLM passes only WRITE proposal markers, never flip
  end-state frontmatter on existing notes.
- ``git_status_porcelain`` — pre-flight check; True if the wiki repo
  has unmerged / conflict paths. C aborts on True to avoid writing into
  a mid-merge working tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class ProposalOnlyError(RuntimeError):
    """Raised when a proposal-only pass attempts to mutate an existing note."""


def resolve_tier_for_pass(
    wiki_path: Path,
    *,
    pass_name: str,
    preferred_tier: str = "high",
    lore_root: Path | None = None,
) -> str:
    """Return a real model ID for ``preferred_tier``, with high→middle
    degradation when ``models.high`` is ``"off"``.

    Emits a ``curator-c/high-tier-off`` warning event to hook-events
    every time a pass degrades (not once-per-run — merciless must-fix:
    once-per-sentinel added complexity without signal). The warning
    rides into CaptureState's ``simple_tier_fallback_active`` field via
    the warnings log (CaptureState sentinel layer — Plan A).
    """
    from lore_core.wiki_config import load_wiki_config

    cfg = load_wiki_config(wiki_path)
    models = cfg.models
    if preferred_tier == "high":
        if models.high == "off" or not models.high:
            # Degrade to middle + warn.
            if lore_root is not None:
                try:
                    from lore_core.hook_log import HookEventLogger
                    HookEventLogger(lore_root).emit(
                        event="curator-c",
                        outcome="high-tier-off",
                        error={
                            "pass": pass_name,
                            "message": (
                                f"Curator C {pass_name} running at middle tier "
                                "— high tier is off; expect coarser judgments."
                            ),
                        },
                    )
                except Exception:
                    pass
            return models.middle
        return models.high
    if preferred_tier == "middle":
        return models.middle
    return models.simple


def validate_llm_response(
    response: dict | None,
    *,
    required: dict[str, type | tuple[type, ...]],
    ranges: dict[str, tuple[float, float]] | None = None,
    lore_root: Path | None = None,
    pass_name: str = "unknown",
) -> dict | None:
    """Validate an LLM tool-call response. Returns the dict or None.

    ``required`` maps key → expected type (or tuple of types).
    ``ranges`` maps numeric key → (min, max) inclusive.

    On any violation (missing key, wrong type, out-of-range, None input,
    non-dict input) returns None and emits a hook-event warning when
    ``lore_root`` is provided. Never raises.
    """
    ranges = ranges or {}
    if not isinstance(response, dict):
        _warn(lore_root, pass_name, f"response is {type(response).__name__}, not dict")
        return None

    for key, expected_type in required.items():
        if key not in response:
            _warn(lore_root, pass_name, f"missing required key {key!r}")
            return None
        if not isinstance(response[key], expected_type):
            _warn(
                lore_root,
                pass_name,
                f"{key!r} has type {type(response[key]).__name__!r}; "
                f"expected {expected_type}",
            )
            return None

    for key, (lo, hi) in ranges.items():
        val = response.get(key)
        if val is None:
            continue
        try:
            if not (lo <= val <= hi):
                _warn(
                    lore_root,
                    pass_name,
                    f"{key!r}={val!r} outside range [{lo}, {hi}]",
                )
                return None
        except TypeError:
            _warn(lore_root, pass_name, f"{key!r}={val!r} is not comparable")
            return None

    return response


def _warn(lore_root: Path | None, pass_name: str, message: str) -> None:
    if lore_root is None:
        return
    try:
        from lore_core.hook_log import HookEventLogger
        HookEventLogger(lore_root).emit(
            event="curator-c",
            outcome="llm-response-invalid",
            error={"pass": pass_name, "message": message},
        )
    except Exception:
        pass


def git_status_porcelain(repo: Path) -> list[str]:
    """Return non-empty lines from `git status --porcelain` or [] on error.

    Used by the C integration skeleton as a pre-flight check — if the
    wiki repo has uncommitted-conflict paths (UU, AA, DD), C aborts
    before writing anything to avoid confusing the working tree.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0:
        return []
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def has_merge_conflicts(repo: Path) -> bool:
    """True if the repo has unmerged paths (UU, AA, DD, UA, UD, DU, AU)."""
    lines = git_status_porcelain(repo)
    for ln in lines:
        if len(ln) >= 2 and ln[0] in "UAD" and ln[1] in "UAD":
            return True
    return False
