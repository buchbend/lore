"""Pure renderers for run logs — no I/O.

Callers pass records (from run_reader.read_run) + an IconSet +
a use_color flag; get back a string ready for print.

TTY / NO_COLOR / LORE_ASCII detection lives in `pick_icon_set()` and
`should_use_color()` so tests can inject explicitly.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class IconSet:
    kind: str
    transcript_start: str
    low_signal: str
    kept: str
    skipped: str
    filed: str
    warning: str
    error: str
    unknown: str
    run_end: str

    @classmethod
    def unicode(cls) -> "IconSet":
        return cls("unicode", "▶", "·", "↑", "⊘", "✓", "!", "✗", "?", "═")

    @classmethod
    def ascii(cls) -> "IconSet":
        return cls("ascii", ">", ".", "+", "x", "+", "!", "X", "?", "=")


def pick_icon_set() -> IconSet:
    if os.environ.get("LORE_ASCII") == "1":
        return IconSet.ascii()
    enc = getattr(sys.stdout, "encoding", "") or ""
    if "utf" not in enc.lower():
        return IconSet.ascii()
    return IconSet.unicode()


def should_use_color() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    isatty = getattr(sys.stdout, "isatty", lambda: False)
    return bool(isatty())


def render_flat_log(records: list[dict], *, icons: IconSet, use_color: bool) -> str:
    lines: list[str] = []
    for r in records:
        lines.append(_render_record(r, icons, use_color))
    return "\n".join(lines)


def _short_time(ts: str) -> str:
    if "T" in ts:
        tail = ts.split("T", 1)[1]
        return tail[:8]
    return ts


def _truncate(s: str, maxlen: int) -> str:
    if not s:
        return s
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "…"


def _render_record(r: dict, icons: IconSet, use_color: bool) -> str:
    ts = _short_time(r.get("ts", ""))
    icon, kind_label, message = _icon_and_message(r, icons)
    return f"{ts} {icon} {kind_label:<14} {message}"


def _icon_and_message(r: dict, icons: IconSet) -> tuple[str, str, str]:
    t = r.get("type")
    if t == "run-start":
        return icons.low_signal, "start-run", f"trigger={r.get('trigger', '?')}"
    if t == "transcript-start":
        tid = r.get("transcript_id", "?")
        hb = r.get("hash_before") or "∅"
        turns = r.get("new_turns", 0)
        return icons.transcript_start, "start", f"transcript {tid} (hash {hb}, {turns} new turns)"
    if t == "redaction":
        kinds = ", ".join(r.get("kinds") or [])
        return icons.low_signal, "redacted", f"{r.get('hits', 0)} hits ({kinds})"
    if t == "noteworthy":
        verdict = r.get("verdict")
        icon = icons.kept if verdict else icons.skipped
        reason = _truncate(r.get("reason", ""), 80)
        latency = r.get("latency_ms", 0)
        return icon, "noteworthy", f"{verdict} — {reason!r} ({latency}ms)"
    if t == "merge-check":
        target = r.get("target", "?")
        sim = r.get("similarity", 0)
        decision = r.get("decision", "?")
        return icons.low_signal, "merge-check", f"{target} similarity={sim} → {decision}"
    if t == "session-note":
        action = r.get("action", "?")
        wikilink = r.get("wikilink", "?")
        if action == "filed":
            return icons.filed, "filed", wikilink
        return icons.filed, "merged", f"into {wikilink}"
    if t == "skip":
        return icons.skipped, "skipped", r.get("reason", "?")
    if t == "warning":
        return icons.warning, "warning", r.get("message", "")
    if t == "error":
        return icons.error, "error", f"{r.get('exception', 'Error')}: {r.get('message', '')}"
    if t == "run-end":
        dur = r.get("duration_ms", 0) / 1000.0
        nn = r.get("notes_new", 0); nm = r.get("notes_merged", 0)
        sk = r.get("skipped", 0); er = r.get("errors", 0)
        return icons.run_end, "end", f"{dur:.1f}s · {nn} new, {nm} merged, {sk} skipped · {er} errors"
    if t == "run-truncated":
        return icons.error, "run-truncated", r.get("note", "run interrupted")
    if t == "_malformed":
        return icons.error, "malformed", "<line unparseable>"
    return icons.unknown, "unknown", f"type={t!r}"


def render_summary_panel(records: list[dict], *, term_width: int = 80) -> list[str]:
    """Return the summary-panel content as a list of lines.

    Caller wraps in a Rich panel on TTY, or prints as plain text.
    Wikilinks collapse to basename ellipsis when term_width < 60.
    """
    start = next((r for r in records if r.get("type") == "run-start"), {})
    end = next((r for r in reversed(records) if r.get("type") == "run-end"), {})
    filed = [r for r in records if r.get("type") == "session-note" and r.get("action") == "filed"]
    merged = [r for r in records if r.get("type") == "session-note" and r.get("action") == "merged"]

    def fmt_link(link: str) -> str:
        if term_width >= 60:
            return link
        inner = link.strip("[]")
        if len(inner) > 40:
            return f"[[...{inner[-30:]}]]"
        return link

    lines: list[str] = []
    lines.append(f"Started   {start.get('ts', '?')}")
    dur_ms = end.get("duration_ms", 0)
    lines.append(f"Duration  {dur_ms / 1000:.1f}s")
    lines.append(f"Trigger   {start.get('trigger', '?')}")
    errors = end.get("errors", 0)
    new = end.get("notes_new", 0)
    merged_ct = end.get("notes_merged", 0)
    skipped = end.get("skipped", 0)
    lines.append(
        f"Outcome   {new} new, {merged_ct} merged, {skipped} skipped · {errors} errors"
    )
    links = [fmt_link(r.get("wikilink", "")) for r in filed]
    links += [f"{fmt_link(r.get('wikilink', ''))} (merged)" for r in merged]
    if links:
        lines.append("Notes     " + links[0])
        for l in links[1:]:
            lines.append("          " + l)
    return lines
