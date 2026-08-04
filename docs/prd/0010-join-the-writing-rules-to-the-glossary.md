---
title: "Join the writing rules to the glossary"
status: draft
epic: https://github.com/buchbend/lore/issues/336
repos:
  - buchbend/lore
---

# PRD 0010: Join the writing rules to the glossary

> Source of truth for this epic.

## Problem

Agents write short names that no glossary defines. A reader who was not in
the session cannot decode the text. The writer cannot decode it either, a
month later.

Four issue titles in `ccatobs/system-integration` show the failure. A check
of every short name in those titles against that repo's `CONTEXT.md`:

| Short name | In the glossary |
| --- | --- |
| `C-ext` | yes, as "credential class C-ext" |
| `green-while-broken` | yes |
| `red-while-working` | no |
| `deploy_user` | no; the glossary says "deploy user" |
| `config-sync` | no; the glossary says "config sync" |
| `LTA` | no |
| `P6` | no |
| `epic cascade` | no |

The glossary was in place first. `ccatobs/system-integration/CONTEXT.md` was
created 2026-07-17 and last changed 2026-07-23. Issues 453 and 454 were
written 2026-07-24. Issue 459 was written 2026-07-27. Issue 468 was written
2026-07-31.

The commit that last changed that glossary is titled "docs(openbao):
ADR-0011 SECRET_KEY in core slice + P6 game-day findings". The commit used
`P6` in its own message. The commit did not add `P6` to the glossary.

Four separate failures appear in the table:

1. The glossary defines the term. The writer uses it without the
   definition. `C-ext` alone carries no meaning.
2. The glossary defines the term. The writer spells it differently.
   `deploy_user` and "deploy user" name one thing.
3. The term names a real thing. No glossary defines it. `LTA` and
   `red-while-working` are examples.
4. The term names a piece of work, not a thing. `P6` and `epic cascade` are
   examples. A work label carries no meaning once the work is finished.

Failure 4 needs a different answer from failure 3. A glossary entry for `P6`
would be wrong.

The writing rules already answer failures 1 to 3, and the answers do not run.
Rule 1 tells the writer to take the term from the glossary. Rule 2 forbids a
new domain term inside an issue. Rule 4 requires an expansion for every
abbreviation outside the glossary. All three point at a glossary. No skill
reads one.

The document states the gap in its own text: "Rules 1 and 2 become checkable
once the glossary is structured."

Two further observations, both in this repo:

- The document carried a worked example that used `L0` twice, and no glossary
  defines `L0`. The example that agents copy broke the rule the document is
  about to add. Commit `e17807a` removed that example while compacting the
  document from 245 lines to 139.
- The segmenter in `tests/test_context_sentences.py` counts 27 of the 114
  sentences in Lore's `CONTEXT.md` past the 25-word ceiling, measured on
  `main` before this epic. `ccatobs/system-integration/CONTEXT.md` runs 3 of
  24 past it. A definition nobody can read fixes nothing.

## Solution

A human decides which short names are real. Lore writes the decision down
once, then reads it at every point where an agent writes.

The team keeps one glossary per repo, in `CONTEXT.md`, in the format
`lore-workflow/skills/domain-modeling/CONTEXT-FORMAT.md` already specifies.
Nothing about the file format changes.

The writing rules gain two entries. One rule covers a short name for a thing. One
rule covers a short name for a piece of work. The `file-issue` skill reads
the repo's `CONTEXT.md` when it drafts, next to the document it already
resolves.

The `grilling` skill stays the only door into the glossary. A person
approves every entry. At the end of an interview, `grilling` lists the terms
it wrote.

`CONTEXT-FORMAT.md` adopts the sentence rules, so a definition
reads as plainly as the text that cites it.

## Implementation decisions

- **The document is renamed from "issue register" to "writing rules".** Both
  halves of the old name were wrong. The scope covers PR descriptions and ADR
  context, so "issue" understates it. "Register" is a linguistics term, and
  our readers do not work in that field. In a software repo the word first
  reads as a CPU register or a list of records. "Writing rules" states what
  the file holds before a reader opens it.

- **The rename is whole, and a dated alias covers the paste block.** Every
  document, skill and test uses the new name. `KNOWN_STYLES` keeps
  `issue-register` as a deprecated alias that resolves to the new file and
  prints one line to stderr naming the new command. Instruction files in
  other repos already carry `lore style show issue-register`, and a blank
  error would strand them. The alias is removed once those files are
  repasted. ADR 0006 and PRD 0009 keep the old name, because both record a
  decision that was made under it.

- **The rename lands before every other change.** No later slice writes the
  old name, so no later slice needs rewriting.

- **The glossary read happens at drafting time, not only at session start.**
  Gloaguen and colleagues find that context files do not generally improve
  task success and add over 20% to inference cost
  (<https://arxiv.org/abs/2602.11988>). McMillan finds no adherence effect
  from file size or position across 1,650+ sessions, and finds compliance
  decaying within a session (<https://arxiv.org/abs/2605.10039>). So
  `file-issue` reads `CONTEXT.md` at step 1. The SessionStart directive
  stays one line and does not grow.

- **`CONTEXT.md` is a plain file in the repo. No new command resolves it.**
  The writing rules resolve per wiki. The glossary resolves per repo. A skill
  reads the file from the working directory, or from the repo it targets.

- **The document carries two rules, not one.** A short name for a thing must
  be in the glossary; a writer who lacks the entry writes out the meaning
  instead. A short name for a piece of work never enters a title, a
  description, a document or a commit message. A writer who must point at
  the work cites the issue number.

- **The second rule already exists in a private file.** The user's global
  agent instructions forbid phase names in code comments and summaries, and
  name the issue number as the escape hatch. The rule reaches one person.
  The shared document gives the same rule to the team.

- **A person approves every glossary entry.** No skill writes a term an
  agent invented without approval. The surveyed record contains no team that
  auto-appends agent-invented terms. The `nix.dev` Vale adoption shows the
  cost: an agent that appends its own term disables the rule that would have
  caught the term (<https://github.com/NixOS/nix.dev/pull/798>).

- **`grilling` is the only door.** `domain-modeling` drops the clause that
  invites another skill to maintain the model. `implement-issue` and `brief`
  keep their references, which cover ADR criteria and never touch the
  glossary.

- **`grilling` ends with a recap.** The skill lists every term it wrote or
  changed, so the user sees the glossary diff without opening the file.

- **Vale gets a second gate, and the gate never blocks.** Vale's `spelling`
  check flags any word absent from the dictionaries it is given
  (<https://docs.vale.sh/checks/spelling>). Common English is the base
  dictionary. The glossary terms are the ignore list. A word in neither is
  flagged, including a term nobody listed in advance. Vale's default filters
  skip mixed-case words, uppercase tokens and words holding digits. The
  check needs `custom: true` and explicit filters to see `L0`, `C-ext` or
  `LTA`.

- **Lint severity follows GitLab's model.** An error blocks. A warning shows
  and does not block. The glossary check ships as a warning
  (<https://docs.gitlab.com/development/documentation/testing/vale/>).
  Contentsquare's first Vale run produced 673 errors and 12,910 warnings,
  and the team judged the result not actionable
  (<https://engineering.contentsquare.com/2023/using-vale-to-help-engineers-become-better-writers/>).

- **The document replaces text rather than growing.** The document ran 245
  lines. Anthropic names the over-specified instruction file as a standard
  failure mode. Practitioners cap such files near 300 lines
  (<https://www.humanlayer.dev/blog/writing-a-good-claude-md>). Every
  addition here removes or rewrites an existing paragraph.

- **The document shows the contrast inside a rule, not in an example.** Rules
  8 and 9 already pair a wrong sentence with a right one, at one line each.
  A new rule that needs a contrast follows the same shape.

- **Existing terms enter through one grilling session per repo.** `L0`,
  `L1b` and `LTA` are real and undefined. A person seeds them. The work is
  a migration step, not a tool.

## Testing decisions

Test the CLI and the file contents, not skill prose. Prior art: the existing
`tests/test_style_register.py` and `tests/test_directive_template.py`.

- Assert the document carries both new rules.
- Assert `CONTEXT-FORMAT.md` carries the sentence ceiling.
- Assert the SessionStart directive still renders one line, and that the
  line names the glossary.
- Assert the document passes its own Vale style on every line except rule
  3's banned-word list. That line names all 16 banned words, so Vale flags
  each one and the file can never exit 0. Vale reports 16 errors at commit
  `e17807a`, all on that line. The check must keep passing as rules are
  added.
- Run the Vale spelling check against a fixture holding one glossary term
  and one invented short name. Skip when Vale is absent, as
  `tests/test_vale_style.py` already does.

## Out of scope

- Automatic extraction of terms from code or transcripts.
- Any write path into `CONTEXT.md` outside `grilling`.
- A check that a term is used with the meaning the glossary gives it. No
  surveyed tool does this. The check stays human.
- CI lint over issue bodies or PR descriptions. Lint runs at generation
  time, per ADR 0006.
- The PR-body skeleton, the review-comment shape and the `review-pr` skill.
  Those depend on a shared vocabulary and follow in a later epic.
- Session notes, commit messages and code comments, which PRD 0009 already
  places outside these rules.

## Open questions

- TODO: which repos get a seeding grilling session first, and in what order?
- TODO: does `LTA` belong in `ccatobs/system-integration/CONTEXT.md`, or in
  a data-center repo that owns the archive?
