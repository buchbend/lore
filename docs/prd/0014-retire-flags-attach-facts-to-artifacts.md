---
title: Retire flags; attach facts to repo artifacts; trim Lore to a context machine
status: draft
epic: TODO (file with /lore-workflow:to-epic once this draft is ratified)
repos:
  - buchbend/lore
---

# PRD 0014: Retire flags; attach facts to repo artifacts; trim Lore to a context machine

> Source of truth for this epic. Tracker: TODO.
> Decisions to record: an ADR that supersedes ADR 0007's crossing and ADR 0008
> (flag lands marked unreviewed) and ADR 0011 (review walk in a browser page).
> Design input: back-and-forth session 2026-09-16 (transcript ledger, buchbend/lore).

## Problem

The flag is the only session-to-wiki crossing (ADR 0007). Its write side
works. Its review side never ran.

- `lore status` on 2026-09-16 (host saiyajin, vault `~/git/vault`): wiki
  ccat holds 74 written flags, 79 pending, 0 accepted, 0 declined. Wiki
  private: 1 written, 1 pending, 0 reviewed.
- `projects/system-integration/system-integration.md` in ccat-knowledge
  holds 51 flag blocks in 458 lines. `projects/ccat-agent-workflow.md`
  holds 23 in 243 lines. A human no longer reads these notes.
- Sampled flags (certificate-authority.md, 2026-09-07 and 2026-09-10) are
  docs-versus-code gaps with verified refs. Each one is a bug report. A
  bug report is transient. It does not belong in a knowledge base.
- The "unreviewed" marker (ADR 0008) is a permanent label, because no
  review walk ever ran. A pulled flag carries no state, so a reader
  invents one and treats the text as current truth.
- The SessionStart hook takes 3.1–4.5 s wall time on 2026-09-16
  (`/usr/bin/time lore hook session-start`, 3 runs). The hook's own
  timed region reports 62–592 ms (spine, event `session-start`).
  `lore --help` alone takes 0.8 s.
- `lib/` holds 30,221 lines of Python across 8 packages. Modules for
  retired or parked surfaces still ship: `flag.py` (768),
  `flag_review_html.py` (447), `briefing_cmd.py` (559),
  `llm_client.py` (1015), `freshness.py` (504).

Every agent-written wiki surface Lore shipped has died (PRD 0007, 0012,
0013, now flags). Every artifact with a human loop survived: issues,
PRs, ADRs, PRDs, docs.

## Solution

Lore stops writing to the wiki. Agents attach facts to the repo artifact
that already has a reader and a lifecycle. Lore becomes a context
machine over those artifacts plus human-written wiki topic notes.

The crossing:

- A docs-versus-code gap, a trap, or a missing fact becomes an issue.
- A fact about an existing issue or PR becomes a comment on that issue or PR.
- A dead end and its reason becomes an issue closed as not-planned, with the reason in the close comment.
- A decision never lands lights-out. The agent drafts the ADR and opens a PR. A human merges. The same rule holds for a PRD and for any edit to a wiki topic note.
- A cross-repo fact becomes an issue on the org knowledge repo (ccat: `ccatobs/ccat-knowledge`).
- A request to add a fact to a wiki topic note becomes an issue on the knowledge repo. A human edits the note.

The agent posts eagerly during the session. At session end the agent
reports every artifact it created or commented on, so the developer can
triage. Nothing blocks. Nothing asks.

Issue state replaces review: open means under triage, closed-fixed means
done, closed-not-planned means dead end. The developer maintains that
state during normal work.

Lore's remaining jobs. Lore is a context machine and a token-saving
machine. Deterministic lookup first; an LLM call only where a lookup
cannot answer.

- Know, per repo and per org, where ADRs, PRDs, docs and issues live, and the team's language for them (glossary, writing rules, issue register).
- Mirror issue and PR text locally, so an agent reads artifacts from disk and search instead of calling `gh` each time. Duplication is the point.
- Inject the human-written wiki topic notes at session start, scoped to the repo.
- Enforce look-first retrieval: tier resolve, context pack, codemap, artifact mirror, then exploration.
- Notice its own lookup gaps: when an agent spends many turns finding a fact a lookup should have served, the agent files an issue on the Lore repo. Opt-in per user, default off.
- Capture transcripts and the transcript ledger (personal layer, ADR 0009). Unchanged.

## Implementation decisions

- **Crossing** — the `lore-workflow:file-issue` skill and `gh` are the write path. Lore ships no new write verb. The session directive replaces "file a flag" with "attach the fact to its artifact". The MCP `lore_flag` tool is removed.
- **Decision gate** — the workflow skills (domain-modeling, to-epic, orchestrate-epic) open a PR for every ADR, PRD or topic-note edit. No skill commits such a file to `main` and no skill edits a wiki note in place. The session directive states the rule.
- **Artifact mirror** — one directory `$LORE_CACHE/artifacts/<owner>/<repo>/{issues,pulls}/<number>.md`, each file holding frontmatter (number, state, title, labels, updated_at, url) and the body plus comments. `lore artifacts sync <repo>` pulls with `gh ... list --state all --json ... --search "updated:>=<last_sync>"`, so a refresh costs one call per repo. Sync runs in the background after SessionStart, never on the hook's critical path. The mirror feeds the existing FTS index, so `lore_search` returns issues and PRs beside wiki notes. `lore_read` serves a mirrored artifact by `owner/repo#number`. The mirror is disposable: delete it and the next sync rebuilds it. Scope: the attached repo plus the org knowledge repo named in the wiki config; further repos only when listed. Depth: body plus comments for open items, body only for closed items.
- **Lookup-gap feedback** — config key `feedback.lore_issues: false`. When true, the orient, implement-issue and tdd skills carry one session-end check: if the agent needed repeated search or exploration to locate a fact the codemap, context pack or artifact mirror should have served, the agent files one issue on `buchbend/lore` naming the fact, the tools tried and the turn count. When false, the skills skip the check. Never forced on. The trigger is the agent's own judgement at session end; no spine counter. A counter is the upgrade path if judgement proves too eager or too quiet.
- **Session-end report** — the session-end hook prints the artifacts the session created or commented on. Source: the transcript ledger linkage block (issues, PRs). Zero LLM.
- **Dedup** — before filing, the agent searches open issues by the refs behind the fact. The file-issue skill carries that step. No Lore-side index.
- **Sensitivity gate** — the publish gate keeps running on outbound issue and comment text. Fail closed, quarantine withheld text. Scope narrows from flag text to artifact text.
- **Non-GitHub backends** — out of scope. The artifact location stays configurable in principle; only GitHub ships.
- **Flag stock** — one migration command lists every flag block in every wiki, files each one as an issue on the wiki's repo with the origin line as source, then removes the block. Dry-run default, `--apply` executes. Author decides per wiki.
- **Retirement** — remove: `flag.py`, `flag_cmd.py`, `flag_review_html.py`, `flag_metrics.py`, the pending-flag chip and count in `session_start.py`, the flag counters in `status_cmd.py` and `trace_cmd.py`, the flag glossary section in `CONTEXT.md`, the flag directive lines.
- **Code sweep** — after retirement, a subagent pass over `lib/` lists every module, verb and config key that serves no retained job. The pass produces a report, not a diff. The owner gates deletion (HITL, same discipline as PRD 0011).
- **Hook speed** — target: `lore hook session-start` under 1 s wall time. Measure first: import cost vs. git auto-pull vs. transcript sync. The measurement is a slice on its own; the fix follows the measurement.
- **Language enforcement** — TODO: the owner wants a separate review of the Vale mechanism before any change. Do not touch in this epic.

## Testing decisions

- Session-end report: ledger entry with linkage → rendered report lines. Deterministic.
- Migration: fixture vault with flag blocks → issue payloads and cleaned notes; dry-run plan equals applied result.
- Retirement: tests for removed paths are deleted with the code. Retained suites (capture, ledger, search, context pack, codemap, style) are the regression net.
- Artifact mirror: fixture `gh` output → mirror files; second sync with unchanged `updated_at` → no writes; FTS index contains the mirrored refs. The `gh` binary is stubbed; no network in tests.
- Decision gate: skill text is prose, so the check is a grep test over `lore-workflow/skills/*/SKILL.md` asserting the PR rule is present in each decision-writing skill.
- Hook speed: one timing test with a threshold, marked slow, run in CI on Linux only.

## Acceptance criteria

- When an agent finds a docs-versus-code gap, the agent shall file an issue on the owning repo through the file-issue skill.
- When a session ends, the session-end hook shall print every issue and PR the session created or commented on.
- When the migration runs with `--apply`, Lore shall leave no `<!-- lore:flag` block in any wiki.
- When `lore status` runs after retirement, the output shall show no flag section.
- When `lore hook session-start` runs on a warm cache, the hook shall finish in under 1 s wall time.
- When an agent records a decision, the agent shall open a PR holding the ADR draft and shall not merge it.
- When `lore artifacts sync <repo>` runs twice with no upstream change, the second run shall make one `gh` call and write no file.
- When `lore_search` runs after a sync, results shall include mirrored issues and PRs with their `owner/repo#number` ref.
- While `feedback.lore_issues` is false, no skill shall file an issue on the Lore repo.
- When the code sweep completes, the report shall list each candidate module with its last caller (code, skill or template), per the prose-caller rule.

## Out of scope

- Non-GitHub artifact backends.
- The Vale and writing-rules enforcement mechanism.
- Briefings, journals, inbox, freshness verdicts, hygiene: unchanged here; the code sweep may nominate them.
- Any LLM harvest of the flag stock beyond the one-to-one issue migration.

## Glossary changes

- Retire: flag, crossing (as defined for flags), origin line, unreviewed marker, review walk.
- Add: artifact mirror (the local copy of issue and PR text under `$LORE_CACHE`), lookup gap (a fact an agent found by exploration that a deterministic lookup should have served).
- TODO: a term for "the per-repo record of where ADRs, PRDs, docs and issues live". Candidate: reuse "codemap" if it already covers docs, else add one term in its own commit. Settle in grilling; issue #417 may absorb it as the regime registry.
