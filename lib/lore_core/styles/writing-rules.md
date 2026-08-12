# Writing Rules

Status: draft
Scope: issue text, PR descriptions, PR review comments, ADR context sections, design documents and flag text

## Why this exists

We are an international team. Most of us read English as a second language.
An agent writes fluent, dense English and assumes the context of the session
that wrote it. The text reads as polished and costs the reader real effort.

These rules come from ASD-STE100 Simplified Technical English, written so
that a reader can work from a technical document without ambiguity. We keep
its writing rules. We drop its approved-word list, which would fight our
domain vocabulary.

The rules say how to write. They do not say what to write. Say whatever the
work needs. Say it so the next reader gets it the first time.

## Rules

### Vocabulary

1. One term, one meaning. Take the term from the glossary. Do not vary the wording for style.
2. Do not introduce a new domain term. Add it to the glossary first, in its own commit.
3. Use the shorter common word. Banned: leverage, utilise, robust, seamless, holistic, comprehensive, streamline, delve, underscore, intricate, crucial, pivotal, landscape, journey, unlock, elevate.
4. Expand every abbreviation on first use unless the glossary holds it.
5. Do not use a noun as a verb, or a verb as a noun, unless the glossary lists that form.

### Sentences

6. Maximum 20 words for an instruction. Maximum 25 for a description.
7. One instruction per sentence.
8. Active voice. Name the actor. Not "the file is written" but "the ingest service writes the file".
9. Do not open a clause with a participle. Not "Having parsed the header, the service...". Write two sentences.
10. Maximum three nouns in a row. Break longer chains with a preposition.
11. Present tense for behaviour. Imperative for actions.
12. Do not use "this", "that" or "it" for a whole preceding clause. Repeat the noun.

### Structure

13. State the context. Assume the reader was not in the session that wrote the text.
14. Name where you observed it: host, log, dashboard, run identifier, file path and line, command output, test name.
15. Write acceptance criteria in EARS. See below.
16. Lists over paragraphs. No section holds more than one paragraph of prose.
17. No closing summary. Do not restate the text at the end.
18. Keep out the reasoning that led here unless it constrains the solution. It belongs in the ADR.
19. When you lack a fact, write the heading and `TODO:` with the specific question. Never write a plausible guess.

Rule 19 carries more weight than the rest. Most text that is hard to decode
holds no banned word. It holds a confident sentence written over a missing
fact.

### Short names

20. A short name for a thing belongs in the glossary. Where the glossary holds no entry, write the meaning out. `L0` means a data level.
21. A short name for a piece of work never enters a title, a description, a document or a commit message. A phase, a group and a priority code are such names. Cite the issue number instead. Not "the G4 group" but "issue 412".

Enforcement differs per rule. Vale lints rules 3 and 6, the banned words and
the sentence length. Rules 9 and 12 run as regex heuristics that catch the
common forms and miss the rest. Rules 4 and 10 need a human reviewer, because
Vale does not tag parts of speech. Rules 1, 2 and 20 need the glossary.

## EARS patterns for acceptance criteria

Five patterns cover almost everything, and each maps close to one test.

| Pattern | Form |
| --- | --- |
| Ubiquitous | The `<system>` shall `<response>`. |
| Event driven | When `<trigger>`, the `<system>` shall `<response>`. |
| State driven | While `<state>`, the `<system>` shall `<response>`. |
| Unwanted behaviour | If `<condition>`, then the `<system>` shall `<response>`. |
| Optional | Where `<feature is present>`, the `<system>` shall `<response>`. |

One criterion per line. Do not join two behaviours with "and".

## Required issue structure

```
# <Imperative title, max 12 words>

## Context
## Current behaviour
## Required behaviour
## Acceptance criteria
## Out of scope
## References
```

Sections may be empty and stay in the file. An empty "Out of scope" is a
signal, not an omission. A small issue keeps the same headings and writes one
or two lines under each.

## Batch issues

An issue carries one change or several changes.

- **Change** — one required-behaviour statement with its own acceptance criteria. The smallest unit the writing rules describe.
- **Batch issue** — several changes under one Context section, one PR, and no ordering dependency between the changes.

In a batch, give each change one subheading under "Required behaviour", and
repeat the same subheadings under "Acceptance criteria". Every change carries
its own criteria, never one pooled list.

Split the batch when a change needs its own Context section. Split it when
one change must land before another change.

## Flag text

A flag holds one lead sentence and a short body. A teammate reads it months
later, on a wiki page, without the session that filed it. That reader is the
reader these rules are written for.

Apply the vocabulary rules, the sentence rules, and rules 13, 14, 17, 19, 20
and 21.

Three parts do not apply to a flag:

- The issue skeleton and the EARS patterns. A flag carries two fields, not sections.
- Rule 16. A flag is shorter than the paragraph that rule guards against.
- Rule 18. A flag exists to carry the reasoning that no document holds.

Two rules change shape in a flag:

- Rule 14 lands in the flag's refs. Name a host, a log path or a run in the body.
- Rule 13 costs one clause. Name the component the fact is about in the lead.

Write the lead as the fact. Do not write it as a report about the session.
Write "the reaper starves mid-drain", not "we found that the reaper starves".
Lore adds the session framing itself when a ref does not check out.

## Block for CLAUDE.md and AGENTS.md

Paste this into the agent instruction file so that generated text arrives in
the writing rules.

```markdown
## Issue writing

These rules cover issue text, PR descriptions, PR review comments, ADR
context sections, design documents and flag text. When you write or edit one,
follow the writing rules (`lore style show writing-rules`, or the copy your
team pasted below). In short:

- Use the required section structure. Keep empty sections.
- One term, one meaning. Take terms from the glossary. Do not invent domain
  terms; ask instead.
- A short name for a thing belongs in the glossary. Where no entry exists,
  write the meaning out.
- A short name for a piece of work never enters a title, a description, a
  document or a commit message. A phase, a group and a priority code are such
  names. Cite the issue number.
- Maximum 20 words per instruction, 25 per description. One instruction per
  sentence.
- Active voice with a named actor. No participial clause openers. No noun
  chains longer than three. No "this" or "it" referring back to a clause.
- Name where every observation came from: host, log path, run identifier,
  timestamp.
- Acceptance criteria in EARS. One behaviour per criterion.
- Lists over paragraphs. No closing summary.
- Do not use: leverage, utilise, robust, seamless, holistic, comprehensive,
  streamline, delve, underscore, intricate, crucial, pivotal, landscape,
  journey, unlock, elevate.
- When a fact is missing, write the heading and TODO with the specific
  question. Do not fill the gap with a plausible guess.
- A flag follows the same rules. A flag skips the section skeleton and EARS,
  and states the fact rather than the session that found it.
```

## Not constrained

- The glossary's own contents. Separate artifact, separate problem.
- Code comments, chat, session notes and commit messages, which already have a
  convention. Rule 21 still covers a commit message.
- Machine-read text: roadmap tables, board comments, reviewer verdict blocks.
- Vocabulary size in "Context", where domain precision beats simplicity.
