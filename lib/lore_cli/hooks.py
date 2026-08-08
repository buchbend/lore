"""Claude Code hook dispatch — the seven `lore hook ...` entry points.

Each command is a thin shell: resolve cwd, read the stdin payload, check the
suppression toggles, delegate, emit. The work itself lives in the domain
layer — :mod:`lore_core.session_start` (banner assembly) and
:mod:`lore_curator.capture_routing` (transcript registration).

What stays here is the hook contract itself: the crash shield, the JSON
envelope Claude Code expects per event, stdin payload parsing, the PID-keyed
context-log cache, and the process-ancestry walk that identifies the Claude
Code process.

    lore hook session-start [--cwd PATH]
    lore hook pre-compact  [--cwd PATH]
    lore hook stop
    lore hook user-prompt-submit
    lore hook spawn-model-gate
    lore hook context-log
    lore hook capture --event ...

Exposed via `lore_cli.__main__` dispatch (see subcommand wiring there).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from lore_core.config import get_lore_root, get_wiki_root
from lore_core.io import atomic_write_text
from lore_core.scopes import (
    load_scopes_yml,
    subtree_siblings,
    walk_scope_leaves,
)
from lore_core.session_start import MAX_CONTEXT_CHARS
from lore_core.session_start import load_directive_lines as _load_directive_lines
from lore_core.session_start import maybe_auto_pull_for_scope as _maybe_auto_pull_for_scope
from lore_core.session_start import maybe_auto_push_for_scope as _maybe_auto_push_for_scope
from lore_core.session_start import offer_notice_line as _offer_notice_line
from lore_core.session_start import pre_compact_text as _pre_compact
from lore_core.session_start import render_capture_state_block as _render_capture_state_block
from lore_core.session_start import render_project_orientation as _render_project_orientation
from lore_core.session_start import session_start_text as _session_start
from lore_curator.capture_routing import (
    register_pending_transcripts as _register_pending_transcripts,
)
from lore_curator.capture_routing import route_capture as _route_capture

from lore_cli.context_cache import (
    _cache_dir,
    _cache_path_for_pid,
    _claude_code_pid,
    _gc_sessions_cache,
    _legacy_cache_path,
)


def __getattr__(name: str):
    """Backwards-compat shim — keep `from hooks import LORE_DIRECTIVE_LINES`."""
    if name == "LORE_DIRECTIVE_LINES":
        return _load_directive_lines()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Scope helpers re-exported here so tests that monkeypatch the underscore-
# prefixed names continue to intercept calls.
_walk_scope_leaves = walk_scope_leaves
_load_scopes_yml = load_scopes_yml
_subtree_siblings = subtree_siblings


# ---------------------------------------------------------------------------
# `lore hook context-log` — read-only cache lookup for the /lore:context skill
# ---------------------------------------------------------------------------


def _context_log() -> str:
    """Read the PID-scoped context log. Pure file read — no live I/O."""
    cc_pid = _claude_code_pid()
    if cc_pid is not None:
        path = _cache_path_for_pid(cc_pid)
        if path.exists():
            try:
                return path.read_text(errors="replace")
            except OSError:
                pass
    legacy = _legacy_cache_path()
    if legacy.exists():
        try:
            body = legacy.read_text(errors="replace")
        except OSError:
            body = ""
        if body:
            return (
                "_(showing the most recent context log — your current "
                "Claude Code session may not have written one yet)_\n\n"
                + body
            )
    return "lore: no context log found. SessionStart may not have fired. Run `lore doctor`.\n"


# ---------------------------------------------------------------------------
# Stop hook (timeout-style prompt)
# ---------------------------------------------------------------------------


def _stop() -> str:
    """No-op — session capture is automatic."""
    return ""



# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def _first_line(text: str) -> str:
    """Return the first non-empty line, stripped."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _emit(hook_event: str, text: str, *, plain: bool) -> None:
    """Emit hook output in the format Claude Code expects.

    The authoritative schema (docs.claude.com, 2026-04) differs per event:

      SessionStart — both `systemMessage` (top-level, visible banner to
        the user and injected as context to Claude) and
        `hookSpecificOutput.additionalContext` (quietly injected full
        body for the agent to consume) are allowed. We use both so the
        user sees a one-liner in the transcript and the agent gets the
        full focus/open-items context.

      PreCompact — `hookSpecificOutput` is NOT allowed for this event.
        Only top-level fields (`systemMessage`, `continue`, etc.) are
        valid. We pack the open-items summary into `systemMessage` —
        which Claude Code injects as context on the next turn per the
        docs, so it survives the compaction boundary.

      Stop — `hookSpecificOutput` is NOT allowed. Only top-level fields.
        We emit the hint via `systemMessage`.

    `--plain` dumps raw text to stdout — used by the /lore:context skill and
    for manual inspection.
    """
    if plain:
        if text:
            sys.stdout.write(text)
            if not text.endswith("\n"):
                sys.stdout.write("\n")
        return
    if not text:
        return

    one_liner = _first_line(text)
    envelope: dict

    if hook_event == "SessionStart":
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        ts_hm = _dt.now(_UTC).strftime("%H:%M")
        log_text = f"── SessionStart {ts_hm} ──\n{text}"
        cc_pid = _claude_code_pid() or os.getppid()
        try:
            atomic_write_text(_cache_path_for_pid(cc_pid), log_text)
        except OSError:
            pass
        try:
            _gc_sessions_cache()
        except OSError:
            pass
        context_text = text
        if len(context_text) > MAX_CONTEXT_CHARS:
            context_text = (
                context_text[: MAX_CONTEXT_CHARS - 40]
                + "\n... (truncated — /lore:context for full)"
            )
        envelope = {
            "systemMessage": one_liner,
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context_text,
            },
        }
    elif hook_event == "Stop":
        envelope = {"systemMessage": text.strip()}
    else:
        # PreCompact and any future events — systemMessage only.
        envelope = {"systemMessage": text}

    sys.stdout.write(json.dumps(envelope))
    sys.stdout.write("\n")


_HOOK_EVENT = {
    "session-start": "SessionStart",
    "pre-compact": "PreCompact",
    "stop": "Stop",
}


def _hook_failure_banner(
    hook_event: str,
    exc: BaseException,
    *,
    log_path: Path | None = None,
) -> str:
    """Build a user-friendly diagnostic for a crashed hook.

    Claude Code surfaces stderr + traceback as
    "Failed with non-blocking status code" when a hook exits non-zero.
    That's noise without a next step. The shield catches the exception,
    feeds the banner through `_emit` like normal, and exits 0 — the user
    sees an actionable message instead of a traceback.

    Common causes named explicitly: stale install (templates / package
    data missing under pipx wheels) and binary-vs-plugin-cache drift.

    ``log_path`` (when provided) appears on a final ``Full traceback:``
    line so the maintainer / user can attach the file to a GitHub issue.
    Persisted by ``_crash_log.write_crash`` from both the per-hook
    shield and the ``__main__`` top-level backstop.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc) or "(no message)"
    # Truncate noisy paths/values so the banner stays readable.
    if len(exc_msg) > 200:
        exc_msg = exc_msg[:197] + "..."
    banner = (
        f"⚠ lore {hook_event} hook failed: {exc_name}: {exc_msg}\n"
        "\n"
        "Likely causes + fixes:\n"
        "  • Stale install (e.g. templates not bundled): "
        "[bold]lore install --upgrade[/bold] (or re-run install.sh).\n"
        "  • Binary vs plugin-cache drift: "
        "[bold]lore doctor[/bold] flags it and prints the exact command.\n"
        "  • If the error persists, file an issue: "
        "https://github.com/buchbend/lore/issues\n"
    )
    if log_path is not None:
        banner += f"\nFull traceback: {log_path}\n"
    banner += (
        "\nLore continues without its SessionStart banner — your session "
        "is otherwise unaffected."
    )
    return banner


def _shield_hook(typer_event: str):
    """Decorator: wrap a hook entry point so unexpected exceptions
    surface a friendly diagnostic via `_emit` and exit 0.

    Local try/except blocks scattered through the hook bodies catch
    *expected* failures (offer-rendering, drain breadcrumbs, curator
    spawns). The shield is the backstop for *unexpected* ones —
    NameError, FileNotFoundError on a bundled resource, etc. — that
    would otherwise leak a Python traceback into Claude Code's UI.

    `typer_event` is the Claude Code event name (SessionStart,
    PreCompact, Stop, UserPromptSubmit) so the banner names what
    failed precisely.
    """
    from functools import wraps

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (typer.Exit, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 - hook crash shield
                # Best-effort: emit via the same JSON envelope used on
                # the happy path. If even that fails, fall through to
                # plain stderr — never re-raise (the whole point of
                # the shield is "no traceback in the user's session").
                plain = bool(kwargs.get("plain", False))
                # Persist traceback to disk so doctor + the user can
                # find it later. write_crash returns None silently if
                # the cache dir isn't writable — don't let that mask
                # the original failure.
                try:
                    from lore_cli._crash_log import write_crash
                    log_path = write_crash(typer_event, exc)
                except Exception:  # noqa: BLE001
                    log_path = None
                banner = _hook_failure_banner(typer_event, exc, log_path=log_path)
                try:
                    _emit(typer_event, banner, plain=plain)
                except Exception:  # noqa: BLE001
                    sys.stderr.write(
                        f"lore {typer_event} hook crashed and the diagnostic "
                        f"emitter also failed: {type(exc).__name__}: {exc}\n"
                    )
                # Exit 0 so Claude Code doesn't show 'non-blocking status code'.
                return None
        return wrapped
    return decorator


import typer  # noqa: E402
from lore_adapters import get_adapter  # noqa: E402
from lore_core.scope_resolver import resolve_scope  # noqa: E402
from lore_core.spawn_gate import check_spawn  # noqa: E402
from lore_core.spine import ErrorCode, emit_hook_event  # noqa: E402

from lore_cli._argv_compat import argv_main  # noqa: E402

# Re-export the spawn machinery so hooks-internal heartbeat code and any
# external test that patches ``lore_cli.hooks.<name>`` keep working without
# tracking the move to ``lore_cli.spawn``.
from lore_cli.spawn import (  # noqa: E402, F401
    _open_proc_log,
    _prior_spawn_runaway,
    _process_is_ours,
    _rotate_meta_sidecar,
    _spawn_detached,
    _spawn_detached_transcript_sync,
    _stamp_within_cooldown,
    _write_stamp,
)

hook_app = typer.Typer(
    add_completion=False,
    help="Internal hook dispatcher — invoked by Claude Code at SessionStart, PreCompact, etc.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _ppid_cmd() -> str | None:
    """Return /proc/<ppid>/cmdline as a space-joined string, or None.

    Best-effort hook-event provenance (Linux only); any error yields None.
    """
    try:
        ppid = os.getppid()
        data = Path(f"/proc/{ppid}/cmdline").read_bytes()
        if not data:
            return None
        return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return None


def _resolve_cwd(explicit: str | None) -> str:
    """Resolve CWD: explicit --cwd → $CLAUDE_PROJECT_DIR → $CURSOR_PROJECT_DIR → os.getcwd().

    Cursor exposes ``CLAUDE_PROJECT_DIR`` as an alias automatically (per
    cursor.com/docs/hooks), so the explicit ``CURSOR_PROJECT_DIR`` lookup
    is a defensive fallback for older Cursor versions or partial-compat
    environments where the alias hasn't been set up.
    """
    return (
        explicit
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("CURSOR_PROJECT_DIR")
        or os.getcwd()
    )


def _in_curator_mode() -> bool:
    return os.environ.get("LORE_CURATOR_MODE") == "1"


def _capture_suppressed() -> bool:
    """True when the dispatching orchestrator opted this session out of
    standalone capture (e.g. a teammate whose work is meant to land in a
    shared epic note instead of its own fragment)."""
    return os.environ.get("LORE_SUPPRESS_CAPTURE") == "1"


def _session_off_all() -> bool:
    """True iff `/lore:off` (scope=all) is active for the current session.

    Resolves the session id from CLAUDE_SESSION_ID, which `_read_hook_payload`
    publishes from the Claude Code stdin payload (issue #29 / v0.13.1).
    Returns False when no sid is available — without a sid we can't scope a
    sentinel, so the safe default is "not muted."
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        return False
    from lore_core.toggles import is_off
    return is_off("all", sid)


def _journal_directive_lines() -> list[str]:
    """Invitation directive for the AI journal when the feature flag is on.

    Returns the lines appended to the SessionStart additionalContext
    directive cluster when ``journal.enabled`` is set in the root
    config. Trigger-moment framing rather than passive permission:
    names concrete moments where a gut-check ("anything to save?")
    is worth pausing for, and points the lens both inward (about
    self) and outward (about the user, the work, the conversation).
    Off by default; enable with ``lore journal enable``.
    """
    try:
        from lore_core import journal
        if not journal.enabled():
            return []
    except Exception:  # noqa: BLE001 — never break SessionStart
        return []
    return [
        "- **AI Journal active.** Pause and ask *anything to save?* "
        "when you spot a smell/pattern bigger than the task, get "
        "corrected substantively, notice an unpredictable user pivot, "
        "or hit an unusually sharp/leaky framing. Append via "
        "`lore_journal_write` (kind=`ai`). Reader is future-you — "
        "candid, not sycophantic.",
        "",
    ]


def _citation_directive_lines() -> list[str]:
    """Suppression directive for `/lore:off citations`.

    Returns the lines appended to the SessionStart additionalContext
    directive cluster when the citations sentinel is set for this
    session, else an empty list. The line tells the agent to skip the
    inline `› consulted [[X]]` affordance for the rest of the session.
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        return []
    from lore_core.toggles import is_off
    if not is_off("citations", sid):
        return []
    return [
        "- Inline citations are silenced this session — do not emit "
        "`› consulted [[X]]` lines after consulting the vault.",
        "",
    ]


def _read_hook_payload() -> dict:
    """Consume the JSON payload Claude Code passes on stdin for every hook fire.

    The payload carries the canonical ``session_id`` (and ``cwd``,
    ``transcript_path``, ``hook_event_name``). Until v0.12.1 lore never
    read it, so :func:`lore_core.drain.resolve_session_id` had to fall
    back to a transcript-freshness heuristic — fine for a single
    session, but with multiple concurrent Claude sessions the freshest
    transcript at curator-write time and at heartbeat-read time can
    disagree, leaving curator-filed notes stranded in the wrong drain
    file (issue #29).

    Side effect: when the payload provides ``session_id`` we publish it
    as ``CLAUDE_SESSION_ID`` so :func:`resolve_session_id`'s priority-2
    branch (and any spawned curator subprocess inheriting the env)
    pick it up without further plumbing. We never overwrite an
    explicit ``CLAUDE_SESSION_ID`` already in the env — a host that
    sets it directly stays authoritative.

    No-ops on a TTY (manual ``lore hook ... --plain`` runs) and on any
    parse error; hooks must never abort because of payload trouble.
    """
    try:
        if sys.stdin.isatty():
            return {}
    except (OSError, ValueError):
        return {}
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    sid = payload.get("session_id")
    if (
        isinstance(sid, str)
        and sid
        and not os.environ.get("CLAUDE_SESSION_ID")
    ):
        os.environ["CLAUDE_SESSION_ID"] = sid
    return payload


@hook_app.command("session-start")
@_shield_hook("SessionStart")
def cmd_session_start(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
    probe: bool = typer.Option(
        False,
        "--probe",
        hidden=True,
        help="Suppress all side-effects; used by lore doctor.",
    ),
) -> None:
    """Inject vault context at session start."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    cwd_resolved = Path(_resolve_cwd(cwd))
    out = _session_start(str(cwd_resolved))

    # Refresh the local, gitignored CODEMAP.md for this repo. Deterministic,
    # no LLM, no network; the fingerprint no-op fast path makes an unchanged
    # tree cheap, so it is safe to run inline. Never allowed to crash
    # SessionStart.
    # ponytail: inline generate; if a very large repo's first-run parse adds
    # perceptible startup latency, move this to a detached spawn like the
    # transcript mirror below.
    if not probe:
        try:
            from lore_core import codemap as _codemap

            _codemap.generate(cwd_resolved, quiet=True)
        except Exception:  # noqa: BLE001 - codemap must never crash SessionStart
            pass

    # Surface pending `.lore.yml` offers at the top of the banner.
    # Defensive: offer rendering reads multiple files and classifies state;
    # we explicitly never let any failure here crash the SessionStart hook.
    try:
        notice = _offer_notice_line(cwd_resolved)
        if notice:
            out = notice + "\n\n" + out
    except Exception:  # noqa: BLE001 - hook must never crash SessionStart
        pass

    # Resolve scope once — reused by the banner, curator spawns, and
    # transcript sync below. None means "unattached cwd."
    scope = resolve_scope(cwd_resolved)
    lore_root = _infer_lore_root(scope.claude_md_path) if scope is not None else None

    if scope is None and not probe:
        _nudge_unattached(cwd_resolved, out)

    # Cross-host auto-pull (Phase 10 / 0.11.0).
    # Fast-forward this scope's wiki repo from origin if the wiki opted in
    # via .lore-wiki.yml's git.auto_pull (default true). Strictly read-only
    # on dirty/diverged trees — never disrupts the user's in-flight work.
    # Warning rendered into the banner footer so the user sees diverged
    # state surfaced; otherwise silent.
    auto_pull_warning: str | None = None
    if scope is not None and lore_root is not None and not probe:
        try:
            auto_pull_warning = _maybe_auto_pull_for_scope(scope, lore_root)
        except Exception:  # noqa: BLE001 — pull must never crash SessionStart
            auto_pull_warning = None

    # Opportunistic retention sweep (#190) — flock-guarded, daemon-free; a
    # contended lock just skips this cycle, the next SessionStart retries.
    if scope is not None and lore_root is not None and not probe:
        try:
            from lore_cli._janitor_entry import run_opportunistic_janitor

            run_opportunistic_janitor(lore_root)
        except Exception:  # noqa: BLE001 - janitor must never crash SessionStart
            pass

    # Capture-state breadcrumb + drain + cross-scope lines.
    if scope is not None and lore_root is not None:
        try:
            out = out + _render_capture_state_block(
                lore_root, scope, cwd_resolved, probe=probe,
            )
        except Exception:  # noqa: BLE001 - banner is presentation; never block SessionStart on it
            pass

    if auto_pull_warning is not None:
        out = out + "\n" + auto_pull_warning

    # Side-effect spawns — suppressed under --probe.
    if not probe and scope is not None and lore_root is not None:
        # Fire-and-forget transcript mirror (P4a). Idempotent, gitignored
        # destination, own spawn lock.
        try:
            _spawn_detached_transcript_sync(lore_root)
        except Exception:
            pass

    # Phase 6: project orientation auto-injection. When SessionStart
    # fires inside an attached scope and a project orientation note
    # exists at ``projects/<slug>/<slug>.md`` (folder layout, post-
    # migration) OR legacy flat ``projects/<slug>.md``, append its
    # body (frontmatter stripped, capped at ORIENTATION_BUDGET_CHARS)
    # to the LLM context block. Concepts/decisions/threads/plans/
    # sessions stay pull-on-demand — only the short orientation auto-
    # loads. User-facing status line is unchanged.
    if scope is not None and not probe:
        try:
            orientation_block = _render_project_orientation(
                scope, get_wiki_root(),
            )
            if orientation_block:
                out = out + "\n\n" + orientation_block
        except Exception:  # noqa: BLE001 - orientation must never crash SessionStart
            pass

    _emit("SessionStart", out, plain=plain)


@hook_app.command("pre-compact")
@_shield_hook("PreCompact")
def cmd_pre_compact(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Inject open items before compaction."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    out = _pre_compact(_resolve_cwd(cwd))
    _emit("PreCompact", out, plain=plain)


@hook_app.command("stop")
@_shield_hook("Stop")
def cmd_stop(
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Hint to capture a session note."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    out = _stop()
    _emit("Stop", out, plain=plain)


@hook_app.command("spawn-model-gate")
@_shield_hook("PreToolUse")
def cmd_spawn_model_gate() -> None:
    """PreToolUse gate: deny Task/Agent spawns that omit an explicit model.

    Reads the PreToolUse payload from stdin. Denial protocol (Claude
    Code): exit code 2 blocks the tool call and routes stderr back to
    the model as feedback; exit 0 allows it. See
    :mod:`lore_core.spawn_gate` for the deny/allow rule itself — this
    command is just the stdin/stdout/exit-code plumbing, matching the
    other `lore hook ...` entry points.
    """
    payload = _read_hook_payload()
    deny_message = check_spawn(payload)
    if deny_message is None:
        return
    sys.stderr.write(deny_message)
    raise typer.Exit(code=2)


@hook_app.command("context-log")
def cmd_context_log() -> None:
    """Print the context log — what Lore injected this session."""
    sys.stdout.write(_context_log())


# ---------------------------------------------------------------------------
# UserPromptSubmit
# ---------------------------------------------------------------------------


@hook_app.command("user-prompt-submit")
@_shield_hook("UserPromptSubmit")
def cmd_user_prompt_submit(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Mid-session transcript registration plus the citations directive."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    cwd_resolved = Path(_resolve_cwd(cwd))
    scope = resolve_scope(cwd_resolved)
    if scope is None:
        return
    lore_root = _infer_lore_root(scope.claude_md_path)

    # Mid-session transcript discovery + mtime refresh. Closes the
    # SessionStart-vs-transcript-creation race (sub-second; SessionStart
    # can sample the projects dir before Claude Code has created the
    # transcript file) and keeps `last_mtime` fresh across the session.
    # Without this, long sessions sit on accumulated turns until SessionEnd.
    try:
        adapter = get_adapter("claude-code")
        _register_pending_transcripts(lore_root, cwd_resolved, adapter=adapter)
    except Exception:
        pass  # never break the prompt path on a registration hiccup

    # Citations toggle takes effect mid-session: re-assert the suppression
    # directive on every prompt while `/lore:off citations` is active so the
    # agent sees it on the very next turn after the user toggles it,
    # rather than waiting for the next SessionStart.
    cite_lines = _citation_directive_lines()
    if not cite_lines:
        return
    ctx = "\n".join(line for line in cite_lines if line)
    if not ctx:
        return

    if plain:
        sys.stdout.write(ctx + "\n")
        return

    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": ctx,
                }
            }
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# Capture hook helpers
# ---------------------------------------------------------------------------


def _resolve_cwd_capture() -> Path:
    """Resolve CWD for capture: $CLAUDE_PROJECT_DIR → $CURSOR_PROJECT_DIR → os.getcwd()."""
    env = (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("CURSOR_PROJECT_DIR")
    )
    return Path(env) if env else Path(os.getcwd())


def _nudge_unattached(cwd: Path, out: str) -> None:
    """One-time nudge when session starts in an unattached directory."""
    import hashlib
    nudge_dir = _cache_dir() / "nudged"
    cwd_hash = hashlib.sha256(str(cwd).encode()).hexdigest()[:16]
    marker = nudge_dir / cwd_hash
    if marker.exists():
        return
    try:
        lore_root = get_lore_root()
        from lore_core.state.attachments import AttachmentsFile
        af = AttachmentsFile(lore_root)
        af.load()
        if any(d.path == cwd for d in af._declined):
            return
    except (OSError, json.JSONDecodeError):
        # Couldn't read the declined list — fall through and consider nudging.
        pass
    try:
        nudge_dir.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    print(
        "lore: this directory is not attached — sessions won't be captured. "
        "Run /lore:attach to connect it.",
        file=sys.stderr,
    )


def _infer_lore_root(start: Path) -> Path:
    """Infer LORE_ROOT for a hook-context path.

    Precedence: ``$LORE_ROOT`` env var → walk-up → config-file → default.

    - **env** wins because it's explicit per-invocation; a user who
      exports ``LORE_ROOT=...`` is overriding for a reason.
    - **walk-up** beats config-file: when the hook has a path argument,
      that path is the explicit signal — a user with a global config-file
      pointing at ``~/personal-vault`` but currently editing inside
      ``~/work-vault/wiki/foo/`` should resolve to ``~/work-vault``.
    - **config-file → ~/lore default** via ``get_lore_root()`` when no
      walk-up ancestor contains ``wiki/``.

    Accepts either a file (CLAUDE.md) or a directory (cwd). Returns a
    resolved absolute path.
    """
    env = os.environ.get("LORE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    starting_dir = start.parent if start.is_file() else start
    for parent in [starting_dir, *starting_dir.parents]:
        if (parent / "wiki").is_dir():
            return parent.resolve()
    return get_lore_root()


def _load_wiki_cfg_from_scope(scope, lore_root: Path):
    from lore_core.wiki_config import load_wiki_config
    wiki_dir = lore_root / "wiki" / scope.wiki
    return load_wiki_config(wiki_dir)


# ---------------------------------------------------------------------------
# Capture subcommand
# ---------------------------------------------------------------------------


@hook_app.command("capture")
def capture(
    event: str = typer.Option(
        ...,
        help="session-end | pre-compact | session-start",
    ),
    transcript: Path | None = typer.Option(None, help="Explicit transcript path; else autodetect via adapter."),
    cwd_override: Path | None = typer.Option(None, "--cwd", help="Explicit cwd; else CLAUDE_PROJECT_DIR or os.getcwd()."),
    integration: str = typer.Option("claude-code", help="Adapter integration name."),
) -> None:
    """Hot-path capture hook — called by Claude Code on SessionEnd / PreCompact / SessionStart.

    Must return in <100ms. Updates the sidecar ledger; spawns detached
    curator when pending work exceeds threshold. No LLM, no network,
    bounded FS walk (8 levels).
    """
    if _in_curator_mode() or _capture_suppressed():
        return
    _read_hook_payload()
    if _session_off_all():
        # `/lore:off all` is the security-first contract: no transcript
        # ingestion, no curator spawn, no ledger writes, no vault output
        # while the user has muted us. SessionEnd is the *only* hook bound
        # to the capture pipeline, so if this is bypassed the user can
        # mute SessionStart/PreCompact/Stop/UserPromptSubmit and still see
        # vault content surface from a curator that ran during their
        # "muted" session.
        return
    import time as _time

    from lore_adapters import UnknownIntegrationError

    start = _time.monotonic()
    cwd = cwd_override or _resolve_cwd_capture()
    _capture_pid = os.getpid()
    _capture_ppid_cmd = _ppid_cmd()

    # Never capture transcripts from the vault root — curator subprocesses
    # run with cwd=LORE_ROOT and their claude -p transcripts must not be
    # re-ingested as user sessions. Only skip when the cwd is the vault root
    # AND has no explicit scope attachment (a real project attached at the
    # vault root would still be captured).
    try:
        _vault = get_lore_root().resolve()
        if Path(cwd).resolve() == _vault and resolve_scope(cwd) is None:
            return
    except OSError:
        # Path resolution failed (broken symlink, permission, etc.); fall
        # through so the calling capture path still runs.
        pass

    scope = resolve_scope(cwd)
    if scope is None:
        # Unattached cwd — no ledger work to do, but we still emit a hook
        # event so "hook fired but declined" is distinguishable from "hook
        # never fired" in `lore status`.
        try:
            emit_hook_event(
                get_lore_root(),
                event=event, integration=integration, scope=None,
                duration_ms=int((_time.monotonic() - start) * 1000),
                outcome="no-scope",
                cwd=str(cwd),
                pid=_capture_pid,
                ppid_cmd=_capture_ppid_cmd,
            )
        except Exception:
            pass
        return

    lore_root = _infer_lore_root(scope.claude_md_path)

    def _emit_hook(**kw: object) -> None:
        emit_hook_event(lore_root, **kw)

    def _elapsed_ms() -> int:
        return int((_time.monotonic() - start) * 1000)

    scope_payload = {"wiki": scope.wiki, "scope": scope.scope}
    # Filled in by route_capture as soon as it knows them, so the error
    # branch below can still report counts computed before a failure.
    progress: dict[str, object] = {"registered": 0}

    try:
        try:
            adapter = get_adapter(integration)
        except UnknownIntegrationError:
            _emit_hook(
                event=event, integration=integration, scope=scope_payload,
                duration_ms=_elapsed_ms(),
                outcome="error",
                registered=0,
                error_code=ErrorCode.UNKNOWN_INTEGRATION,
                error={"type": "UnknownIntegrationError", "message": integration},
                cwd=str(cwd),
                pid=_capture_pid,
                ppid_cmd=_capture_ppid_cmd,
            )
            raise typer.Exit(code=1)

        routed = _route_capture(
            lore_root, cwd, scope,
            event=event,
            adapter=adapter,
            transcript=transcript,
            progress=progress,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        _emit_hook(
            event=event, integration=integration, scope=scope_payload,
            duration_ms=_elapsed_ms(),
            outcome="error",
            error_code=ErrorCode.CAPTURE_FAILED,
            registered=progress["registered"],
            error={"type": type(exc).__name__, "message": str(exc)},
            cwd=str(cwd),
            pid=_capture_pid,
            ppid_cmd=_capture_ppid_cmd,
        )
        raise

    # Session boundary: hand the wiki's commits to the remote. Every flag
    # filed this session is already committed, so this is the step that
    # puts them on a teammate's machine. It costs one network round trip
    # at the end of a session, which is why it runs here and not per turn.
    boundary: dict[str, object] = {}
    if event == "session-end":
        try:
            pushed = _maybe_auto_push_for_scope(scope, lore_root)
            if pushed is not None:
                boundary["push"] = pushed.status.value
        except Exception:  # noqa: BLE001 — an unreachable remote never fails the hook
            boundary["push"] = "error"

    _emit_hook(
        event=event, integration=integration, scope=scope_payload,
        duration_ms=_elapsed_ms(),
        outcome=routed.outcome,
        registered=routed.registered,
        run_id=None,
        cwd=str(cwd),
        pid=_capture_pid,
        ppid_cmd=_capture_ppid_cmd,
        **boundary,
    )

    # Session-end breadcrumb, displayed at the next SessionStart. Only for
    # session-end / pre-compact; session-start is already visible.
    if event in ("session-end", "pre-compact"):
        try:
            from lore_core.breadcrumb import (
                render_session_end_breadcrumb,
                write_pending_breadcrumb,
            )

            crumb = render_session_end_breadcrumb(outcome=routed.outcome)
            if crumb is not None:
                write_pending_breadcrumb(lore_root, crumb)
        except Exception:
            pass  # breadcrumb is best-effort, never fatal


main = argv_main(hook_app)


if __name__ == "__main__":
    sys.exit(main())
