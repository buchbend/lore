---
name: lore-workflow:orient
description: The first step of a piece of work — the session does its own homework, then
  reflects its understanding back for confirmation before any planning or grilling. A light
  mode ("brief me") pulls only the context pack and code map and hands one change straight to
  build. Use at the start of a task, when the user says "orient", "get oriented", "brief me",
  "what's your understanding", or wants the session to reflect back before planning.
---

# Orient

Before any planning, do your own homework on what the user asked, then reflect your
understanding back for confirmation. You explore and restate. You do not implement, and you do
not fix scope alone. Nothing is persisted: the reflected understanding lives in the chat.

Pick the weight:

| Mode | Use when | Hands off to |
|---|---|---|
| **Full** | Several features, or the shape is unsettled. | [`grilling`](../grilling/SKILL.md) |
| **Light** ("brief me") | One change, clear-ish in the user's head, not written down. | [`build`](../build/SKILL.md) |

A written, clear issue skips this skill: run `build` in `issue` mode.

## 1. Capture intent

Take the user's stated goal as the seed, verbatim. Do not expand it or jump to solutions. When
the user passes an issue reference, such as a seed issue from
[`handover`](../handover/SKILL.md), read it (`gh issue view <n> --json title,body`). Its Intent
and Findings are the seed.

## 2. Pull the context pack

Pull `lore_context_pack` once, up front. It is cold-start-safe: an empty repo returns an empty
pack. Add `lore_repo_docs_list` and `lore_repo_docs_fetch` for the full ADR and PRD listing. Read
ADRs and PRDs as context; only `Invariant` lines bind.

- **Docs and decisions** come from the pack's `adr` and `prd` entries and from `CONTEXT.md`.
- **Prior art** comes from the pack's `epic_state`, plus one targeted `lore_search` or
  `lore_drill` call for what the pack lacks.
- **Code** comes from `lore codemap` (or the `lore_codemap` tool): a ranked index, not a
  directory walk.

**Light mode stops here.** Spawn no explorer. Where the pack is thin for a facet, say so in the
brief.

**Full mode** fans out only what the pack left thin: one `Explore` subagent for docs the pack
missed, one for the code map, one for cross-repo consumers when the intent spans repos. Spawn
them in one message at the `mid` tier (required): pass `lore tier resolve mid` as each model; see
[TIER-DELEGATION.md](../../TIER-DELEGATION.md). Scale the fan-out to the ask.

## 3. Reflect back

**Full mode.** One screen, about 300 words, hard cap. Put what the user must react to first:

- **What we are deciding** — the crux and the real choices.
- **Open questions and assumptions.**
- **What I understand you want** — one short paragraph in the project's domain language.
- **Relevant context** — at most five bullets, each a fact that constrains a decision. Close
  with "ask for the long version".
- **Tentative scope** — in and out, marked provisional.

**Light mode.** One message: what you understand, the constraints from the pack, a provisional
scope, and three to five questions, each with your recommended answer filled in. The user replies
once ("yes to your recs except #3"). Ask a second round only when an answer forks the design.

The cap governs the printed brief, never the homework behind it.

## 4. Loop

Ask whether this matches. On a correction, explore again only the facets that moved. Repeat until
the user confirms. When light mode finds the work is several features or unsettled, say so and
switch to full mode; the brief so far seeds step 1.

## 5. Hand off

- **Full mode:** `/lore-workflow:grilling`, in "grill with docs" mode by default. It aligns terms
  against `CONTEXT.md` and ADRs, which `to-epic` depends on. Say "grill me" when there is no
  domain model to align against.
- **Light mode:** file the agreed intent and acceptance criteria through
  [`file-issue`](../file-issue/SKILL.md), then run `build` in `issue` mode. When even an issue is
  ceremony, run `build` in `loop` mode. Write no brief file: the issue holds what survives.
  **Never write to `CONTEXT.md`** — a term worth adding belongs to `grilling`, not this skill.
  An ADR candidate goes on the build ledger.

## 6. Session end

Before you hand off, run the retrieval-miss check: read `feedback.retrieval_misses` (`lore config
get feedback.retrieval_misses`). When true, file one issue per retrieval miss on
`feedback.retrieval_misses_repo` through `file-issue`, naming the fact, the tools tried, and the
turn count. When false, skip the check. List every issue and PR the session created or commented
on in your final message.

Chain: `orient → grilling → to-epic → build → document`, with `handover` at any stop.
