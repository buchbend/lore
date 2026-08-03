# Why the issue register is a document, not config

The **issue register** fixes the prose style for issue text, PR descriptions,
and ADR context sections. Lore ships one default. A team replaces it with one
file in its wiki. This page explains three choices. Why the register is a prose
document rather than a settings block. Why a team's copy wins whole rather than
merging. Why the lint runs when the text is written, not when it is committed.

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

Shipping a document also means the register can carry a worked example. The
default register shows one issue before and after. An example teaches a style
faster than a rule list does.

## Why whole-file override

A team's copy replaces the default entirely. Nothing merges.

Merging a rules essay is ill-defined. Two documents that both describe how to
open a sentence do not combine into one coherent instruction. They combine into
a contradiction the reader has to resolve. Whole-file resolution also leaves no
cascade to debug — one lookup answers "which register applies here", and the
answer is a path you can open.

The cost is that customizing means copying the default and editing it, and a
team that does so stops inheriting later improvements to the default. That
trade is deliberate: a register that silently changes under a team is worse
than one they own.

There is no per-repo layer for the same reason. The register belongs to a
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
absent the register still applies as instructions and nothing blocks. Lore
degrades to the weaker enforcement rather than failing.

## What the register does not do

It does not fix terminology. One term with one meaning is a glossary problem,
and the glossary is a separate artifact.

It does not decide what is worth filing. The `file-issue` skill owns how to
write and file; the caller decides what to capture. A skill that second-
guessed the caller's judgement would turn every capture into a negotiation.

## Related

- [Customize the issue register](../how-to/customize-the-issue-register.md)
- [Why the PRD lives in the repo](why-prd-in-repo.md)
