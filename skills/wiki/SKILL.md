---
name: lore:wiki
description: Write one wiki entry after the person says yes — search the wiki first, extend an entry that fits or write a new one, then commit and push it with `lore wiki commit`. Use for research, a scoping of options, an explanation for a human reader or draft paper material that has no home in a repo artifact. Offer at a stop of the work; also use when the person asks for a wiki entry or says "put this in the wiki". Run with "/lore:wiki".
---

# Wiki entry

A **wiki entry** is one note of a wiki that a person or an agent wrote on
purpose. The yes of the person in the session is the review. The person has
the context in mind at that moment. See the amendment of 2026-11-12 in
ADR 0012 (agents file facts as repo artifacts).

## What goes into a wiki entry

- Research, for example a larger online search with its sources.
- A scoping of options.
- An explanation that a human reads again.
- Draft material for a paper.

A decision, a defect, an experiment result and the story of the work are no
wiki entry. `lore style show filing` names their homes.

## 1. Offer the entry

- Offer at a stop of the work, not during a task.
- Offer in one line: the topic and the wiki. Example: "Keep the scoping of
  the three queue options as a wiki entry in `ccat`?"
- Offer at most once per piece of material. Do not offer again after a no
  or after no answer.
- Without a yes, write no file.

When the person asks for an entry directly, the request is the yes.

## 2. Search the wiki

Call `lore_search` on the topic before you write.

- When an existing entry covers the topic, extend that entry. Read it with
  `lore_read` first. Keep its structure and add to it.
- Otherwise write a new entry where the existing structure of the wiki
  suggests. Look at the folders and the entries next to the search hits.

Lore prescribes no folder and no entry kind. The structure of a wiki grows
from use. Follow the wiki's `CLAUDE.md` when it has one.

## 3. Write the entry

- A frontmatter block with a `description`: one sentence that says what the entry
  holds.
- The date of the entry, and the date of each extension.
- A sources list: each URL, paper, file path, issue or PR the entry builds
  on. Write no claim that a source does not carry.
- Write for a reader who was not in the session. Follow
  `lore style show writing-rules`.
- Link other entries of the same wiki with `[[wikilinks]]`. Link no note of
  another wiki.

## 4. Commit and push

Run `lore wiki commit <path>` with the path of the entry. The command:

- runs `lore wiki check` on the entry, and commits nothing on an error for
  the entry. Errors in other notes do not block.
- runs `git init` in a wiki that is no git repo.
- commits the other uncommitted edits of the person first, in their own
  commit.
- merges the remote changes first and pushes after the commit, when the wiki
  has a remote.
- prints the path of the entry and the commit hash.

On an error for the entry, fix the entry and run the command again. On a
merge conflict, the local commit stays and the command names the conflicting
paths. Tell the person the paths. Do not resolve the conflict yourself. When
the push fails, the local commit stays too, and the command names the reason.
The next run of the command pushes the commit.

## 5. Report

Tell the person in one line: the path of the entry, the commit hash, and
whether the push ran.
