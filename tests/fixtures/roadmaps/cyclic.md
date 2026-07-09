## Roadmap
Cyclic: 1 → 3 → 2 → 1. Columns are valid, refs are fully-qualified, and every
edge resolves, but the blocked-by graph has a cycle.

| # | Feature | Issue | Repo | Type | Blocked by |
|---|---------|-------|------|------|------------|
| 1 | Feature A | ccatobs/widget#12 | widget | AFK | #14 |
| 2 | Feature B | ccatobs/widget#13 | widget | AFK | #12 |
| 3 | Feature C | ccatobs/widget#14 | widget | AFK | #13 |
