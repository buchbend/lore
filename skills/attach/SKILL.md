---
name: lore:attach
description: Attach the current repo or folder to a Lore wiki scope.
  Dispatches `lore attach accept|decline|manual` based on the current
  consent state. Non-destructive; idempotent. Run with "/lore:attach".
user_invocable: true
---

# Attach-to-wiki

Attachment state lives in `$LORE_ROOT/.lore/attachments.json`, keyed by
repo path. Offers live in per-repo `.lore.yml` files. The state machine
(`lore_core/consent.py`) drives every decision — this skill just
dispatches the right CLI verb.

The mechanical work lives in `lore attach {accept,decline,manual,offer}`
and `lore attachments {ls,show,rm}`. This skill only interprets the
user's intent.

## Workflow

### 1. Classify state

Run these two probes from the current directory:

```
ls .lore.yml 2>/dev/null        # offer present?
lore attachments show . --json  # attachment row present?
```

Four cases:

- **Offer + matching attachment** → ATTACHED. Report the covering
  wiki/scope from `lore attachments show .` and stop. Nothing to do.
- **Offer, no attachment** → OFFERED. Go to §2 (accept or decline).
- **Attachment, no offer** → MANUAL. Already attached without a
  shareable offer. Report and stop.
- **Neither** → UNTRACKED. Go to §3 (manual attach).

If the user's message includes "decline" / "dismiss", skip directly to
§2b. If it includes "detach" / "remove", hand off to `/lore:detach`.

### 2a. Accept an offer (OFFERED or DRIFT)

Show the offer (`cat .lore.yml`), confirm once, then:

```
lore attach accept
```

The CLI walks up for `.lore.yml`, writes the attachment row with the
offer fingerprint, and ingests the scope chain into `scopes.json`.
Exits 1 on scope conflicts — surface the error to the user verbatim.

DRIFT (attached under an older fingerprint) uses the same command — the
CLI will overwrite the row.

### 2b. Decline an offer

```
lore attach decline
```

Records a `(repo_root, offer_fingerprint)` row so future
SessionStart banners stay silent. A changed `.lore.yml` produces a new
fingerprint and will re-prompt — that's intentional.

### 3. Manual attach (no .lore.yml)

Ask the user for `wiki` and `scope` (one question each, or combined if
obvious from context). Then:

```
lore attach manual --wiki <wiki> --scope <scope>
```

Scope format is colon-separated, e.g. `ccat:data-center:data-transfer`.
Suggest existing scopes from `$LORE_ROOT/wiki/<wiki>/_scopes.yml` if
it exists.

### 4. Optional: publish a shareable offer

If the user wants others to be able to accept the same attachment via
`.lore.yml`, mention `lore attach offer --wiki <wiki> --scope <scope>`
— it writes a `.lore.yml` at cwd. Do not run it unprompted.

## Three-question ceiling

At most:

1. Confirm accept/decline/manual intent (when ambiguous).
2. Wiki + scope (manual path only).
3. Append the scope to `_scopes.yml` (manual path only, if not already
   present). Stage the edit; do not commit.

If the user explicitly says "accept", "decline", or names a wiki+scope
upfront, skip the questions entirely.

## Output

Print what the CLI printed — green success line with the attachment
path, wiki, and scope. Do not re-narrate.

## Important rules

- **Never edit `attachments.json` directly.** Use the CLI.
- **Never edit `CLAUDE.md`.** Post-0.3.0 the `## Lore` section is gone;
  attachments live in `$LORE_ROOT`, not in the repo.
- **Non-git folders are first-class.** `lore attach manual` works on
  any directory.
- **Monorepo rule.** `lore attachments show <path>` uses longest-prefix
  match. A parent attachment covers children unless they have their own.
- **Idempotent.** Re-running `accept` with the same offer is a no-op.

## Related

- `/lore:detach` — remove the attachment row (and the legacy `## Lore`
  section if any CLAUDE.md still has one)
- `lore attachments ls` — every attachment on this host
- `lore attachments show <path>` — which attachment covers a path
- `lore attachments rm <path>` — delete an attachment row (no prompt)
- Concept: `local-lore-state` (private wiki)
- Concept: `scopes-hierarchical` (private wiki)
