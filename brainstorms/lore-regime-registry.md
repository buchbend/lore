# Regime registry and the lab-notebook regime — divergent brief
state: open (tracker: buchbend/lore#417)
updated: 2026-09-16 (opened from the flag-retirement session; no slice exported)

Predecessor: `lore-session-notes-worth.md` (2026-08-04, settled that Lore is
the funnel, not the holder). Sibling: PRD 0014 (retire flags, attach facts to
artifacts). This brief asks the question PRD 0014 leaves open: **what is
Lore's opinion on where each kind of knowledge lives, and which kinds have no
home yet?**

## Framing

Software work has a home for almost everything: decision → ADR, requirement →
PRD, task → issue, change → PR, how-to → docs, truth → code. PRD 0014 routes
every agent-found fact to one of these. Two kinds still lack a home:

- **Messy lab work.** An observation log, a calibration run, "we tried X on
  the FFTS host and got Y". Not a decision, not a bug, not a how-to yet.
- **Processes and living reference.** What the CCAT knowledge wiki holds
  today, human-processed: who runs what, how a thing is done here, what the
  observatory-wide context is.

Observed 2026-09-16 in `orgs/data-center-pmo`: the project-office agent
already runs a hand-written version of the answer. Its README carries a
regimes table (per directory: what it holds, who writes, what trigger, how
reviewed). Its playbook restates Lore's rules on its own: write only in one
lane, propose everything else as a branch merged on approval, freshness as
prime directive, nudge memory as JSON state, read-only mirrors of sibling
repos. It also still lists "lore session notes" as a source, retired in Lore
0.70.0. A consumer restated Lore's conventions because Lore never offered
them as an importable thing, and the copy drifted.

## The idea

**Regime registry.** Lore's opinion, as one deterministic table a repo or an
org declares and Lore reads at session start:

| kind | location | writer | trigger | review |
|---|---|---|---|---|
| decision | `docs/adr/` | agent drafts | a choice with alternatives | PR, human merges |
| requirement | `docs/prd/` | agent drafts | an epic | PR, human merges |
| task / gap / trap | issue tracker | agent files | found during work | issue state |
| dead end | issue closed not-planned | agent files | tried and failed | close comment |
| meeting | `office/meetings/` | agent or human | a meeting happened | none, dated, append-only |
| lab entry | TODO | deliberate, named author | work happened | none, dated, append-only |
| process / reference | wiki topic note | human | at defined triggers | PR or issue on knowledge repo |
| agreement | `agreements/` | human | signature event | versioned, never by agent |

`.lore.yml` already holds a half of this (location, template, hint per
surface). The registry generalises it and adds writer, trigger and review.
Lore renders it into the session directive. A consumer playbook imports it
instead of restating it.

**Lab-notebook regime.** The one row with no location. Shape borrowed from
`office/reports/`: dated entries, append-only, never edited, a named author
who writes deliberately at the moment of the work. Distillation into a topic
note happens later, through a proposal PR. The difference from the retired
session note is the deliberate act with a named author. Lights-out
extraction died; a deliberate dated entry is a different thing.

## Open questions

- Is the registry per repo, per org, or both with org as the default and repo as the override?
- Does the lab entry live in the code repo, in the knowledge repo, or in the personal vault with a crossing?
- Who distils lab entries into a topic note, and on what trigger? Human at review, or agent proposal PR on a cadence?
- Does the PMO office import the registry, or does the registry only inform Lore's own directive?
- Is "regime" the right term for the glossary, or does "surface" (already in `.lore.yml`) cover it?

## Assumptions

- PRD 0014 lands first. This brief does not touch flags.
- No LLM in the registry path. It is a table Lore reads and renders.
- Astronomy lab work is the first real lab-notebook user (ccat, science wiki).

## Sources

- `orgs/data-center-pmo/README.md` — regimes table
- `orgs/data-center-pmo/office/PLAYBOOK.md` §2 hard rules, §3 sources of truth
- `orgs/data-center-pmo/AGENTS.md` — change regimes
- PRD 0001 (lab-notebook notes, superseded direction; shape still relevant)
- PRD 0014 (retire flags; attach facts to artifacts)
- memory note `capture-retrieve-never-ask` (`.lore.yml` registry with location + from_surface + template + hint)
