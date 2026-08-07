# Session note format — title and body shape

**Nothing writes one any more.** The compose pipeline retired and the code
that rendered a note is deleted. This file describes notes a vault may still
hold, so a reader — human, or `read_note` in `lore_core/note_document.py` —
knows what they are looking at. Everything below is in the past tense on
purpose. `CONTEXT.md` owns the vocabulary (buffer, flush, chapter, fact,
ledger, disclaimer).

A note was written **once, at session close** — the whole file below appeared
in one step. Nothing was rendered while the session ran.

## Title: `scope: name`

A note's frontmatter `title` was a placeholder at creation (e.g. `proj:x
session — 2026-07-10`) — the first heartbeat fired before there was any
content to name.

At close, one bounded LLM call wrote the session's **headline** from the
extracted fact table, and the headline named the note, replacing the
placeholder with `<scope>: <name>`.

- **scope first** — the linkage scope (repo/project slug, e.g. `proj:x` or
  `ccat:data-center`) so a list of notes sorts and scans by *where*, not by
  an arbitrary date stamp.
- **name second** — the headline, minus its trailing period.

Example: `proj:x: Traced the flush race`.

The same headline slugged the filename, so title and filename always name the
same topic. An empty headline left both at their placeholder.

## Body: disclaimer, reading, ledger

```
> **Lab-notebook session note — not authoritative.** …          ← DISCLAIMER

**Traced the flush race.**                                       ← headline

## Done
- The chunker landed. — commit 1111111 ✓ @2
- The extraction PR merged. — pr 289 (unchecked) @4

## Decisions recorded
- The ledger stays append-only. Why: The grounding tier survives every
  rewrite. — file docs/adr/0003.md ✓ @10

## Findings
- The gate scans the marker too. — file lib/lore_core/publish_gate.py ✓ @18
- Observed in session: The local model returns empty on oversized prompts. @24

## Open
- Agreed in discussion, recorded nowhere: Curators never edit a note body.
  — The body is derived state. @16
- Coverage gap: turns 40–71 are not covered by this note (extraction failed
  at session end). @40

## Ledger                                                        ← LEDGER_HEADING
<!-- lore:chapter 1 @0-39 -->
<!-- lore:fact {"anchor": 2, "kind": "done", …} -->
**The chunker landed.**
> "…verbatim quote from turn 2…"
@2
…
```

Everything above `## Ledger` is **derived state** — a pure function of the
ledger below it, recomputed in full at every close (ADR 0003). The ledger is
append-only. The same ledger renders to the same bytes, always.

### The four sections

Fixed order, which is the reading order: **Done**, **Decisions recorded**,
**Findings**, **Open**. Items sort by anchor. Empty sections are dropped
entirely.

- A `progress` fact reads under **Open** — it is unfinished work at the
  session's ending — *unless* its thread later reached a terminal (`done`)
  fact, in which case the render suppresses it. Suppression is a decision of
  the reading only; the fact stays whole in the ledger.
- A `decision` with no refs is not a decision: it routes to **Open** as
  *"Agreed in discussion, recorded nowhere: … — <why>"*.
- Every chapter that is not a `facts` chapter — a failed marker, a withheld
  marker, or a prose chapter from a legacy note — renders as a one-line
  **coverage gap** under Open, so a partial reading never presents itself as
  complete.

### A rendered line

```
- [lead] <text> [Why: <why>] [— <type> <value> <stamp>, …] @<anchor>
```

The `text` and the `why` are the model's. **Every other word is code's**
(ADR 0004), chosen by a template keyed on `(kind, verification)`:

| Verification | Lead | Ref stamp |
| :--- | :--- | :--- |
| verified | *(none — the line states itself)* | `✓` |
| unchecked | *(none)* | `(unchecked)` |
| missing | `Claimed in session, ref not found:` | `(not found)` |
| no refs | `Reported done in session, recorded nowhere:` / `Reported in session:` / `Agreed in discussion, recorded nowhere:` / `Observed in session:` / `Left open in session:` (by kind) | — |

## The ledger

One chapter per extracted chunk, opened by an HTML-comment delimiter carrying
its turn span. Each fact is stored twice: as a machine-readable
`<!-- lore:fact {…} -->` marker (the copy `read_note` parses back) and as
human-readable text — the bold statement, its code-attached verbatim quote
from the anchor turn, and the `@N` anchor. Drill-down chain: rendered line →
fact in the ledger → quote → archived transcript turn.

Notes written before typed facts carry **prose chapters** of bold-lead topic
blocks instead of fact markers. They still parse and still render; contributing
no facts, their spans appear as coverage gaps in any note that mixes them with
facts. They are not migrated (fix-forward).
