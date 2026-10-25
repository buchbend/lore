"""The static token-statistics page that ``lore stats`` writes.

:func:`render` turns a :class:`lore_core.token_usage.Summary` into one
self-contained HTML document: inline CSS, inline SVG, no script and no
network request. The output depends only on its arguments, so the same token
records always give the same bytes. The page carries no "generated at" time;
it names the newest record it sums instead.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from lore_core.token_usage import NO_TARGET, UNTAGGED, Bucket, Summary

_TOP_SESSIONS = 15

_CSS = """
:root {
  --bg: #fbfaf8; --panel: #ffffff; --ink: #1f2328; --muted: #656d76;
  --line: #e4e2dd; --bar: #3b6fb6; --bar-soft: #c9d8ee; --warn: #b4540a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15171a; --panel: #1d2024; --ink: #e6e8eb; --muted: #9aa3ad;
    --line: #2e3238; --bar: #7aa7e6; --bar-soft: #2d4467; --warn: #f0a35e;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 1040px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 36px 0 10px; }
.sub, .note { color: var(--muted); font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-top: 20px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 14px 16px; }
.tile .k { color: var(--muted); font-size: 13px; }
.tile .v { font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile.warn .v { color: var(--warn); }
.scroll { overflow-x: auto; background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { padding: 7px 12px; border-bottom: 1px solid var(--line); text-align: right;
  white-space: nowrap; font-variant-numeric: tabular-nums; }
th { color: var(--muted); font-weight: 500; }
tr:last-child td { border-bottom: none; }
th:first-child, td:first-child { text-align: left; white-space: normal;
  overflow-wrap: anywhere; min-width: 160px; }
td.share { width: 140px; min-width: 100px; }
.bar { height: 8px; border-radius: 4px; background: var(--bar-soft); position: relative; }
.bar span { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--bar); }
svg { display: block; width: 100%; height: auto; }
svg .col { fill: var(--bar); }
svg .axis { fill: var(--muted); font-size: 11px; }
svg .grid { stroke: var(--line); }
code { font-size: 12.5px; }
ul.mismatch li { margin: 4px 0; }
nav.pages { display: flex; gap: 16px; font-size: 14px; margin-bottom: 18px; }
nav.pages a { color: var(--muted); text-decoration: none; }
nav.pages a[aria-current] { color: var(--ink); font-weight: 600; }
"""

#: The status pages ``lore stats`` writes side by side: file name, nav label.
PAGES = (("stats.html", "Token use"), ("systems.html", "System map"))


def nav(current: str) -> str:
    """The links between the status pages; *current* is this page's file name."""
    current_attr = ' aria-current="page"'
    links = "".join(
        f'<a href="{name}"{current_attr if name == current else ""}>{label}</a>'
        for name, label in PAGES
    )
    return f'<nav class="pages">{links}</nav>'


def _n(value: int) -> str:
    return f"{value:,}"


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _compact(value: int) -> str:
    for size, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if value >= size:
            return f"{value / size:.1f}{unit}"
    return str(value)


def _short(name: str) -> str:
    return name.removeprefix("claude-")


def _table(head: str, buckets: dict[str, Bucket], total_cost: float, *, label=_short) -> str:
    """Rows ranked by cost, with a share bar. Ties sort by name, so the order is fixed."""
    ranked = sorted(buckets.items(), key=lambda kv: (-round(kv[1].cost, 6), kv[0]))
    rows = []
    for name, b in ranked:
        c = b.counts
        share = b.cost / total_cost if total_cost else 0.0
        cost = _usd(b.cost) + ("*" if b.unpriced else "")
        rows.append(
            "<tr>"
            f"<td>{escape(label(name))}</td><td>{cost}</td>"
            f'<td class="share"><div class="bar" title="{share:.0%}">'
            f'<span style="width:{share * 100:.1f}%"></span></div></td>'
            f"<td>{_n(b.total)}</td>"
            f"<td>{_n(c['input'])}</td><td>{_n(c['output'])}</td>"
            f"<td>{_n(c['cache_read'])}</td><td>{_n(c['cache_write'])}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        f"<th>{escape(head)}</th><th>est. cost</th><th>share of cost</th><th>tokens</th>"
        "<th>input</th><th>output</th><th>cache read</th><th>cache write</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _days(by_day: dict[str, Bucket]) -> list[tuple[str, float]]:
    """Every day from the first to the last recorded day, zero where none was recorded."""
    known = {}
    for key, bucket in by_day.items():
        try:
            known[date.fromisoformat(key)] = bucket.cost
        except ValueError:
            continue
    if not known:
        return []
    day, last = min(known), max(known)
    out = []
    while day <= last:
        out.append((day.isoformat(), known.get(day, 0.0)))
        day += timedelta(days=1)
    return out


def _day_chart(by_day: dict[str, Bucket]) -> str:
    days = _days(by_day)
    if not days:
        return ""
    width, height, pad_left, pad_bottom = 1000, 220, 56, 24
    plot_w, plot_h = width - pad_left - 8, height - pad_bottom - 12
    top = max(cost for _, cost in days) or 1.0
    step = plot_w / len(days)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Estimated cost per day, {escape(days[0][0])} to {escape(days[-1][0])}">'
    ]
    for frac in (0.0, 0.5, 1.0):
        y = 12 + plot_h * (1 - frac)
        parts.append(
            f'<line class="grid" x1="{pad_left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{pad_left - 6}" y="{y + 4:.1f}" '
            f'text-anchor="end">{_usd(top * frac)}</text>'
        )
    label_every = max(1, len(days) // 8)
    for i, (day, cost) in enumerate(days):
        h = plot_h * cost / top
        x = pad_left + i * step + step * 0.15
        parts.append(
            f'<rect class="col" x="{x:.1f}" y="{12 + plot_h - h:.1f}" '
            f'width="{step * 0.7:.1f}" height="{h:.1f}" rx="2">'
            f"<title>{escape(day)}: {_usd(cost)}</title></rect>"
        )
        if i % label_every == 0 or i == len(days) - 1:
            parts.append(
                f'<text class="axis" x="{x + step * 0.35:.1f}" y="{height - 6}" '
                f'text-anchor="middle">{escape(day[5:])}</text>'
            )
    parts.append("</svg>")
    return '<div class="scroll" style="padding:12px">' + "".join(parts) + "</div>"


def _sessions(summary: Summary) -> str:
    rows = []
    for s in summary.per_session[:_TOP_SESSIONS]:
        repo = s.cwd.rstrip("/").rsplit("/", 1)[-1] if s.cwd else ""
        rows.append(
            "<tr>"
            f"<td><code>{escape(s.session[:8])}</code> {escape(repo)}</td>"
            f"<td>{escape(s.recorded_at[:10])}</td>"
            f"<td>{escape(', '.join(sorted(s.targets)) or '-')}</td>"
            f"<td>{_n(s.bucket.total)}</td><td>{_usd(s.bucket.cost)}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>session</th><th>ended</th>'
        "<th>epic or issue</th><th>tokens</th><th>est. cost</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render(summary: Summary, *, span: str, prices_as_of: str) -> str:
    """The page for ``summary``; ``span`` names the window, such as ``last 30 days``."""
    cost = summary.total.cost
    latest = summary.latest[:10] or "-"
    tiles = [
        ("Estimated cost", _usd(cost), False),
        ("Tokens", _compact(summary.total.total), False),
        ("Sessions", _n(summary.sessions), False),
        ("Model mismatches", _n(len(summary.mismatches)), bool(summary.mismatches)),
    ]
    body = [
        nav(PAGE_NAME),
        "<h1>Token use</h1>",
        f'<p class="sub">{escape(span)} · newest record {escape(latest)} · '
        f"API list prices of {escape(prices_as_of)}</p>",
        '<div class="tiles">'
        + "".join(
            f'<div class="tile{" warn" if warn else ""}"><div class="k">{escape(k)}</div>'
            f'<div class="v">{escape(v)}</div></div>'
            for k, v, warn in tiles
        )
        + "</div>",
    ]
    if not summary.sessions:
        body.append(
            "<h2>No sessions recorded</h2><p>The SessionEnd hook records each session as it "
            "ends. Run <code>lore stats</code> again after a session.</p>"
        )
    else:
        body += ["<h2>Estimated cost per day</h2>", _day_chart(summary.by_day)]
        body += ["<h2>By model</h2>", _table("served model", summary.by_model, cost)]
        body += [
            "<h2>By workflow stage</h2>",
            '<p class="note">A subagent counts under its stage tag. '
            "Main-session work counts under its skill. "
            f"The row &ldquo;{escape(UNTAGGED)}&rdquo; holds spawns without a tag.</p>",
            _table("stage", summary.by_stage, cost, label=str),
        ]
        body += [
            "<h2>By epic or issue</h2>",
            '<p class="note">Summed over every session that worked for it. '
            f"The row &ldquo;{escape(NO_TARGET)}&rdquo; holds work that named no reference.</p>",
            _table("epic or issue", summary.by_target, cost, label=str),
        ]
        if summary.by_request:
            body += [
                "<h2>Subagents by stage and requested model</h2>",
                _table("stage · requested model @ effort", summary.by_request, cost, label=str),
            ]
        if summary.mismatches:
            items = "".join(
                f"<li><code>{escape(m.session[:8])}</code> {escape(m.phase)}: asked "
                f"<code>{escape(m.requested)}</code>, served <code>{escape(m.served)}</code></li>"
                for m in summary.mismatches
            )
            body += [
                "<h2>Subagents on a different model than requested</h2>",
                f'<ul class="mismatch">{items}</ul>',
            ]
        body += [f"<h2>Most expensive sessions (top {_TOP_SESSIONS})</h2>", _sessions(summary)]
    notes = [
        "The cost is an estimate at API list prices, not a bill. On a subscription, read it "
        "as the cost of the same use on the API.",
        "One rate per model. Cache writes cost 1.25x input (5-minute cache) or 2x (1-hour "
        "cache). No long-prompt surcharge, fast mode or batch discount.",
    ]
    if summary.unpriced_models:
        notes.append(
            "* No price for " + ", ".join(sorted(summary.unpriced_models)) + ". Add one under "
            "tokens.prices in ~/.config/lore/config.yml."
        )
    body.append(
        '<h2>Notes</h2><ul class="note">'
        + "".join(f"<li>{escape(n)}</li>" for n in notes)
        + "</ul>"
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Lore token use</title>"
        f"<style>{_CSS}</style></head><body><main>" + "\n".join(body) + "</main></body></html>\n"
    )


PAGE_NAME = "stats.html"


def page_path(lore_root: Path) -> Path:
    return Path(lore_root) / ".lore" / PAGE_NAME


def write_page(lore_root: Path, *, days: int, now: datetime) -> Path:
    """Sum the token records of the last ``days`` days (0: all) and write the page.

    Returns the page path, ``<lore_root>/.lore/stats.html``.
    """
    from lore_core.io import atomic_write_text
    from lore_core.root_config import load_root_config
    from lore_core.token_prices import PRICES_AS_OF
    from lore_core.token_usage import load_records, summarize

    since = now - timedelta(days=days) if days > 0 else None
    prices = load_root_config(lore_root).tokens.prices
    summary = summarize(load_records(lore_root), since=since, prices=prices)
    span = f"last {days} days" if days > 0 else "all recorded sessions"
    path = page_path(lore_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, render(summary, span=span, prices_as_of=PRICES_AS_OF))
    return path
