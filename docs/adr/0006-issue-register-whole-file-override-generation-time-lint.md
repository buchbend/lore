# ADR 0006: The issue register — per-wiki whole-file override, generation-time lint

- Status: Accepted
- Date: 2026-08-03
- Context: issue [#303](https://github.com/buchbend/lore/issues/303)
  (issue-register draft), evidence review of agent-written issues
  ccatobs/ops-db-api#265, #269–#271; implemented by PRD
  [0009](../prd/0009-issue-register.md), epic
  [#310](https://github.com/buchbend/lore/issues/310)

## Context

Agent-written issues read polished but are expensive to decode. An
international team converges on a shared subset of English; agents pull
toward a wider vocabulary, denser sentences, and context that existed only
in the session that wrote the issue. Issue #303 drafts a register for issue
text: ASD-STE100-derived prose rules, EARS acceptance criteria, a fixed
section skeleton.

Held against four recent agent-written issues, the failures are density and
structure — long em-dash sentences, ad-hoc sections, missing acceptance
criteria, assumed epic context — not vocabulary. The register must ship
per-team adjustable: teams differ in language needs, and Lore serves more
than one team.

Two populations write issues: lore-workflow skills (to-epic, seed-epic,
orchestrate-epic follow-ups) and foreign agents that only read a repo's
CLAUDE.md/AGENTS.md.

## Decision

1. **The register is a prose document, not config.** There are no merge
   semantics — merging a rules essay is ill-defined. Resolution is
   whole-file: `<wiki>/style/issue-register.md` if present, else the
   default shipped as lore package data (the packaged-templates
   precedent). The CLI is the only resolver, so the plugin carries no
   copy of the file. No per-repo layer.
2. **Wiki = team.** Per-team style rides the wiki boundary, the same
   boundary that already carries access control and shipping. Customizing
   means copying the default into the wiki and editing it.
3. **Lint runs at generation time, not in CI.** A skill drafts the issue
   body to a temp file, runs Vale on it (`vale --config` pointing at the
   plugin-shipped style, nothing written into user repos), fixes findings,
   then posts. Vale is PATH-detected; absence degrades to
   instruction-only enforcement and never blocks. No commit or PR hooks.
4. Skills resolve the register through one deterministic call
   (`lore style show issue-register`); no skill hardcodes register prose.
5. **The register reaches agents through the SessionStart injection, not
   through CLAUDE.md.** The banner gains one directive line: before
   writing or editing an issue or PR body, run
   `lore style show issue-register` and follow it. Push the pointer, pull
   the content — resolved at read time, so it cannot go stale. The
   register text keeps #303's paste-block section for environments
   Lore's hooks do not reach; a `lore style block` print command is
   deferred until such a consumer exists.
6. **All lore-workflow issue and PR filing routes through one in-context
   `file-issue` skill** — resolve register, draft per skeleton (single
   change, batch, or a caller-supplied template such as seed-epic's),
   Vale lint loop, post. Invoked via the Skill tool in the calling
   session, never as a subagent (a subagent per issue is the cost that
   made batching necessary in the first place). to-epic, seed-epic,
   orchestrate-epic follow-ups, and implement-issue call it instead of
   filing inline; it is also directly user-invocable. The funnel owns
   how to write and file — deciding *what* to capture stays with the
   caller.

## Consequences / Trade-offs

- Positive — customize = copy + edit. No cascade to debug, no question of
  which layer won.
- Positive — a filed issue was already linted; nobody reviews a posted
  issue back into compliance.
- Positive — single source of truth: the house-style section in
  `docs/conventions.md` becomes a pointer to the register.
- Negative — a wiki override forks the default; later improvements to the
  shipped default do not reach forked copies automatically.
- Negative — not all register rules are machine-checkable (no POS tagging
  in Vale); the register must state honestly which rules the linter
  covers.
- Negative — the register reaches only Lore-installed environments
  automatically. A consumer without Lore copies the paste-block section
  from the register file by hand.

## Alternatives considered

- **Layered merge (default → wiki → repo).** Rejected: merge semantics
  for prose do not exist; which sentence wins when the wiki rewrites a
  rule is unanswerable. Whole-file is one lookup.
- **CI lint (on commit or PR).** Rejected: the issue is already filed by
  then, and agents file issues from sessions, not commits. Generation
  time is where the fix is cheap and automatic.
- **LLM style judge.** Rejected: unenforceable and untestable — mirrors
  ADR 0004's rejection of model-judged certainty.
- **Auto-managed CLAUDE.md block.** Rejected: resurrects the deliberately
  retired managed-section writer and mutates a file teams treat as
  hand-written. The SessionStart directive covers the same population
  without touching any repo file.
- **Register instructions injected into each writing skill.** Rejected:
  four copies of the same resolve–draft–lint–post choreography; the
  funnel skill is the single implementation.
