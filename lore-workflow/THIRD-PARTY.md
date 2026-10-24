# Third-party notices

`lore-workflow` builds on the skills of Matt Pocock,
[`mattpocock/skills`](https://github.com/mattpocock/skills), licensed MIT. This file
says what we took from his work, what we changed, and what is our own.

The files came into `ccatobs/ccat-agent-workflow` on 2026-06-22 from upstream commit
[`6eeb81b`](https://github.com/mattpocock/skills/commit/6eeb81b5fcfeeb5bd531dd47ab2f9f2bbea27461).
`lore-workflow` ported them from there on 2026-07-09 (PRD 0003). The port lost this
notice; this file restores it.

## From Matt Pocock

| What | Our file | Upstream source | State |
|---|---|---|---|
| Grilling: interview the user until the plan is settled, with a recommended answer to every question | `skills/grilling/SKILL.md` | `skills/productivity/grilling`, `skills/productivity/grill-me`, `skills/engineering/grill-with-docs` | Merged into one skill and rewritten |
| Shared language: challenge terms against the glossary, test terms with scenarios | `skills/grilling/SKILL.md` | `skills/engineering/domain-modeling/SKILL.md` | Adapted |
| Glossary format for `CONTEXT.md` | `skills/grilling/CONTEXT-FORMAT.md` | `skills/engineering/domain-modeling/CONTEXT-FORMAT.md` | Nearly verbatim; small changes for our writing rules |
| ADR format and the three-criteria gate for offering an ADR | `skills/grilling/ADR-FORMAT.md` | `skills/engineering/domain-modeling/ADR-FORMAT.md` | Gate verbatim; template changed (see below) |
| Test-driven development: principles for good and bad tests, the red-green-refactor loop | `skills/tdd/SKILL.md` | `skills/engineering/tdd/SKILL.md` | Adapted; large parts verbatim |
| The idea to turn a conversation into a PRD, and a PRD into issues | `skills/to-epic/SKILL.md` | `skills/engineering/to-prd`, `skills/engineering/to-issues` | Idea only; text is our own |

The ideas under these skills are older than both projects. Architecture decision records
come from Michael Nygard (2011). Ubiquitous language comes from Eric Evans,
*Domain-Driven Design* (2003). Test-driven development comes from Kent Beck. Matt
Pocock's contribution is the form of these ideas as agent skills, which we adopted.

## Our changes to his work

- The ADR template follows MADR-lite (Context · Decision · Consequences · Alternatives ·
  Status), with a deterministic step that registers each ADR in the docs index.
- Each ADR states its strength: `Holds`, `Revisit if` and `Amendments`. Only an invariant
  that names its enforcing test binds (lore ADR 0015).
- Glossary entries and ADRs follow our writing rules (`lore style show writing-rules`).
- The three grilling skills became one skill with a plain mode and a "with docs" mode.

## Our own

`orient`, `to-epic` (PRD file, epic tracker, roadmap table), `build` (loop, issue and
epic modes, the ledger, review by risk level), `document`, `handover`, `file-issue`,
`triage`, `debug`, the model tiers in `TIER-DELEGATION.md`, and the `lore` plugin itself.
`triage`, `debug` and `handover` share a name or a purpose with upstream skills
(`triage`, `diagnosing-bugs`, `handoff`), but not their text.

## Licence of the upstream work

```
MIT License

Copyright (c) 2026 Matt Pocock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
