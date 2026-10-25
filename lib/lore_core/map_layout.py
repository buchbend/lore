"""A layered layout for the system-map diagram: boxes in rows, edges between them.

The diagram puts each producer above its consumers. :func:`layout` follows
the classic layered (Sugiyama) steps, sized for a few dozen repos:

1. Break cycles: one edge of each cycle is drawn upwards.
2. Layer: a repo sits one row below its lowest producer. A repo that only
   produces moves down next to its first consumer, so its edges stay short.
3. Waypoints: an edge that spans rows gets a waypoint in each row between, so
   it runs in a lane between the boxes instead of through them.
4. Order: barycenter sweeps plus adjacent swaps cut edge crossings; the best
   order seen wins.
5. Place: each box moves towards the boxes it connects to, keeping its gap.
6. Ports: the ends of a box's edges spread along its top and bottom.

The result depends only on the arguments, so the same map draws the same
picture.
"""

from __future__ import annotations

from dataclasses import dataclass

NODE_H = 40
ROW_GAP = 72
PAD = 16
GAP = 28  # between two boxes of a row
LANE_GAP = 16  # next to a waypoint
PORT_GAP = 20  # between two edge ends on one side of a box
_SWEEPS = 12

Node = str | tuple[str, int, int]  # a repo, or ("lane", edge index, row)


@dataclass(frozen=True)
class Box:
    repo: str
    x: float  # centre
    y: float  # top
    w: float


@dataclass(frozen=True)
class Route:
    """The path of edge ``index``, from its producer to its consumer."""

    index: int
    points: list[tuple[float, float]]
    upward: bool


@dataclass(frozen=True)
class Layout:
    boxes: dict[str, Box]
    routes: list[Route]
    width: float
    height: float


def box_width(repos: list[str]) -> float:
    """One width for every box, sized to the longest name, within bounds."""
    longest = max((len(r.rpartition("/")[2]) for r in repos), default=10)
    return float(min(max(longest * 7.6 + 24, 150), 250))


def _break_cycles(repos: list[str], pairs: list[tuple[str, str]]) -> set[int]:
    """Indices of the edges to draw upwards so the rest form no cycle."""
    out: dict[str, list[tuple[str, int]]] = {r: [] for r in repos}
    for i, (producer, consumer) in enumerate(pairs):
        out[producer].append((consumer, i))
    state: dict[str, int] = {}
    upward: set[int] = set()
    for start in repos:
        if start in state:
            continue
        stack = [(start, iter(sorted(out[start])))]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            for child, i in children:
                if state.get(child) == 1:
                    upward.add(i)
                elif child not in state:
                    state[child] = 1
                    stack.append((child, iter(sorted(out[child]))))
                    break
            else:
                state[node] = 2
                stack.pop()
    return upward


def _layers(repos: list[str], down: list[tuple[str, str]]) -> dict[str, int]:
    ups: dict[str, list[str]] = {r: [] for r in repos}
    downs: dict[str, list[str]] = {r: [] for r in repos}
    for a, b in down:
        ups[b].append(a)
        downs[a].append(b)
    layer: dict[str, int] = {}

    def depth(repo: str) -> int:
        if repo not in layer:
            layer[repo] = 0  # guards a cycle the breaker missed
            layer[repo] = max((depth(p) + 1 for p in ups[repo]), default=0)
        return layer[repo]

    for repo in repos:
        depth(repo)
    for repo in repos:  # a pure producer sits just above its first consumer
        if not ups[repo] and downs[repo]:
            layer[repo] = min(layer[c] for c in downs[repo]) - 1
    return layer


def _crossings(rows: list[list[Node]], links: list[tuple[Node, Node]], pos) -> int:
    total = 0
    by_row: dict[int, list[tuple[int, int]]] = {}
    row_of = {n: i for i, row in enumerate(rows) for n in row}
    for a, b in links:
        by_row.setdefault(row_of[a], []).append((pos[a], pos[b]))
    for pairs in by_row.values():
        pairs.sort()
        for i, (_, b1) in enumerate(pairs):
            total += sum(1 for _, b2 in pairs[i + 1 :] if b2 < b1)
    return total


def layout(repos: list[str], pairs: list[tuple[str, str]]) -> Layout:
    """Boxes and routes for *repos* and the ``(producer, consumer)`` *pairs*."""
    width = box_width(repos)
    known = set(repos)
    pairs = [(p, c) for p, c in pairs if p in known and c in known]
    upward = _break_cycles(repos, pairs)
    drawn = [i for i, (p, c) in enumerate(pairs) if p != c]
    down = [(c, p) if i in upward else (p, c) for i, (p, c) in enumerate(pairs)]
    layer = _layers(repos, [down[i] for i in drawn])
    depth = max(layer.values(), default=0) + 1

    rows: list[list[Node]] = [[] for _ in range(depth)]
    for repo in repos:
        rows[layer[repo]].append(repo)
    links: list[tuple[Node, Node]] = []
    chains: dict[int, list[Node]] = {}
    for i in drawn:
        top, bottom = down[i]
        chain: list[Node] = [top]
        for row in range(layer[top] + 1, layer[bottom]):
            lane: Node = ("lane", i, row)
            rows[row].append(lane)
            chain.append(lane)
        chain.append(bottom)
        chains[i] = chain
        links += list(zip(chain, chain[1:], strict=False))

    ups: dict[Node, list[Node]] = {n: [] for row in rows for n in row}
    downs: dict[Node, list[Node]] = {n: [] for row in rows for n in row}
    for a, b in links:
        downs[a].append(b)
        ups[b].append(a)

    def positions() -> dict[Node, int]:
        return {n: i for row in rows for i, n in enumerate(row)}

    def sweep(order: range, near: dict[Node, list[Node]]) -> None:
        for r in order:
            pos = positions()
            keys = {}
            for i, n in enumerate(rows[r]):
                others = near[n]
                centre = sum(pos[o] for o in others) / len(others) if others else float(i)
                keys[n] = (centre, i)
            rows[r].sort(key=keys.__getitem__)

    def transpose() -> None:
        improved = True
        while improved:
            improved = False
            for row in rows:
                for i in range(len(row) - 1):
                    before = _crossings(rows, links, positions())
                    row[i], row[i + 1] = row[i + 1], row[i]
                    if _crossings(rows, links, positions()) < before:
                        improved = True
                    else:
                        row[i], row[i + 1] = row[i + 1], row[i]

    best = [list(row) for row in rows]
    best_cost = _crossings(rows, links, positions())
    for n in range(_SWEEPS):
        if n % 2 == 0:
            sweep(range(1, depth), ups)
        else:
            sweep(range(depth - 2, -1, -1), downs)
        cost = _crossings(rows, links, positions())
        if cost < best_cost:
            best, best_cost = [list(row) for row in rows], cost
    rows[:] = best
    transpose()

    # A box with many edge ends on one side grows, so the ends do not crowd.
    sides = {r: max(len(ups[r]), len(downs[r])) for r in repos}
    widths = {r: max(width, (sides[r] + 1) * PORT_GAP / 0.8) for r in repos}

    def w(n: Node) -> float:
        return widths[n] if isinstance(n, str) else 0.0

    def sep(a: Node, b: Node) -> float:
        gap = GAP if isinstance(a, str) and isinstance(b, str) else LANE_GAP
        return (w(a) + w(b)) / 2 + gap

    x: dict[Node, float] = {}
    for row in rows:
        at = 0.0
        for i, n in enumerate(row):
            at = at + sep(row[i - 1], n) if i else 0.0
            x[n] = at
    for n in range(24):
        near = ups if n % 2 == 0 else downs
        for row in rows:
            want = []
            for node in row:
                others = near[node] or ups[node] + downs[node]
                want.append(sum(x[o] for o in others) / len(others) if others else x[node])
            left = list(want)
            for i in range(1, len(row)):
                left[i] = max(want[i], left[i - 1] + sep(row[i - 1], row[i]))
            right = list(want)
            for i in range(len(row) - 2, -1, -1):
                right[i] = min(want[i], right[i + 1] - sep(row[i], row[i + 1]))
            for i, node in enumerate(row):
                x[node] = (left[i] + right[i]) / 2
    shift = PAD - min((x[n] - w(n) / 2 for row in rows for n in row), default=0.0)
    for n in x:
        x[n] += shift
    full = max((x[n] + w(n) / 2 for row in rows for n in row), default=0.0) + PAD

    def top(r: int) -> float:
        return PAD + r * (NODE_H + ROW_GAP)

    boxes = {r: Box(repo=r, x=x[r], y=top(layer[r]), w=widths[r]) for r in repos}

    # Ports: spread each box's edge ends along its bottom (out) and top (in).
    out_port: dict[int, float] = {}
    in_port: dict[int, float] = {}
    for repo in repos:
        outs = sorted((x[chains[i][1]], i) for i in chains if chains[i][0] == repo)
        ins = sorted((x[chains[i][-2]], i) for i in chains if chains[i][-1] == repo)
        for ends, port in ((outs, out_port), (ins, in_port)):
            for k, (_, i) in enumerate(ends):
                span = widths[repo] * 0.8
                port[i] = x[repo] - span / 2 + (k + 1) * span / (len(ends) + 1)

    routes = []
    for i in drawn:
        chain = chains[i]
        first, last = chain[0], chain[-1]
        points = [(out_port[i], top(layer[first]) + NODE_H)]
        for lane in chain[1:-1]:
            row = lane[2]
            points += [(x[lane], top(row)), (x[lane], top(row) + NODE_H)]
        points.append((in_port[i], top(layer[last])))
        if i in upward:
            points.reverse()
        routes.append(Route(index=i, points=points, upward=i in upward))
    return Layout(
        boxes=boxes,
        routes=routes,
        width=full,
        height=PAD * 2 + depth * NODE_H + (depth - 1) * ROW_GAP,
    )


def path(points: list[tuple[float, float]]) -> str:
    """An SVG path through *points*: vertical runs joined by smooth curves."""
    (x0, y0), rest = points[0], points[1:]
    d = [f"M{x0:.1f},{y0:.1f}"]
    for x1, y1 in rest:
        if abs(x1 - x0) < 0.5:
            d.append(f"L{x1:.1f},{y1:.1f}")
        else:
            mid = (y0 + y1) / 2
            d.append(f"C{x0:.1f},{mid:.1f} {x1:.1f},{mid:.1f} {x1:.1f},{y1:.1f}")
        x0, y0 = x1, y1
    return " ".join(d)
