"""The layered layout of the system-map diagram (``lore_core.map_layout``)."""

from __future__ import annotations

import pytest
from lore_core.map_layout import NODE_H, layout, path

# A dense map like a real one: a hub that most repos feed, long edges and a cycle.
EDGES = """data-center ops-db
kosma ops-db
fyst obs
obs fyst
ops-db api
fyst api
obs api
ops-db transfer
ops-db workflow
transfer docs
workflow docs
api docs
ops-db docs
data-center docs
fyst docs
obs docs
api client
api ui
client ui
transfer integration
workflow integration
api integration
ui integration
ops-db integration
docs integration
fyst integration
obs integration
kosma integration
client integration
data-center integration"""
PAIRS = [tuple(f"o/{n}" for n in line.split()) for line in EDGES.splitlines()]
REPOS = sorted({r for pair in PAIRS for r in pair})


def _sample(points: list[tuple[float, float]], steps: int = 24) -> list[tuple[float, float]]:
    """Points along the drawn path: lines as they are, curves as path() bends them."""
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        mid = (y0 + y1) / 2
        for k in range(1, steps + 1):
            t = k / steps
            if abs(x1 - x0) < 0.5:
                out.append((x0, y0 + (y1 - y0) * t))
                continue
            a, b = (1 - t) ** 3, 3 * (1 - t) ** 2 * t
            c, d = 3 * (1 - t) * t**2, t**3
            out.append((a * x0 + b * x0 + c * x1 + d * x1, a * y0 + b * mid + c * mid + d * y1))
    return out


def test_the_layout_depends_only_on_its_arguments() -> None:
    assert layout(REPOS, PAIRS) == layout(REPOS, PAIRS)


def test_each_producer_sits_above_its_consumer_except_one_edge_per_cycle() -> None:
    shape = layout(REPOS, PAIRS)
    upward = [r for r in shape.routes if r.upward]
    assert len(upward) == 1
    assert {PAIRS[upward[0].index]} <= {("o/fyst", "o/obs"), ("o/obs", "o/fyst")}
    for route in shape.routes:
        producer, consumer = PAIRS[route.index]
        above = shape.boxes[producer].y < shape.boxes[consumer].y
        assert above != route.upward


def test_no_edge_runs_through_a_box_it_does_not_join() -> None:
    shape = layout(REPOS, PAIRS)
    for route in shape.routes:
        ends = set(PAIRS[route.index])
        for repo, box in shape.boxes.items():
            if repo in ends:
                continue
            left, right = box.x - box.w / 2, box.x + box.w / 2
            for x, y in _sample(route.points):
                inside = left < x < right and box.y < y < box.y + NODE_H
                assert not inside, f"{PAIRS[route.index]} crosses {repo} at {x:.0f},{y:.0f}"


def test_boxes_of_a_row_keep_their_gap() -> None:
    shape = layout(REPOS, PAIRS)
    rows: dict[float, list] = {}
    for box in shape.boxes.values():
        rows.setdefault(box.y, []).append(box)
    for row in rows.values():
        row.sort(key=lambda b: b.x)
        for a, b in zip(row, row[1:], strict=False):
            assert b.x - b.w / 2 >= a.x + a.w / 2 + 20
    assert all(b.x - b.w / 2 >= 0 and b.x + b.w / 2 <= shape.width for b in shape.boxes.values())


def test_edge_ends_spread_along_a_box() -> None:
    shape = layout(REPOS, PAIRS)
    hub = [r.points[-1] for r in shape.routes if PAIRS[r.index][1] == "o/integration"]
    xs = sorted(x for x, _ in hub)
    assert len(xs) == 11
    assert all(b - a >= 14 for a, b in zip(xs, xs[1:], strict=False))


def test_a_long_edge_runs_through_a_lane_in_each_row_it_spans() -> None:
    shape = layout(REPOS, PAIRS)
    i = PAIRS.index(("o/data-center", "o/integration"))
    route = next(r for r in shape.routes if r.index == i)
    rows = round((shape.boxes["o/integration"].y - shape.boxes["o/data-center"].y) / (NODE_H + 72))
    assert len(route.points) == 2 + 2 * (rows - 1)


@pytest.mark.parametrize(
    ("repos", "pairs"),
    [
        ([], []),
        (["o/a"], []),
        (["o/a"], [("o/a", "o/a")]),  # a self-edge draws nothing
        (["o/a", "o/b"], [("o/a", "o/b"), ("o/a", "o/b")]),  # two interfaces, one pair
    ],
)
def test_small_and_odd_maps_lay_out(repos: list[str], pairs: list[tuple[str, str]]) -> None:
    shape = layout(repos, pairs)
    assert set(shape.boxes) == set(repos)
    assert all(len(r.points) >= 2 for r in shape.routes)


def test_path_joins_points_with_lines_and_curves() -> None:
    assert path([(10, 0), (10, 40)]) == "M10.0,0.0 L10.0,40.0"
    assert path([(0, 0), (20, 40)]) == "M0.0,0.0 C0.0,20.0 20.0,20.0 20.0,40.0"
