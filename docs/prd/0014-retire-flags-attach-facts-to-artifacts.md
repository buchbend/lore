---
title: Retire flags; attach facts to repo artifacts; trim Lore to a context machine
status: draft
epic: TODO (file with /lore-workflow:to-epic once this draft is ratified)
repos:
  - buchbend/lore
---

# PRD 0014: Retire flags; attach facts to repo artifacts; trim Lore to a context machine

> Source of truth for this epic. Tracker: TODO.
> Decisions to record: one ADR for the new crossing, superseding ADR 0007, ADR 0008
> (flag lands marked unreviewed) and ADR 0011 (review walk in a browser page); one
> new ADR for federated search over the wiki index and GitHub.
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

- A docs-versus-code gap, a trap, or a missing fact becomes an issue. A trivial fix close to the session's work is the exception. The agent fixes it in the session's branch and names it in the PR body.
- A fact about an existing issue or PR becomes a comment on that issue or PR.
- A dead end and its reason becomes an issue closed as not-planned, with the reason in the close comment.
- A decision never lands lights-out. One exception: an ADR or PRD negotiated with the user in a grilling or domain-modeling session. Those skills write the file directly, because the user was in the loop. Any ADR an agent drafts outside such a session goes through a PR. A human merges.
- A cross-repo fact becomes an issue on the org knowledge repo (ccat: `ccatobs/ccat-knowledge`).
- A fact for a wiki topic note becomes a PR on the wiki repo with the edit. A human merges. An issue would need the human to drive the edit; a PR is the most lights-out path that still leaves the human in control.

The agent posts eagerly during the session. At session end the agent
lists every artifact it created or commented on in its final message,
so the developer can triage. The next SessionStart does not replay the
list: the artifacts are discoverable through the tracker. Nothing
blocks. Nothing asks.

Issue state replaces review: open means under triage, closed-fixed means
done, closed-not-planned means dead end. The developer maintains that
state during normal work.

Lore's remaining jobs. Lore is a context machine and a token-saving
machine. Deterministic lookup first; an LLM call only where a lookup
cannot answer.

- Know, per repo and per org, where ADRs, PRDs, docs and issues live, and the team's language for them (glossary and writing rules).
- Keep a local copy of issue and PR text, so an agent reads artifacts from disk and search instead of calling `gh` each time. Duplication is the point.
- Inject the human-written wiki topic notes at session start, scoped to the repo.
- Enforce look-first retrieval: tier resolve, context pack, codemap, federated search, then exploration.
- Notice its own lookup gaps. When an agent spends many turns finding a fact a lookup should have served, the agent files an issue on the Lore repo. Opt-in per user, default off.
- Capture transcripts and the transcript ledger (personal layer, ADR 0009). Unchanged.

## Implementation decisions

- **Crossing** — the `lore-workflow:file-issue` skill and `gh` are the write path. Lore ships no new write verb. The session directive replaces "file a flag" with "attach the fact to its artifact". The MCP `lore_flag` tool is removed.
- **Agent-filed marker** — every issue an agent opens carries the label `agent-filed`, created on demand per repo. Every comment an agent posts opens with a line naming itself as agent-filed. An agent may comment on any issue or PR in the org. An agent closes only issues it opened itself (dead ends, closed as not-planned); it never changes the state of a human's issue.
- **Decision gate** — grilling and domain-modeling keep writing ADRs and PRDs directly; the user negotiated them. Every other path to an ADR, a PRD or a wiki topic note is a PR. The session directive states the rule. The implement-issue, tdd and orchestrate-epic skills carry it.
- **Federated search** — `lore_search` runs the wiki FTS query and one `gh search issues` call. The `gh` call targets the attached repo and the wiki's own git remote, with `--json number,title,state,url,updatedAt --limit 5`. The result holds two sections: wiki hits, then artifact hits. No merged ranking; GitHub ranks its section. No storage, no sync: when `gh` fails or is offline the tool returns the wiki section alone and says so. GitHub search covers titles, bodies and comments. The search API allows 30 calls a minute; the query log shows about 20 lookups a day. Before an agent comments, closes, merges or polls, it reads live through `gh`; search is for finding only. The context pack adds `body` to the fields it already fetches for the session's focus issues, so the common case needs no search at all.
- **Local copy, rejected** — a markdown copy of issue and PR text under `$LORE_CACHE` was reviewed twice on 2026-09-16. Reading a known artifact from disk saves no tokens (issue #417: 3360 bytes via `gh`, 3151 cached). The one-call incremental sync loses comment threads past 100 entries and cannot see deletions. Lookup-gap feedback reporting offline work or rate limits reopens the question. The spec for that day, in five parts. Full list per repo diffed on update time. Re-fetch at exactly 100 comments. Sync time in frontmatter. Distinct index type with a capped share. Secret scanners on ingest.
- **Lookup-gap feedback** — root config keys `feedback.lore_issues: false` and `feedback.lore_issues_repo: buchbend/lore`. When true, the orient, implement-issue and tdd skills carry one session-end check. The check asks whether the agent needed repeated search or exploration for a fact the codemap, context pack or federated search should have served. If so, the agent files one issue on `buchbend/lore` naming the fact, the tools tried and the turn count. When false, the skills skip the check. Never forced on. The trigger is the agent's own judgement at session end; no spine counter. A counter is the upgrade path if judgement proves too eager or too quiet.
- **Session-end report** — the agent's final message lists the artifacts the session created or commented on. The skills carry the instruction; the ledger linkage block (issues, PRs) is the source the agent checks against. No hook output: SessionEnd stdout never reaches the user.
- **Dedup** — before filing, the agent searches open issues by the refs behind the fact. The file-issue skill carries that step. No Lore-side index.
- **Sensitivity gate** — the publish gate keeps running on outbound issue and comment text. Fail closed, quarantine withheld text. Scope narrows from flag text to artifact text.
- **Non-GitHub backends** — out of scope. The artifact location stays configurable in principle; only GitHub ships.
- **Flag stock** — dropped, not migrated. One command removes every flag block from every wiki note and leaves the rest of the note untouched. Dry-run default, `--apply` executes. Git history keeps the blocks. The 74 ccat flags were bug reports at the time; a fresh start costs less than 74 stale issues.
- **Retirement** — remove `flag.py`, `flag_cmd.py`, `flag_review_html.py` and `flag_metrics.py`. Remove the pending-flag chip and count in `session_start.py`. Remove the flag counters in `status_cmd.py` and `trace_cmd.py`. Remove the flag glossary section in `CONTEXT.md` and the flag directive lines.
- **Known-dead now** — briefings (`briefing_cmd.py`, its config keys, its sinks) and the LLM client (`llm_client.py` and every backend key) leave in the retirement slice. Nothing retained calls an LLM.
- **Code sweep** — after retirement, a subagent pass over `lib/` lists every module, verb and config key that serves no retained job. The pass produces a report, not a diff. The owner gates deletion (HITL, same discipline as PRD 0011).
- **Hook speed** — target: `lore hook session-start` under 1 s wall time. Measure first: import cost vs. git auto-pull vs. transcript sync. The measurement is a slice on its own; the fix follows the measurement. The gate is soft: a timing test marked slow with a 2 s CI threshold, not a hard failure at 1 s.
- **Language enforcement** — TODO: the owner wants a separate review of the Vale mechanism before any change. Do not touch in this epic.

## Testing decisions

- Session-end report: prose in skills, so a grep test over the skill files asserts the instruction is present.
- Flag removal: fixture vault with flag blocks → cleaned notes with every non-flag line intact; dry-run plan equals applied result.
- Retirement: tests for removed paths are deleted with the code. Retained suites (capture, ledger, search, context pack, codemap, style) are the regression net.
- Federated search: stubbed `gh` output → two-section result; `gh` failure → wiki section plus a stated omission. No network in tests.
- Decision gate: skill text is prose, so the check is a grep test over `lore-workflow/skills/*/SKILL.md` asserting the PR rule is present in each decision-writing skill.
- Hook speed: one timing test with a threshold, marked slow, run in CI on Linux only.

## Acceptance criteria

- When an agent finds a docs-versus-code gap, the agent shall file an issue on the owning repo through the file-issue skill.
- When a session ends, the agent's final message shall list every issue and PR the session created or commented on.
- When an agent opens an issue, the issue shall carry the label `agent-filed`.
- When an agent closes an issue, the issue shall be one the agent opened.
- When the flag removal runs with `--apply`, Lore shall leave no `<!-- lore:flag` block in any wiki.
- When `lore status` runs after retirement, the output shall show no flag section.
- When `lore hook session-start` runs on a warm cache in CI, the hook shall finish in under 2 s wall time.
- When an agent records a decision, the agent shall open a PR holding the ADR draft and shall not merge it.
- When `lore_search` runs with `gh` available, the result shall hold a wiki section and an artifact section with `owner/repo#number` refs.
- If `gh` fails, then `lore_search` shall return the wiki section and name the omission.
- When the context pack lists a focus issue, the pack shall include the issue body.
- While `feedback.lore_issues` is false, no skill shall file an issue on the Lore repo.
- When the code sweep completes, the report shall list each candidate module with its last caller (code, skill or template), per the prose-caller rule.

## Out of scope

- Non-GitHub artifact backends.
- The Vale and writing-rules enforcement mechanism.
- Journals, inbox, freshness verdicts, hygiene: unchanged here; the code sweep may nominate them.
- Any migration or LLM harvest of the flag stock.

## Glossary changes

- Retire: flag, crossing (as defined for flags), origin line, unreviewed marker, review walk.
- Add: lookup gap, a fact an agent found by exploration that a deterministic lookup should have served.
- Add: agent-filed, the label and the opening line marking an artifact an agent created.
- No new term for "the per-repo record of where ADRs, PRDs, docs and issues live". The PRD says "codemap plus writing rules". Issue #417 owns the question.
