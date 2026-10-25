"""Stage tags: which workflow step a subagent or a session works for.

A lore-workflow delegation point starts the spawn's ``description`` with a
stage tag, for example ``[build:review]``. A session that a skill starts with
its own prompt (a teammate) opens that prompt with the tag instead. A tag may
carry the epic or issue it works for: ``[build:implement] owner/repo#510``.

``lore_core.token_trace`` reads the tags, so token statistics can group by
stage and by epic or issue. ``tests/test_workflow_plugin_structural.py``
checks that the skills name only the tags listed here.
"""

from __future__ import annotations

import re

#: Every stage tag the lore-workflow skills use, with the step it names.
STAGES: dict[str, str] = {
    "build:implement": "teammate that writes an epic feature",
    "build:review": "per-PR review",
    "build:epic-review": "whole-epic review in the epic tail",
    "build:suite": "background suite after a loop round",
    "build:advisory": "advisory pass of the loop wrap-up",
    "orient:explore": "explorer of the orient fan-out",
    "orient:named": "named agent in orient",
    "triage:security": "security lens of triage",
    "triage:architect": "architect lens of triage",
    "triage:ops": "ops lens of triage",
    "triage:other": "triage theme outside the default lenses",
}

_TAG = re.compile(r"^\s*\[([a-z][a-z0-9-]*:[a-z][a-z0-9-]*)\]")
# owner/repo#n, or #n alone. A bare number without # is not a reference.
_REF = re.compile(r"(?:(?<![\w.-])([\w.-]+/[\w.-]+))?#(\d+)\b")


def parse_tag(text: object) -> tuple[str | None, str | None]:
    """``(stage, reference)`` from the start of ``text``; ``(None, None)`` without a tag.

    The reference is the first ``owner/repo#n`` or ``#n`` on the tag's line.
    """
    if not isinstance(text, str):
        return None, None
    match = _TAG.match(text)
    if not match:
        return None, None
    line = text[match.end() :].split("\n", 1)[0]
    return match.group(1), parse_ref(line)


def parse_ref(text: object) -> str | None:
    """The first ``owner/repo#n`` or ``#n`` in ``text``, as written."""
    if not isinstance(text, str):
        return None
    match = _REF.search(text)
    if not match:
        return None
    repo, number = match.groups()
    return f"{repo}#{number}" if repo else f"#{number}"


def ref_label(ref: str, cwd: str = "") -> str:
    """Group key for a reference: ``repo#n``, with the repo taken from ``cwd`` for a bare ``#n``.

    ``buchbend/lore#510`` and ``#510`` typed in a checkout named ``lore``
    both become ``lore#510``.
    """
    repo, _, number = ref.rpartition("#")
    if repo:
        return f"{repo.rsplit('/', 1)[-1]}#{number}"
    name = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else ""
    return f"{name}#{number}" if name else f"#{number}"
