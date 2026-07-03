# How to publish briefings to Matrix

End-to-end recipe for a wiki briefing landing in a Matrix room. Run
via `lore briefing --wiki <wiki>` (the CLI does gather + render +
publish + ledger update in one shot).

---

## What you need

1. A Matrix homeserver and a **dedicated bot user** (don't reuse a
   personal account — the bot's password is what `lore-sink-matrix
   login` exchanges for an access token).
2. The room ID where briefings should go (Element → room settings →
   Advanced → Internal room ID).
3. `lore[sinks]` extras installed (provides `matrix-nio` +
   `markdown`):

   ```bash
   pipx inject lore "lore[sinks] @ git+https://github.com/buchbend/lore.git"
   ```

   Or if you installed Lore as `git+...` already, reinstall with the
   extras:

   ```bash
   pipx install --force "git+https://github.com/buchbend/lore.git#egg=lore[sinks]"
   ```

---

## Step 1 — one-time login (writes credentials to disk)

```bash
LORE_MATRIX_HOMESERVER=https://matrix.example.org \
LORE_MATRIX_USER_ID="@lore-bot:matrix.example.org" \
LORE_MATRIX_ROOM_ID="!ignored:matrix.example.org" \
lore-sink-matrix login
```

The `LORE_MATRIX_*` env vars are only read by the `login` flow so it
knows which homeserver to authenticate against. After login, the
access token + device id land at:

```
~/.local/share/lore/matrix-credentials.json
```

That file is the **only** secret store. It never enters the wiki
repo. (Mode is created with default umask — `chmod 600` it if your
threat model needs it; Lore does not enforce.)

---

## Step 2 — wiki config in `.lore-briefing.yml`

Inside the wiki you want to publish from:

```bash
$EDITOR $LORE_ROOT/wiki/<wiki>/.lore-briefing.yml
```

```yaml
sink: matrix
matrix:
  homeserver: https://matrix.example.org
  user_id: "@lore-bot:matrix.example.org"
  room_id: "!abc123:matrix.example.org"
```

These values are non-secret identifiers (homeserver URL, bot user ID,
room ID). They live in the wiki repo so the team can diff and review
them. Commit the file like any other wiki note.

> The flat top-level form (no `matrix:` nesting) is also accepted for
> backward compat but emits a deprecation warning. New configs should
> use the nested form.

---

## Step 3 — publish

### From the CLI

```bash
lore briefing --wiki <wiki>
```

Or, for the publish step alone:

```bash
lore briefing publish --sink matrix --wiki <wiki>
```

The `--wiki` flag tells the CLI to load `.lore-briefing.yml` and
thread it through to the matrix sink.

### Manually

```bash
echo "## test briefing" | lore briefing publish --sink matrix --wiki <wiki>
```

### Scheduled publish

There is no automatic daily publish — `lore briefing publish` is a
manual (or externally scheduled, e.g. `cron` / `/schedule`) command.
Whatever triggers it reuses the same `.lore-briefing.yml` via
`gather`'s `sink_config` field, so no env vars are required beyond
the one-time Matrix login above.

---

## Resolution order (which value wins?)

Mirroring the OpenAI backend pattern (`docs/architecture/config.md`):

1. **Env var** (`LORE_MATRIX_HOMESERVER` / `LORE_MATRIX_USER_ID` /
   `LORE_MATRIX_ROOM_ID`) — one-shot debug override.
2. **`.lore-briefing.yml`** field (nested under `matrix:` preferred,
   flat top-level accepted with warning).
3. **Error** — no implicit defaults for required IDs.

This means a debug session can override a single field via
`LORE_MATRIX_ROOM_ID=!debugroom:... lore briefing publish ...` without
editing the yaml.

---

## Sink mismatch protection

If `.lore-briefing.yml` declares `sink: matrix` but you invoke the
CLI with `--sink markdown`, the publish refuses with exit code 2:

```
lore: sink mismatch: --sink='markdown' but .lore-briefing.yml sets sink='matrix'
```

This stops a stale yaml from quietly hijacking a different sink.

---

## Troubleshooting

**`No matrix credentials at ~/.local/share/lore/matrix-credentials.json`**
Run `lore-sink-matrix login` first.

**`matrix sink: missing required field(s) ...`**
Either the yaml is missing the field (or you forgot `--wiki`), or the
yaml is malformed. Run `lore briefing gather --wiki <name>` and
inspect `sink_config` in the returned envelope to confirm the loader
sees what you expect.

**Bot joins room but no message appears**
The bot user must be in the room. Invite it from a member account
(matrix permissions are independent of file config).

**`access_token` rotated / 401 on send**
Re-run `lore-sink-matrix login` to refresh credentials. The
homeserver doesn't currently auto-refresh tokens.

---

## Related

- `docs/architecture/config.md` — full precedence table for every
  Lore config source.
- `lore briefing --help` — CLI reference for the gather + publish flow.
- `lib/lore_core/briefing/sinks/matrix.py` — the sink itself; module
  docstring reiterates the resolution order.
