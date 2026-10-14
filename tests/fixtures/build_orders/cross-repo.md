## Roadmap
A cross-repo epic. Blocked-by uses fully-qualified refs because the same issue
number (#5) exists in two different repos, so a bare `#5` would be ambiguous.

| # | Feature | Issue | Repo | Type | Blocked by |
|---|---------|-------|------|------|------------|
| 1 | Producer API | ccatobs/producer#5 | producer | AFK | — |
| 2 | Consumer client | ccatobs/consumer#5 | consumer | AFK | ccatobs/producer#5 |
| 3 | Consumer UI | ccatobs/consumer#6 | consumer | HITL | ccatobs/consumer#5 |
