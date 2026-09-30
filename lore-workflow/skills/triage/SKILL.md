---
name: lore-workflow:triage
description: Review every open issue and open PR of one repo against the code at HEAD. Find what
  is outdated, already done, not worth doing, duplicated or dangling, and present it as one
  filterable page for discussion. Review only — nothing is commented, labelled, closed or pushed
  until the user says so. Use when the user asks to triage, clean up or review the issues or the
  backlog of a repo.
---

# Triage

Input: `<owner>/<repo>`. Output: one page and a short chat summary. Then wait for the discussion.

**Review only.** Do not comment, label, close, commit or push until the user says so. Good
enough is good enough: a verdict with one piece of evidence beats a perfect one.

## 0. Before you start

Tell the user in a few lines what else you will look out for (the list in § 2, plus anything
the repo suggests). Ask for context only when a verdict depends on it: a deploy you cannot see,
a planned rewrite, an owner who left. Otherwise start.

## 1. Record the repo state

```bash
gh repo view <owner>/<repo> --json defaultBranchRef,pushedAt
gh issue list -R <owner>/<repo> --state open --limit 1000 \
  --json number,title,labels,createdAt,updatedAt,author,comments > issues.json
gh pr list -R <owner>/<repo> --state open --limit 200 \
  --json number,title,headRefName,baseRefName,isDraft,mergeable,updatedAt,closingIssuesReferences > prs.json
git log --since="1 month ago" --oneline origin/<default> | wc -l
```

Record the default and release branches and whether they are level (`git rev-list
--left-right --count a...b`). Record the commits in the last month, the open issue and PR counts
and the oldest issue. Record each major component that was removed or replaced (`git log
--diff-filter=D --stat`, the CHANGELOG, ADRs with status `Superseded`). An issue built on a removed component is likely
outdated.

## 2. What to check per issue

Read the body and the comments. Check them against the code at HEAD (`lore codemap`, `rg`) and
against merged PRs that reference the issue (`gh pr list --state merged --search "<n>"`). Cite
evidence as `path:line`, a commit or a PR. Look for:

- done by code or a merged PR, still open;
- duplicates and overlaps: name the issue to merge into (`lore_search` finds near matches);
- blocked on another issue, another repo or an open decision (ADR);
- belongs in another repo;
- a security exposure now, whatever its age;
- a vague "someday" issue with no owner and no activity;
- a file, flag, command or symbol the issue names that no longer exists;
- a request an ADR rejected or a `Holds` invariant forbids;
- a sub-issue whose epic closed, or an epic whose sub-issues all closed;
- agent-filed issues (label `agent-filed`) that repeat each other.

Open PRs: mergeable? stale? superseded by a merged PR? Does it fix an open issue? Recommend
`merge`, `rebuild`, `close` or `decide`.

## 3. Fan out by theme

Split the issues into three to five themes that fit this repo. Give each theme one subagent with
a lens. Default lenses:

| Lens | Takes | Tier |
|---|---|---|
| Security reviewer | secrets, auth, access, exposure | `strong` |
| Architect | CI/CD, build, deploy, design | `strong` |
| Ops/SRE reviewer | runtime, backups, monitoring, docs | `mid` |

Pass `lore tier resolve <tier>` as each spawn's model (see
[TIER-DELEGATION.md](../../TIER-DELEGATION.md)). Split a theme above about 40 issues into two
spawns. Each brief carries the repo state from § 1, the issue numbers and the checklist in
§ 2. It also carries the verdicts below, the review-only rule and this return shape, one JSON
object per issue:

```json
{"n": 123, "kind": "issue", "title": "…", "area": "ci", "verdict": "Backlog",
 "why": "2–3 sentences", "evidence": ["lib/x.py:40", "PR #88"],
 "depends_on": ["#120"], "next": "one step", "verified": false, "live_check": false}
```

Each subagent also returns up to three risks it saw in the code that no issue tracks, each with
evidence. It reads and runs read-only commands only.

## 4. Verdicts

Use exactly these:

- **Critical** — prod or security exposure now.
- **Do next**
- **Backlog**
- **Optional**
- **Close: done**
- **Close: drop / move / duplicate** — name the target for a move or a duplicate.

Tag `verified` when you confirmed the claim yourself. Tag `live check` when the verdict depends
on the running system and not the repo.

## 5. Re-check

Re-check yourself every `Critical`, every `Close: done`, and the claims that surprised you most.
Set `verified` on the ones you confirmed. Change a verdict the evidence does not carry.

## 6. Coverage

Merge the returns into `triage.json`. Every open issue appears exactly once:

```bash
jq -r '.[].number' issues.json | sort -n > open.txt
jq -r '.[] | select(.kind=="issue") | .n' triage.json | sort -n > seen.txt
comm -3 open.txt seen.txt        # empty output: each issue once
jq -r '.[] | select(.kind=="issue") | .n' triage.json | sort -n | uniq -d   # empty: no repeats
```

Fix gaps and repeats before you build the page.

## 7. The page

Build one page from `triage.json` as [PAGE.md](PAGE.md) describes. Publish it as an artifact
when the harness offers one. Else write `triage-<repo>.html` in the scratchpad and name the
path.

## 8. Report and wait

In chat: the repo state in one line, the counts per verdict, the `Critical` list, the three
biggest cleanups, and the page link. Then stop and wait for the discussion.

After the user decides, act only on what they approved. Write each comment, new issue and
close reason through [`file-issue`](../file-issue/SKILL.md). Close only with a comment that
names the evidence, and set `state_reason` (`completed` or `not_planned`).
A human's issue or PR changes state only when the user approved that number in the
conversation. The session rule "close only issues you opened" holds for everything else.
