# Why the writing rules are a document, not config

The **writing rules** fix the prose style for issue text, PR descriptions,
PR review comments, ADR context sections and design documents. Lore ships
one default. A team replaces them with one file in its wiki. This page
explains the choices behind them. Why the rules are a prose document rather
than a settings block. Why a team's copy wins whole rather than merging. Why
the lint runs when the text is written, not when it is committed. Why the
rules cite a glossary they do not own.

The decisions here are recorded in
[ADR 0006](../adr/0006-issue-register-whole-file-override-generation-time-lint.md).

## The problem it solves

Agent-written issues read polished and cost readers real effort. An
international team converges on a shared subset of English. Agents pull toward
a wider vocabulary, denser sentences, and context that existed only in the
session that wrote the issue.

A review of four agent-written issues found the concrete failures. No shared
section structure. No acceptance criteria. Sentences over 30 words. Epic
context assumed instead of stated. Vocabulary was not the failure — the
banned-word list scored zero hits.

The failure that matters is structural. A confident sentence written over a
missing fact survives review far too easily.

## Why a document

The rules are prose because they are read by a language model and by people,
and because most of them cannot be expressed as settings. "Name where every
observation came from" and "do not fill a gap with a plausible guess" are
judgement, not flags. A settings block would capture the banned-word list and
the sentence cap, which are the least valuable rules, and drop the rest.

A shipped document can show as well as tell. Rules 8 and 9 each contrast a
wrong sentence with a right one, in one line. The document carried a full
before-and-after issue until that example grew to 42% of the file. The rules
already said what the example showed.

## Why whole-file override

A team's copy replaces the default entirely. Nothing merges.

Merging a rules essay is ill-defined. Two documents that both describe how to
open a sentence do not combine into one coherent instruction. They combine into
a contradiction the reader has to resolve. Whole-file resolution also leaves no
cascade to debug — one lookup answers "which rules apply here", and the
answer is a path you can open.

The cost is that customizing means copying the default and editing it, and a
team that does so stops inheriting later improvements to the default. That
trade is deliberate: rules that silently change under a team are worse
than rules the team owns.

There is no per-repo layer for the same reason. The rules belong to a
team, and a team's wiki is where its shared conventions already live.

## Why lint at generation time

Vale runs when an agent drafts the text, not when a commit lands.

A lint that runs in CI catches the problem after the issue is filed. By then a
fix means editing a body that people have already read. A lint that runs at
generation time catches it while the draft is still a temp file. The agent
rewrites and reruns before anything is posted.

This also keeps the lint honest about what it can check. Only two rules are
mechanically decidable: banned words and sentence length. Two more are
regex heuristics that over-fire — a rule against participial openers also flags
a heading like `## Testing decisions`. The rest are review-only. Vale reports
the heuristics as warnings and exits 0, so an over-fire never blocks a correct
sentence, and the fix loop cannot spin on one.

Vale is not bundled. It is a single binary, detected on `PATH`. When it is
absent the rules still apply as instructions and nothing blocks. Lore
degrades to the weaker enforcement rather than failing.

## Why the rules reach into the glossary

The rules once stopped at prose and left terminology alone. A review of four
issue titles in `ccatobs/system-integration` showed the cost. Six of the eight
short names in those titles had no glossary entry, or had an entry the writer
spelled differently. A reader outside the session cannot decode `LTA` or `P6`.
The writer cannot decode either name a month later.

Rules 1, 2 and 4 already pointed at a glossary. No skill read one, so the rules
named an authority nobody consulted. Rules 20 and 21 close the gap, and the
`file-issue` skill reads the repo's `CONTEXT.md` when it drafts.

Lore reads the glossary at drafting time rather than only at session start.
Gloaguen and colleagues report that context files do not generally improve task
success, and add over 20% to inference cost
(<https://arxiv.org/abs/2602.11988>). McMillan finds no adherence effect from
file size or position across 1,650+ sessions, and finds compliance decaying
inside a single session (<https://arxiv.org/abs/2605.10039>). A glossary loaded
at session start fades before the agent writes the issue. So `file-issue` reads
the file at step 1, beside the rules it already resolves, and the SessionStart
directive stays one line.

## A thing and a piece of work take different answers

Rule 20 covers a short name for a thing. Rule 21 covers a short name for a
piece of work. The split matters because a glossary entry for `P6` would be
wrong.

A **thing** persists. `L0`, `C-ext` and `LTA` name a data level, a credential
class and an archive. Each name still means something after the work that
introduced it ships. A thing earns a glossary entry, and rule 20 sends the
writer there. Where the glossary holds no entry yet, the writer spells the
meaning out and asks `grilling` for the entry.

A **piece of work** expires. `P6`, "the G4 group" and "phase 2" each name a
slice of a plan. Each name dies with the plan that carried it. A glossary entry
would preserve a label for work that is already finished. Rule 21 sends the
writer to the issue number, which stays resolvable for as long as the tracker
lives.

One question separates the two. Ask whether the name still means anything once
the work is finished. A name that survives belongs in the glossary. A name that
dies gets an issue number.

## Why the glossary check only advises

Vale flags a short name held by neither the repo's glossary nor common English.
The check ships at warning severity, so a draft never fails on one.

Severity follows GitLab's model, where an error blocks and a warning shows
(<https://docs.gitlab.com/development/documentation/testing/vale/>). The check
is a spelling rule aimed at a hand-written word list. The rule fires on real
gaps and on ordinary words nobody thought to list. Contentsquare's first Vale
run produced 673 errors and 12,910 warnings, and the team judged the output not
actionable
(<https://engineering.contentsquare.com/2023/using-vale-to-help-engineers-become-better-writers/>).
A warning a writer reads and dismisses beats an error that stops the draft.

A person approves every glossary entry for the same reason. `grilling` is the
only door into `CONTEXT.md`, and `domain-modeling` proposes each wording and
waits for the user's yes. An agent that appends its own terms defeats the check
that would have caught them. The `nix.dev` Vale adoption shows the failure
(<https://github.com/NixOS/nix.dev/pull/798>).

## What the writing rules do not do

They do not own the glossary. The rules cite `CONTEXT.md` and lint against it.
The `grilling` skill fills it, and a person approves every entry.

They do not check that a writer uses a term with the meaning the glossary
gives it. No surveyed tool does. The check stays human.

They do not decide what is worth filing. The `file-issue` skill owns how to
write and file; the caller decides what to capture. A skill that second-
guessed the caller's judgement would turn every capture into a negotiation.

## Related

- [Customize the writing rules](../how-to/customize-the-writing-rules.md)
- [Why the PRD lives in the repo](why-prd-in-repo.md)
