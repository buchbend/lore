**Segmentation and typed-fact extraction landed; the renderer is next.**

## Done

- Beat-aligned segmentation landed as an indices-only call. @7
- Typed-fact extraction landed with three deterministic lints. @21

## Decisions recorded

- Extraction runs at session end, never per flush. Why: Which facts matter is only knowable backward, at the ending. @9

## Findings

- A fact carrying a comment opener parsed back as a second, forged fact. @14

## Open

- Sketched the renderer's section order. @12
- Ref verification against git and gh is not implemented. @30
- Coverage gap: turns 40–48 are not covered by this note (composition failed at session end). @40
