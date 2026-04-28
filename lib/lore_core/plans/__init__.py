"""Plan-as-Lore-core: capture, store, and surface multi-step plans.

Plans are first-class notes (``type: plan``) at
``wiki/<wiki>/plans/<slug>.md`` with stable step anchors ``s1..sN``.
The ``step_status`` frontmatter dict is the single authoritative signal
for "where are we" — set/advance via :mod:`step_status`, read directly
by SessionStart with one file open.

Submodules:

* :mod:`types` — ``StructuredPlan``, ``PlanStep``, ``StepStatus`` enum.
* :mod:`parser` — markdown → ``StructuredPlan``; permissive payload
  shape, fenced-code stripping, three-mode step detection.
* :mod:`writer` — ``StructuredPlan`` → wiki note; per-slug flock,
  source_hash dedup, slug collision suffix, idempotent re-capture
  preserving the user-owned whitelist.
* :mod:`registry` — ``list_active(wiki, repo)`` + incoming-wikilink scan.
* :mod:`step_status` — atomic per-step status mutation.
* :mod:`breadcrumbs` — informational scan of recent commits + session
  wikilinks for plan-step references; SessionStart polish, never
  authoritative.
"""
