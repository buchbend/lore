---
name: lore:orient
description: The first step of a piece of work — the session does its own homework, then
  reflects its understanding back for confirmation before any planning or grilling. A light
  mode ("brief me") pulls only the roadmap, the context pack and the code map, and hands one
  change straight to build. Use at the start of a task, when the user says "orient", "get oriented", "brief me",
  "what's your understanding", or wants the session to reflect back before planning.
---

# Orient

Before any planning, do your own homework on what the user asked, then reflect your
understanding back for confirmation. You explore and restate. You do not implement, and you do
not fix scope alone. Nothing is persisted: the reflected understanding lives in the chat.

Pick the weight:

| Mode | Use when | Hands off to |
|---|---|---|
| **Full** | The shape is unsettled, or the work spans an epic. | [`grilling`](../grilling/SKILL.md) |
| **Light** ("brief me") | One change, clear-ish in the user's head, not written down. | [`build`](../build/SKILL.md) |

A written, clear issue skips this skill: run `build` in `issue` mode.

Two options combine with either mode:

- **Page** ("as an artifact", "as a page"): the restatement also goes out as a page. See step 3.
- **Named agents** ("orient with `science-pm-counselor`"): each named subagent reviews the intent
  through its own lens. See step 2.

## 1. Capture intent

Read the roadmap first: the `ROADMAP.md` at the repo root, if present. When the work sits in a
component directory with its own `ROADMAP.md`, read that file too. Where the user names a roadmap
item, the item is the seed. Follow its links down to the PRD, epic or issue. Read a milestone ref
in an item, such as `pm:<id>`, with `lore_read` when a source serves the ref. Without a source,
the ref stays plain text.

Otherwise, take the user's stated goal as the seed, verbatim. Do not expand it or jump to
solutions. When the user passes an issue reference, such as a seed issue from
[`handover`](../handover/SKILL.md), read it (`gh issue view <n> --json title,body`). Its Intent
and Findings are the seed.

## 2. Pull the brief

Run `lore brief --json "<intent>"` first, before any other homework call. Pass the seed in a few
words. The brief holds four parts:

- `pack`: the context pack. Its ADRs and PRDs are ranked against the intent, top 8, and
  `epic_state` holds the focus issues.
- `sections`: the first section of each ranked doc.
- `codemap`: about 1000 tokens of the code map. Symbols that name an intent word come first.
- `glossary`: the `CONTEXT.md` definitions of the domain words in the intent.

Read ADRs and PRDs as context; only `Invariant` lines bind.

Where the brief is thin, explore freely. To chain several lookups, write a short `lore.api`
script and print only what you keep. `help(lore.api)` lists the functions, one line each:

```bash
"$(dirname "$(readlink -f "$(command -v lore)")")/python" - <<'PY'
from lore.api import context_pack, repo_docs_fetch, search
top = context_pack(intent="board ledger")["ranked"][0]  # e.g. docs/adr/0002-x.md
print(repo_docs_fetch(top.split("/")[1], top)["content"][:1500])
print([h["path"] for h in search("board ledger")["wiki"]])
PY
```

**Light mode stops here.** Spawn no explorer. Where the brief is thin for a facet, say so in the
restatement.

**Full mode** fans out only what the brief left thin: one `Explore` subagent for docs the brief
missed, one for the code map, one for cross-repo consumers when the intent spans repos. The
cross-repo explorer starts from `lore map <owner/repo> --json` for the current repo and widens
from there. A repo in no map gets an empty list: it scans as before. Spawn them in one message at the `mid` tier (required): pass `lore tier resolve mid` as each model (on OpenCode, as each agent),
and open each description with `[orient:explore]`; see
[TIER-DELEGATION.md](../TIER-DELEGATION.md). Scale the fan-out to the ask. For one lookup,
grep or read directly; spawn only for parallel or isolated work.

**Named agents** run in both modes, after the brief, in the same message as any explorers. Spawn
each one as its own named agent and keep its own model; set no tier. Open its description
with `[orient:named]`. Give it the verbatim
intent and the brief facts that bear on its lens. Ask for the constraints, risks and questions it
sees, each with its evidence. A name that is not an available agent type: say so in the restatement and
go on without it. Fold each return into the restatement and credit the agent by name.

## 3. Reflect back

**Full mode.** One screen, about 300 words, hard cap. Put what the user must react to first:

- **What we are deciding** — the crux and the real choices.
- **Open questions and assumptions.**
- **What I understand you want** — one short paragraph in the project's domain language.
- **Relevant context** — at most five bullets, each a fact that constrains a decision. Close
  with "ask for the long version".
- **Tentative scope** — in and out, marked provisional.

**Light mode.** One message: what you understand, the constraints from the brief, a provisional
scope, and three to five questions, each with your recommended answer filled in. The user replies
once ("yes to your recs except #3"). Ask a second round only when an answer forks the design.

The cap governs the printed restatement, never the homework behind it.

**Page.** Read `lore config get workflow.pages`. On `artifact`, publish the page as an artifact.
On `html`, or when the session has no Artifact tool, write `<skill>-<slug>.html` in the scratchpad.
The page holds the restatement's sections in the same order, then the long version under them: every
relevant-context fact with its source, the named agents' returns, and the code map excerpt. The
chat still carries the printed restatement and the page link. The loop stays in the chat; republish the
same page after each correction.

## 4. Loop

Ask whether this matches. On a correction, explore again only the facets that moved. Repeat until
the user confirms. When light mode finds the work is unsettled or spans an epic, say so and
switch to full mode; the restatement so far seeds step 1.

## 5. Hand off

- **Full mode:** `/lore:grilling`, in "grill with docs" mode by default. It aligns terms
  against `CONTEXT.md` and ADRs, which `to-epic` depends on. Say "grill me" when there is no
  domain model to align against.
- **Light mode:** file the agreed intent and acceptance criteria through
  [`file-issue`](../file-issue/SKILL.md). When the seed is a roadmap item, name the item in the
  issue body. Then run `build` in `issue` mode. When even an issue is
  ceremony, run `build` in `loop` mode. Write no restatement file: the issue holds what survives.
  **Never write to `CONTEXT.md`** — a term worth adding belongs to `grilling`, not this skill.
  An ADR candidate goes on the build ledger.

## 6. Session end

Before you hand off, run the retrieval-miss check: read `feedback.retrieval_misses` (`lore config
get feedback.retrieval_misses`). When true, file one issue per retrieval miss on
`feedback.retrieval_misses_repo` through `file-issue`, naming the fact, the tools tried, and the
turn count. When false, skip the check. List every issue and PR the session created or commented
on in your final message.

Chain: `orient → grilling → to-epic → build → document`, with `handover` at any stop.
