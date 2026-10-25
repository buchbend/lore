"""The static system-map page that ``lore stats`` writes next to the token page.

:func:`render` turns the system maps of the wiki and the person's scopes into
one self-contained HTML document: inline CSS, inline SVG, no script and no
network request. The page shows each system as a diagram and a table of
edges, each repo with its scope, checkout, branch and description, and each
scope with the checkouts below its folder. The output depends only on its
arguments. :func:`write_page` gathers those arguments from disk and git.
"""

from __future__ import annotations

import contextlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from lore_core.map_layout import NODE_H, layout, path
from lore_core.stats_page import _CSS, nav
from lore_core.system_checkouts import order
from lore_core.system_map import SystemMap, problems

PAGE_NAME = "systems.html"

_ABOUT_CHARS = 280

_EXTRA_CSS = """
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 14px 16px; margin: 10px 0; }
.card h3 { font-size: 15px; margin: 0 0 4px; }
.meta { color: var(--muted); font-size: 13px; }
.meta code { overflow-wrap: anywhere; }
.prose { max-width: 72ch; }
.prose h4 { font-size: 14px; margin: 14px 0 4px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0 16px; }
.grid2 ul { margin: 4px 0; padding-left: 18px; font-size: 13.5px; }
.tag { display: inline-block; font-size: 12px; padding: 0 6px; border-radius: 4px;
  border: 1px solid var(--line); color: var(--muted); margin-right: 4px; }
.tag.warn { color: var(--warn); border-color: var(--warn); }
details { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 10px 16px; margin-top: 20px; }
details summary { cursor: pointer; font-weight: 600; }
dl { margin: 8px 0 0; } dt { font-weight: 600; margin-top: 8px; }
dd { margin: 0 0 0 0; color: var(--muted); }
a { color: var(--bar); }
table.text th, table.text td { text-align: left; white-space: normal; min-width: 110px; }
svg.map { width: auto; max-width: 100%; margin: 0 auto; }
svg.map .node rect { fill: var(--panel); stroke: var(--bar); stroke-width: 1.5; }
svg.map .node.drift rect { stroke: var(--warn); stroke-width: 2.5; }
svg.map .node.off rect { stroke: var(--line); stroke-dasharray: 4 3; }
svg.map .node text { fill: var(--ink); font-size: 13px; }
svg.map .node text.owner { fill: var(--muted); font-size: 10.5px; }
svg.map .edge .line { stroke: var(--muted); stroke-width: 1.3; fill: none; }
svg.map .edge .hit { stroke: transparent; stroke-width: 10; fill: none; }
svg.map .edge:hover .line { stroke: var(--bar); stroke-width: 2.2; }
svg.map .head { fill: var(--muted); fill: context-stroke; }
svg.map:has(.node:hover) .edge, svg.map:has(.node:hover) .node { opacity: 0.18; }
svg.map .edge, svg.map .node { transition: opacity 0.12s; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 10px;
  color: var(--muted); font-size: 12.5px; }
.legend .key { display: inline-flex; align-items: center; gap: 6px; }
.legend svg { width: 34px; height: 8px; display: inline-block; }
.legend line { stroke: var(--muted); stroke-width: 1.5; }
.hint { margin: 6px 0 0; }
"""


@dataclass(frozen=True)
class RepoView:
    """What the page shows about one repo besides its edges."""

    repo: str
    scope: str | None = None
    path: str | None = None
    branch: str | None = None
    work: tuple[str, ...] = ()
    pinned: bool = False
    about: str | None = None


@dataclass(frozen=True)
class TargetView:
    """A system's saved target and which checkouts are off it."""

    branch: str
    set: str = ""
    at_target: int = 0
    checkouts: int = 0
    off: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeView:
    """One scope of the person's config and the checkouts below its folder."""

    name: str
    folder: str
    repos: list[str] = field(default_factory=list)


def _anchor(repo: str) -> str:
    return "repo-" + re.sub(r"[^a-z0-9]+", "-", repo.casefold()).strip("-")


def _inline(text: str) -> str:
    """Escape *text*, then turn `code`, **bold** and [[links]] into HTML."""
    out = escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", out)
    out = re.sub(r"\[\[([^\]]+)\]\]", r"\1", out)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1", out)


def _prose(markdown: str) -> str:
    """A small markdown subset for the map's body: headings, lists and paragraphs."""
    blocks: list[str] = []
    para: list[str] = []
    items: list[str] = []
    in_fence = False
    fence: list[str] = []

    def flush() -> None:
        if para:
            blocks.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                blocks.append("<pre><code>" + escape("\n".join(fence)) + "</code></pre>")
                fence.clear()
            else:
                flush()
            in_fence = not in_fence
            continue
        if in_fence:
            fence.append(line)
        elif not stripped:
            flush()
        elif stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            if level > 1:  # the first-level heading is the map's title
                blocks.append(f"<h4>{_inline(stripped.lstrip('#').strip())}</h4>")
        elif stripped[:2] in ("- ", "* "):
            if para:
                flush()
            items.append(stripped[2:])
        elif items and line.startswith((" ", "\t")):
            items[-1] += " " + stripped
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return "".join(blocks)


def _title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


def _labels(repos: list[str], chars: int) -> dict[str, tuple[str, str]]:
    """Owner and name for each node. A name past *chars* ends in an ellipsis."""
    out = {}
    for repo in repos:
        owner, _, name = repo.rpartition("/")
        if len(name) > chars:
            name = name[: chars - 1] + "…"
        out[repo] = (owner, name)
    return out


#: How each pin draws its edge; the legend under a diagram repeats it.
_PIN_DASH = {"sha": "7 4", "tag": "2 4", "image": "10 3 2 3", "package": "1 5"}


def _diagram(
    system: SystemMap, index: int, checked_out: set[str], off_target: frozenset[str] = frozenset()
) -> str:
    """The system as layered boxes: producers above, an arrow down to each consumer.

    Pins draw as dash patterns. Hovering a box highlights its edges and
    neighbours, through CSS alone.
    """
    repos = order([system])
    if not repos:
        return ""
    key = {r.casefold(): r for r in repos}
    edges = [(key[e.producer.casefold()], key[e.consumer.casefold()]) for e in system.edges]
    shape = layout(repos, edges)
    number = {r: i for i, r in enumerate(repos)}
    neighbours: dict[str, set[int]] = {r: set() for r in repos}
    for producer, consumer in edges:
        neighbours[producer].add(number[consumer])
        neighbours[consumer].add(number[producer])
    scope = f"map-{index}"
    marker = f"head-{index}"
    parts = [
        f'<svg class="map" id="{scope}" viewBox="0 0 {shape.width:.0f} {shape.height:.0f}" '
        f'width="{shape.width:.0f}" role="img" aria-label="{escape(system.name)}: '
        f'{len(repos)} repos, {len(system.edges)} edges">'
        f'<defs><marker id="{marker}" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs>'
    ]
    for route in shape.routes:
        edge = system.edges[route.index]
        producer, consumer = edges[route.index]
        tip = f"{edge.consumer} uses {edge.producer}: {edge.interface}"
        if edge.pin:
            tip += f" (pin: {edge.pin})"
        dash = _PIN_DASH.get(edge.pin or "")
        style = f' stroke-dasharray="{dash}"' if dash else ""
        d = path(route.points)
        parts.append(
            f'<g class="edge e{number[producer]} e{number[consumer]}">'
            f'<path class="line" d="{d}"{style} marker-end="url(#{marker})"/>'
            f'<path class="hit" d="{d}"><title>{escape(tip)}</title></path></g>'
        )
    labels = _labels(repos, int((min(b.w for b in shape.boxes.values()) - 20) / 7.6))
    for repo in repos:
        box = shape.boxes[repo]
        owner, name = labels[repo]
        x = box.x - box.w / 2
        state = "" if repo.casefold() in checked_out else " off"
        tip = repo
        if repo in off_target:
            state, tip = " drift", f"{repo}: not on the target branch"
        near = "".join(f" nb{i}" for i in sorted(neighbours[repo]))
        parts.append(
            f'<a href="#{_anchor(repo)}"><g class="node n{number[repo]}{near}{state}">'
            f"<title>{escape(tip)}</title>"
            f'<rect x="{x:.1f}" y="{box.y:.1f}" width="{box.w:.0f}" height="{NODE_H}" rx="6"/>'
            f'<text class="owner" x="{x + 10:.1f}" y="{box.y + 14:.1f}">{escape(owner)}</text>'
            f'<text x="{x + 10:.1f}" y="{box.y + 31:.1f}">{escape(name)}</text></g></a>'
        )
    parts.append("</svg>")
    hover = "".join(
        f"#{scope}:has(.n{i}:hover) .e{i} {{ opacity: 1; }}"
        f"#{scope}:has(.n{i}:hover) .e{i} .line {{ stroke: var(--bar); stroke-width: 2.2; }}"
        f"#{scope}:has(.n{i}:hover) :is(.n{i}, .nb{i}) {{ opacity: 1; }}"
        for i in range(len(repos))
    )
    pins = sorted({e.pin for e in system.edges if e.pin in _PIN_DASH})
    legend = "".join(
        f'<span class="key"><svg width="34" height="8" aria-hidden="true">'
        f'<line x1="0" y1="4" x2="34" y2="4" stroke-dasharray="{_PIN_DASH[p]}"/></svg>'
        f"pin: {escape(p)}</span>"
        for p in pins
    )
    if legend:
        legend = (
            '<div class="legend"><span class="key"><svg width="34" height="8" '
            'aria-hidden="true"><line x1="0" y1="4" x2="34" y2="4"/></svg>no pin</span>'
            f"{legend}</div>"
        )
    return (
        f"<style>{hover}</style>"
        '<div class="scroll" style="padding:12px">'
        + "".join(parts)
        + legend
        + '<p class="note hint">Hover a repo to follow its edges; click it for its card.</p>'
        "</div>"
    )


def _edge_table(system: SystemMap) -> str:
    rows = "".join(
        "<tr>"
        f'<td><a href="#{_anchor(e.consumer)}">{escape(e.consumer)}</a></td>'
        f'<td><a href="#{_anchor(e.producer)}">{escape(e.producer)}</a></td>'
        f"<td>{escape(e.interface)}</td><td>{escape(e.pin or '-')}</td>"
        f"<td>{escape(e.breaks_when or '-')}</td>"
        "</tr>"
        for e in system.edges
    )
    return (
        '<div class="scroll"><table class="text"><thead><tr><th>consumer</th>'
        "<th>producer</th><th>interface</th><th>pin</th><th>breaks when</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


_EXPLAINER = """
<dl>
<dt>System map</dt>
<dd>A wiki note at <code>&lt;wiki&gt;/systems/&lt;name&gt;.md</code> with
<code>type: system-map</code>. It lists the repos of one system and the edges between them.
Agents read its frontmatter; the prose below each diagram is its body.</dd>
<dt>Edge</dt>
<dd>One dependency: the <strong>consumer</strong> reads something from the
<strong>producer</strong>. The <strong>interface</strong> names what it reads. In the
diagram the arrow runs from the producer down to the consumer.</dd>
<dt>Pin</dt>
<dd>How the consumer fixes the producer's version: <code>sha</code>, <code>tag</code>,
<code>image</code>, <code>package</code> or <code>none</code>. With a pin, a change in the
producer's checkout reaches the consumer only after the pin moves.</dd>
<dt>Breaks when</dt>
<dd>The producer change that breaks the consumer.</dd>
<dt>Neighbours</dt>
<dd>The repos one edge away. SessionStart shows them in each repo of a map.</dd>
<dt>Scope</dt>
<dd>A folder from <code>scopes</code> in <code>~/.config/lore/config.yml</code>. The map
names repos, never folders, since folders differ between people. Lore finds each checkout
below the scope folders by its git remote. A <code>checkouts</code> entry in the same
config pins a repo to a folder the search misses. A dashed box has no local checkout.</dd>
<dt>Target</dt>
<dd>The branch a system belongs on, kept on this machine only. A box with an orange border
is off the target. <code>lore system status</code> shows the same in the terminal.</dd>
</dl>
<p class="note">Change a map with <code>/lore:map</code>, which scans the checkouts and opens
a wiki PR. Print it with <code>lore map</code>. Bring a system to its target branch with
<code>/lore:system</code>.</p>
"""


def _repo_card(repo: str, view: RepoView, maps: list[SystemMap]) -> str:
    key = repo.casefold()
    systems = sorted(
        {
            m.name
            for m in maps
            for e in m.edges
            if key in (e.consumer.casefold(), e.producer.casefold())
        }
        | {m.name for m in maps if key in {r.casefold() for r in m.repos}}
    )
    uses = [e for m in maps for e in m.edges if e.consumer.casefold() == key]
    used_by = [e for m in maps for e in m.edges if e.producer.casefold() == key]
    tags = "".join(f'<span class="tag">{escape(s)}</span>' for s in systems)
    if view.path:
        where = (
            f"scope {escape(view.scope or '-')} · <code>{escape(view.path)}</code>"
            f"{' (pinned in your config)' if view.pinned else ''} · "
            f"branch <code>{escape(view.branch or 'detached')}</code>"
        )
        where += "".join(f' <span class="tag warn">{escape(w)}</span>' for w in view.work)
    else:
        where = (
            '<span class="tag warn">no local checkout</span> in any scope folder; add '
            f"<code>checkouts: {{{escape(repo)}: &lt;folder&gt;}}</code> to your config"
        )

    def item(edge, other: str) -> str:
        text = f'<a href="#{_anchor(other)}">{escape(other)}</a>: {escape(edge.interface)}'
        if edge.pin:
            text += f' <span class="tag">pin: {escape(edge.pin)}</span>'
        return f"<li>{text}</li>"

    lists = []
    if uses:
        lists.append(
            "<div><strong>Uses</strong><ul>"
            + "".join(item(e, e.producer) for e in uses)
            + "</ul></div>"
        )
    if used_by:
        lists.append(
            "<div><strong>Used by</strong><ul>"
            + "".join(item(e, e.consumer) for e in used_by)
            + "</ul></div>"
        )
    about = f"<p>{escape(view.about)}</p>" if view.about else ""
    return (
        f'<div class="card" id="{_anchor(repo)}"><h3>{escape(repo)} {tags}</h3>'
        f'<div class="meta">{where}</div>{about}'
        f'<div class="grid2">{"".join(lists)}</div></div>'
    )


def render(
    maps: list[SystemMap],
    repos: dict[str, RepoView],
    scopes: list[ScopeView],
    bodies: dict[str, str] | None = None,
    targets: dict[str, TargetView] | None = None,
) -> str:
    """The page. *bodies* maps a map's note path to its markdown body; *targets*
    maps a system's name to its saved target."""
    bodies = bodies or {}
    targets = targets or {}
    all_repos = order(maps)
    checked_out = {r.casefold() for r, v in repos.items() if v.path}
    edges = sum(len(m.edges) for m in maps)
    tiles = [
        ("Systems", str(len(maps))),
        ("Repos", str(len(all_repos))),
        ("Edges", str(edges)),
        (
            "Checked out",
            f"{sum(1 for r in all_repos if r.casefold() in checked_out)} / {len(all_repos)}",
        ),
    ]
    body = [
        nav(PAGE_NAME),
        "<h1>System map</h1>",
        f'<p class="sub">{len(maps)} systems across {len(scopes)} scopes · '
        "read from the wiki's <code>systems/</code> notes</p>",
        '<div class="tiles">'
        + "".join(
            f'<div class="tile"><div class="k">{escape(k)}</div>'
            f'<div class="v">{escape(v)}</div></div>'
            for k, v in tiles
        )
        + "</div>",
        f"<details{'' if maps else ' open'}><summary>What is a system map?</summary>"
        f"{_EXPLAINER}</details>",
    ]
    if not maps:
        body.append(
            "<h2>No system map yet</h2><p>Run <code>/lore:map</code> inside a scope folder. "
            "The skill scans the checkouts and proposes the first map in a wiki PR.</p>"
        )
    found = [p for m in maps for p in problems(m)]
    if found:
        body.append(
            '<h2>Format errors</h2><ul class="mismatch">'
            + "".join(f"<li><code>{escape(p)}</code></li>" for p in found)
            + '</ul><p class="note">Run <code>lore map check</code>.</p>'
        )
    for index, system in enumerate(maps):
        text = bodies.get(str(system.path), "")
        title = _title(text)
        heading = escape(system.name) + (
            f' <span class="meta">· {escape(title)}</span>' if title else ""
        )
        body += [
            f'<h2 id="system-{escape(system.name)}">{heading}</h2>',
            f'<p class="meta"><code>{escape(str(system.path))}</code> · '
            f"{len(system.repos)} repos · {len(system.edges)} edges</p>",
        ]
        goal = targets.get(system.name)
        if goal:
            drift = (
                ' <span class="tag warn">off target: ' + escape(", ".join(goal.off)) + "</span>"
                if goal.off
                else ""
            )
            body.append(
                f'<p class="meta">target <code>{escape(goal.branch)}</code>'
                f"{' (set ' + escape(goal.set) + ')' if goal.set else ''} · "
                f"{goal.at_target} of {goal.checkouts} checkouts on target{drift}</p>"
            )
        else:
            body.append(
                '<p class="meta">no target · <code>lore system target &lt;branch&gt; '
                f"{escape(system.name)}</code></p>"
            )
        prose = _prose(text)
        if prose:
            body.append(f'<div class="prose">{prose}</div>')
        off = frozenset(goal.off) if goal else frozenset()
        body.append(_diagram(system, index, checked_out, off))
        if system.edges:
            body.append(_edge_table(system))
    if all_repos:
        body.append("<h2>Repositories</h2>")
        body += [_repo_card(r, repos.get(r) or RepoView(repo=r), maps) for r in all_repos]
    if scopes:
        mapped = {r.casefold() for r in all_repos}
        rows = []
        for s in scopes:
            listed = (
                ", ".join(
                    f'<a href="#{_anchor(r)}">{escape(r)}</a>'
                    if r.casefold() in mapped
                    else f'{escape(r)} <span class="tag">not in a map</span>'
                    for r in s.repos
                )
                or '<span class="meta">no checkout</span>'
            )
            rows.append(
                f"<tr><td>{escape(s.name)}</td><td><code>{escape(s.folder)}</code></td>"
                f"<td>{listed}</td></tr>"
            )
        body += [
            "<h2>Scopes</h2>",
            '<p class="note">The checkouts Lore finds below each scope folder.</p>',
            '<div class="scroll"><table class="text"><thead><tr><th>scope</th>'
            "<th>folder</th><th>repos</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>",
        ]
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Lore system map</title>"
        f"<style>{_CSS}{_EXTRA_CSS}</style></head><body><main>"
        + "\n".join(body)
        + "</main></body></html>\n"
    )


def _home(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def about(path: Path) -> str | None:
    """One line on what the repo is: its package description, else its README's first paragraph."""
    try:
        text = (path / "pyproject.toml").read_text(encoding="utf-8")
        found = tomllib.loads(text).get("project", {}).get("description")
        if isinstance(found, str) and found.strip():
            return found.strip()[:_ABOUT_CHARS]
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    try:
        found = json.loads((path / "package.json").read_text(encoding="utf-8")).get("description")
        if isinstance(found, str) and found.strip():
            return found.strip()[:_ABOUT_CHARS]
    except (OSError, ValueError, AttributeError):
        pass
    for name in ("README.md", "README.rst", "README.txt", "README"):
        try:
            text = (path / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        para: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            skip = line.startswith(("#", "[!", "![", "<", "=", "-", "..", "|", "```"))
            if not line or skip:
                if para:
                    break
                continue
            para.append(line)
        if para:
            joined = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(para))
            return joined[:_ABOUT_CHARS]
    return None


def write_page(lore_root: Path) -> Path:
    """Read the maps, scopes and checkouts and write ``<lore_root>/.lore/systems.html``."""
    from lore_core.config import list_wikis
    from lore_core.io import atomic_write_text
    from lore_core.schema import strip_frontmatter
    from lore_core.system_checkouts import report
    from lore_core.system_map import load_maps
    from lore_core.system_map_scan import configured_scopes, find_checkouts

    maps = load_maps(list_wikis(Path(lore_root)))
    scopes = [
        ScopeView(
            name=name,
            folder=_home(folder),
            repos=sorted(find_checkouts(folder)) if folder.is_dir() else [],
        )
        for name, folder, _tidy in configured_scopes()
    ]
    repos: dict[str, RepoView] = {}
    targets: dict[str, TargetView] = {}
    for m in maps:
        data = report(m, Path(lore_root))
        for c in data["checkouts"]:
            if c["repo"] in repos:
                continue
            path = Path(c["path"])
            repos[c["repo"]] = RepoView(
                repo=c["repo"],
                scope=c["scope"] or None,
                path=_home(path),
                branch=c["branch"],
                work=tuple(c["local_work"]),
                pinned=c["source"] == "pinned",
                about=about(path),
            )
        if data["target"]:
            targets[m.name] = TargetView(
                branch=data["target"]["branch"],
                set=data["target"]["set"],
                at_target=data["at_target"],
                checkouts=len(data["checkouts"]),
                off=tuple(c["repo"] for c in data["checkouts"] if c["at_target"] is False),
            )
    bodies = {}
    for m in maps:
        with contextlib.suppress(OSError):
            bodies[str(m.path)] = strip_frontmatter(m.path.read_text(encoding="utf-8"))
    path = Path(lore_root) / ".lore" / PAGE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, render(maps, repos, scopes, bodies, targets))
    return path
