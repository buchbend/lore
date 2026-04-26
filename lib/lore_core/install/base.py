"""Action / ApplyResult / InstallContext / LegacyArtifact dataclasses.

Action is a pure data container — no closures, no module-local state.
The dispatcher in `install_cmd.py` knows how to `preview_action()` and
`execute_action()` (in `_helpers.py`) by switching on `Action.kind`.

This shape is what makes `lore install plan --json` trivially
serialisable: dump the list of Actions to JSON, ship to an editor
extension, the consumer reconstructs without needing to import any
Lore Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Action.kind values
KIND_NEW = "new"          # write a file Lore owns end-to-end
KIND_MERGE = "merge"      # mutate a key range inside a shared file
KIND_REPLACE = "replace"  # would clobber existing user content (per-action prompt)
KIND_RUN = "run"          # subprocess invocation (claude plugin install, pipx, …)
KIND_CHECK = "check"      # read-only assertion (binary on PATH, etc.)
KIND_DELETE = "delete"    # remove a key from JSON / managed block from markdown
                          # (forward action with removal semantics — uninstall path)

VALID_KINDS = frozenset(
    {KIND_NEW, KIND_MERGE, KIND_REPLACE, KIND_RUN, KIND_CHECK, KIND_DELETE}
)

# Action.on_failure values
ON_FAILURE_ABORT_INTEGRATION = "abort_integration"
ON_FAILURE_CONTINUE = "continue"

VALID_ON_FAILURE = frozenset({ON_FAILURE_ABORT_INTEGRATION, ON_FAILURE_CONTINUE})


@dataclass
class Action:
    """One discrete unit of installer work.

    `payload` carries everything `_helpers.execute_action` needs to do
    the work — structured per-kind:

      kind=new      payload = {"path": str, "content": str}
      kind=merge    payload = {"path": str, "key_path": list[str],
                               "value": Any, "schema_version": str}
      kind=replace  payload = {"path": str, "key_path": list[str],
                               "new_value": Any, "old_value": Any,
                               "reason": str}
      kind=run      payload = {"argv": list[str], "timeout": int,
                               "fallback_message": str | None}
      kind=check    payload = {"check": str, "args": dict,
                               "fail_message": str}
      kind=delete   payload = {"path": str,
                               "key_path": list[str] | None}
                    # key_path None  → delete the file (or managed block
                    #                  if file uses lore-managed markers)
                    # key_path list  → delete that key from JSON
    """

    kind: str
    description: str
    target: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    on_failure: str = ON_FAILURE_ABORT_INTEGRATION

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(
                f"Action.kind must be one of {sorted(VALID_KINDS)}, "
                f"got {self.kind!r}"
            )
        if self.on_failure not in VALID_ON_FAILURE:
            raise ValueError(
                f"Action.on_failure must be one of {sorted(VALID_ON_FAILURE)}, "
                f"got {self.on_failure!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for `lore install plan --json`."""
        return {
            "kind": self.kind,
            "description": self.description,
            "target": self.target,
            "summary": self.summary,
            "payload": self.payload,
            "on_failure": self.on_failure,
        }


@dataclass
class ApplyResult:
    """Outcome of executing one Action."""

    ok: bool
    diff: str | None = None
    error: str | None = None
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "diff": self.diff,
            "error": self.error,
            "rolled_back": self.rolled_back,
        }


@dataclass
class LegacyArtifact:
    """An install.sh-era artifact an integration module's `detect_legacy` found."""

    kind: str          # "skill_symlink" | "agent_symlink" | "hook_entry" |
                       # "permission_rule" | "env_entry"
    path: str          # human-readable location
    detail: str        # specific item (e.g. the symlink name, the rule string)


@dataclass
class InstallContext:
    """Per-run inputs passed to every integration module's plan/detect_legacy."""

    lore_repo: Path | None = None       # for editable / dev installs
    force: bool = False                  # override legacy-artifact refusal
    dry_run: bool = False                # check-only mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "lore_repo": str(self.lore_repo) if self.lore_repo else None,
            "force": self.force,
            "dry_run": self.dry_run,
        }
