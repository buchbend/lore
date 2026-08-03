# Issue Register

Status: draft
Scope: issue text, PR descriptions, ADR context sections
Owner: TBD

## Why this exists

Our team is international and already converges on a shared subset of English. Agent written issues do not converge with us. They pull toward a wider vocabulary and a denser style, and they assume context that only existed in the session where the issue was written. The result reads as polished and is expensive to decode.

This document fixes the register for issue text. It does not fix terminology. Terminology lives in the glossary and is a separate problem.

The rules below are adapted from ASD-STE100 Simplified Technical English, which was built so that non native readers could work from technical documentation without ambiguity. We take the rules and drop the approved word dictionary, which would strangle our domain writing.

## Rules

### Vocabulary

1. One term, one meaning. Use the glossary term every time. Do not vary wording for style.
2. Do not introduce a new domain term inside an issue. Add it to the glossary first, in its own commit.
3. Use the shorter common word. Banned: leverage, utilise, robust, seamless, holistic, comprehensive, streamline, delve, underscore, intricate, crucial, pivotal, landscape, journey, unlock, elevate.
4. Expand every abbreviation on first use unless it is in the glossary.
5. Do not use a noun as a verb, or a verb as a noun, unless the glossary lists that form.

### Sentences

6. Maximum 20 words for an instruction. Maximum 25 words for a description.
7. One instruction per sentence.
8. Active voice. Name the actor. Not "the file is written" but "the ingest service writes the file".
9. Do not open a clause with a participle. Not "Having parsed the header, the service...". Write two sentences.
10. Maximum three nouns in a row. Break longer chains with a preposition.
11. Present tense for behaviour. Imperative for actions.
12. Do not use "this", "that" or "it" to refer back to a whole preceding clause. Repeat the noun.

### Structure

13. State the context explicitly. Assume the reader was not in the conversation where the issue was written.
14. Every statement about current behaviour names where it was observed. Host, log, dashboard, run identifier, file path and line, command output, test name.
15. Acceptance criteria use EARS. See below.
16. Lists over paragraphs. No section holds more than one paragraph of prose.
17. No closing summary. Do not restate the issue at the end.
18. Do not include reasoning that led to the issue unless it constrains the solution. Put it in the ADR instead.

Enforcement differs per rule:

- Vale lints rules 3 and 6. The linter checks the banned words and the sentence length.
- Rules 9 and 12 run as regex heuristics. The regex catches the common forms and misses the rest.
- Rules 4 and 10 need a human reviewer. Vale does not tag parts of speech.
- Rules 1 and 2 become checkable once the glossary is structured.

## EARS patterns for acceptance criteria

Five patterns cover almost everything. They read cleanly for us and map close to one test each.

| Pattern | Form |
| --- | --- |
| Ubiquitous | The `<system>` shall `<response>`. |
| Event driven | When `<trigger>`, the `<system>` shall `<response>`. |
| State driven | While `<state>`, the `<system>` shall `<response>`. |
| Unwanted behaviour | If `<condition>`, then the `<system>` shall `<response>`. |
| Optional | Where `<feature is present>`, the `<system>` shall `<response>`. |

Write one criterion per line. Do not combine two behaviours with "and".

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

Sections may be empty and stay in the file. An empty "Out of scope" is a signal, not an omission.

## Batch issues

An issue carries one change or several changes.

- **Change** — one required-behaviour statement with its own acceptance criteria. The smallest unit the register describes.
- **Batch issue** — several changes under one Context section, one PR, and no ordering dependency between the changes.

Rules for a batch issue:

- Keep the required section structure.
- Give each change one subheading under "Required behaviour".
- Repeat the same subheadings under "Acceptance criteria".
- Split the batch when a change needs its own Context section.
- Split the batch when one change must land before another change.

## Worked example

### Before, typical agent output

> ## Overview
>
> This issue addresses a critical gap in our housekeeping telemetry ingestion pipeline. Currently, when the FFTS backend undergoes an unexpected restart, the ingest service fails to gracefully handle the resulting connection state transition, leading to a scenario where telemetry samples are silently dropped without any corresponding entry in the provenance log. This is problematic because it undermines our ability to reason about data completeness downstream, particularly for calibration workflows that rely on continuous housekeeping coverage.
>
> ## Proposed Solution
>
> We should implement a robust reconnection strategy that leverages exponential backoff while ensuring that any gap in the telemetry stream is comprehensively captured in the provenance layer. Having established the gap boundaries, the service should emit a structured gap record that downstream consumers can utilise to make informed decisions about data quality.

Two paragraphs, no host, no log, no run identifier, no way to test it. "Critical", "gracefully", "robust", "comprehensively", "leverages", "utilise". One participial opener. "This is problematic" refers back to a whole clause. It reads as if someone knows the answer, and none of it can be verified.

### After

```markdown
# Record telemetry gaps when the FFTS backend restarts

## Context

The ingest service reads housekeeping telemetry from the FFTS backend over a
persistent TCP connection. The backend restarts during normal operations, on
average twice per observing night.

Calibration workflows assume continuous housekeeping coverage. They have no way
to distinguish a real gap from a missing sample.

## Current behaviour

The ingest service drops samples during the reconnect window. It writes no
provenance record for the dropped interval.

Observed on ccat-housekeeping, run 2026-07-28T22:14Z. The service log shows a
reconnect at 22:14:31 and the next sample at 22:14:47. The L0 store holds no
rows for that interval. The provenance table holds no gap record.

## Required behaviour

The ingest service detects the reconnect and records the gap boundaries in the
provenance table.

The service does not attempt to interpolate the missing samples.

## Acceptance criteria

- When the ingest service loses the backend connection, the service shall write
  a gap record with the timestamp of the last received sample.
- When the ingest service reconnects, the service shall close the open gap
  record with the timestamp of the first received sample.
- If the service restarts while a gap record is open, then the service shall
  close the gap record with the restart timestamp.
- While a gap record is open, the service shall retry the connection every five
  seconds.
- The service shall write no rows to the L0 store for a gap interval.

## Out of scope

Reconnect behaviour for the science data path. That path uses a different
transport and is covered by #412.

Alerting on gap frequency.

## References

- Service log: ccat-housekeeping:/var/log/hk-ingest/2026-07-28.log
- Provenance schema: docs/schema/provenance.md
- Related: #412
```

### A small issue does not need the full apparatus

```markdown
# Set the retention period on the ingest service log

## Context

The ingest service log on ccat-housekeeping has no rotation policy. The log
directory holds 41 GB.

## Current behaviour

logrotate has no entry for hk-ingest.

## Required behaviour

The log rotates daily and is kept for 30 days.

## Acceptance criteria

- The system shall rotate /var/log/hk-ingest/*.log daily.
- The system shall retain 30 rotated files.
- The system shall compress rotated files.

## Out of scope

Retention for the science data path logs.

## References

- Ansible role: roles/hk-ingest/tasks/main.yml
```

## Block for CLAUDE.md and AGENTS.md

Paste this into the agent instruction file so that generated issues arrive in the register.

```markdown
## Issue writing

When you write or edit an issue, a PR description, or an ADR context section,
follow the issue register (`lore style show issue-register`, or the copy your team
pasted below). In short:

- Use the required section structure. Keep empty sections.
- One term, one meaning. Take terms from the glossary. Do not invent domain
  terms; ask instead.
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

If the issue needs context you do not have, write the section heading and the
word TODO with the specific question. Do not fill the gap with a plausible
guess.
```

The last paragraph matters more than the rest. Most of the crypticness we see is not vocabulary. It is a confident sentence written over a missing fact.

## Deliberately not constrained

- The glossary. Separate artifact, separate problem.
- Comments in code.
- Mattermost.
- Commit messages. They already have a convention.
- Vocabulary size in the "Context" section, where domain precision beats simplicity.

## Open questions

- Does the register apply to ADRs in full, or only to their context sections?
- Do we lint on commit, on PR, or both?
- What is the escape hatch when a rule blocks a correct sentence? Proposal: an
  inline `<!-- vale off -->` with a reason on the same line.

