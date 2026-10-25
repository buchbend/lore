---
name: lore:checkout
description: Switch every local checkout of a system to one branch, producers
  first, using the system map. Fast-forward only; a checkout with local changes
  is skipped, never stashed or reset. Reports the pinned edges a switch does
  not reach. Run with "/lore:checkout <branch> [system]".
user_invocable: true
---

# Checkout a system

A system map (`<wiki>/systems/<name>.md`, PRD 0019) lists the repos of one system and the
edges between them. This skill puts each local checkout of the system's repos on one
branch, such as `main` or the feature branch of a change across repos. It reads the map for
the order and for the pins.

The arguments are the branch and, optionally, the system: the map's note name. Without a
system, the skill works on every map. Without a branch, ask for one.

Never discard work. Never stash, reset, rebase, force or delete a branch. Never push. Only
`git fetch`, `git switch` and `git pull --ff-only` run.

## Steps

1. Run `lore map status --system <system> --json` and fetch each checkout it lists:
   `git -C <path> fetch --prune`. A fetch that fails keeps the local refs; name the
   checkout in the report.
2. Run `lore map status --branch <branch> --system <system> --json`. The output holds
   `order`, `checkouts`, `no_checkout` and `pinned`. `checkouts` comes producers first; keep
   that order, so a consumer switches after the repos it reads.
3. For each checkout, act on its `action`:
   - `switch`: `git -C <path> switch <branch>`. Where only `target_remote` has the branch,
     `git switch` creates the local branch that tracks it. Then `git -C <path> pull --ff-only`.
   - `pull`: the checkout is on the branch. `git -C <path> pull --ff-only`.
   - `default`: no local or remote branch of that name. Switch to `default_branch` and pull
     with `--ff-only`. Name the repo under "stayed on the default branch". Where
     `default_branch` is empty, leave the checkout as it is and report it.
   - `skip`: the checkout holds `dirty` changed files. Leave it as it is.
   - `blocked`: another worktree, `target_elsewhere`, holds the branch. Leave the checkout
     as it is and name the worktree.
   A `pull --ff-only` that fails leaves the branch diverged from its upstream. Leave it and
   report the repo; do not merge or rebase.
4. Read `pinned`. Each entry is an edge whose consumer fixes the producer's version by `sha`,
   `tag`, `image` or `package`. A producer that moved to the branch does not reach that
   consumer until its pin moves. Name each such edge, with the file the pin lives in where
   you find it. Do not edit the pin. Offer it as a follow-up.
5. Name each repo in `no_checkout`: the map lists it, and no scope folder holds a checkout.

## Report

One table, in the order of `checkouts`: repo, branch before, branch after, what ran. Below it,
the skipped and blocked checkouts with their reason, the repos that stayed on the default
branch, the pinned edges the switch does not reach, and the repos without a checkout.

The systems page shows the branch of each checkout: `lore stats --page systems`.
