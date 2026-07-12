**Segmentation and typed-fact extraction landed; the renderer is next.**

## Done

- Beat-aligned segmentation landed as an indices-only call. — pr 288 ✓ @7
- Typed-fact extraction landed with three deterministic lints. — pr 289 (unchecked) @21

## Decisions recorded

- Extraction runs at session end, never per flush. Why: Which facts matter is only knowable backward, at the ending. — commit 41cab11 ✓ @9

## Findings

- Observed in session: A fact carrying a comment opener parsed back as a second, forged fact. @14

## Open

- Reported in session: Sketched the renderer's section order. @12
- Left open in session: Ref verification against git and gh is not implemented. @30
- Coverage gap: turns 40–48 are not covered by this note (composition failed at session end). @40
