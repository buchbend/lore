# ADR 0016: Routing by folder scope replaces attach

- **Status:** Accepted
- **Date:** 2026-10-11
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0016](../prd/0016-routing-settings-and-setup.md), epic [#466](https://github.com/buchbend/lore/issues/466);
  design round "Lore Full Circle" (2026-10-11);
  ADR [0009](0009-privacy-boundary-is-locality.md) (locality),
  ADR [0017](0017-the-logbook.md) (the sinks this routing selects)

## Holds

- **Default:** a scope is a folder in the person's lore config. It sets the
  wiki, sinks, sources and tracker for every repo below the folder. The
  longest matching folder wins.
- **Default:** a scope may list git remotes. Lore then matches a repo by its
  remote too. When a path match and a remote match name different scopes,
  the path match wins.
- **Default:** a repo's committed `.lore.yml` holds exceptions only. It names
  sinks, sources or a tracker by name, or it opts out of a sink.
- **Default:** the reporting boundary. A repo file names a sink and does not
  define one. Only the person's own config defines a sink's command or
  host. When a route names a sink the person has not defined, Lore uses the
  `file` sink and `lore status` says why. The rule holds for a sink name
  from any origin: `.lore.yml`, a scope, the defaults or an environment
  variable.
- **Default:** config files hold the names of secrets, not their values. The
  values live in a `0600` secrets file outside any git worktree, or in an
  environment variable.
- **Incidental:** the person's config lives in one file under
  `~/.config/lore/`. It absorbs the root config, `attachments.json` and
  `scopes.json`.
- **Incidental:** the old commands (`attach`, `install`, `init`, `scopes`)
  go in one hard cut. Lore had one user at the time.

The epic that builds this promotes the reporting boundary and the secrets
rule to invariants, each with its test.

## Revisit if

- A colleague's folder layout does not fit the scope folders and remote
  matching cannot fix it.
- A team needs a sink defined once for every member, not once per person.

## Context

The `lore attach` flow carried an offer file, a consent state machine,
fingerprints and two state files (`attachments.json`, `scopes.json`). The
flow existed so that session notes reached the right wiki. ADR 0007 retired
session notes. Since ADR 0012, Lore writes to a wiki only on purpose. The
heavy per-repo step now guards nothing.

The resolver already matched the longest path prefix
(`lib/lore_core/state/attachments.py:102`). It reached that rule only through
attach. The colon namespace inside a wiki (`ccat:data-center`) existed to
route notes.

Two needs stay. A session needs to know which wiki, tracker, sources and
sinks apply. A team needs a reporting boundary: one person's private work
does not reach a team logbook.

## Decision

- The person registers a wiki once, then names scope folders such as
  `~/git/ccatobs`. A scope folder does not have to be a repo.
- `resolve_route(cwd)` replaces `resolve_scope(cwd)`. It reads the nearest
  `.lore.yml`, then the longest scope folder, then a remote match, then the
  defaults.
- Attach, offers, consent, fingerprints, the `## Lore` block, the
  `backend` key and the two JSON state files retire.
- `lore setup` registers wikis and scopes, and it repairs a broken setup.

## Consequences / Trade-offs

- Easier: a new repo under a scope folder needs no step. One command sets up
  and repairs.
- Easier: the reporting boundary is a lookup in the person's own config.
  A cloned repo cannot send entries to a host the person did not set up.
- Harder: a person who clones a team repo outside the scope folder gets no
  routing. The fix is a remote match on the scope.
- Harder: the hard cut breaks scripts that call the old commands.

## Alternatives considered

- **Keep attach and make it lighter.** Rejected: every remaining part of
  attach (offer, consent, drift) guards a write that no longer happens.
- **Declare the team sink only in the wiki's shared `_scopes.yml`.**
  Rejected: it couples the logbook to a wiki the person may not use, and the
  colon namespace goes away.
- **Declare the team sink with its host in the committed `.lore.yml`.**
  Rejected: a cloned repo could then send a person's entries to any host.
- **Match by git remote only.** Rejected as the default: it fails for repos
  without a remote. It stays as an option on each scope.
