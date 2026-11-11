# /// script
# dependencies = ["matplotlib"]
# ///
"""Draw `docs/img/architecture.svg`, the figure the root README shows.

Run `python3 tools/fig_architecture.py` (or `uv run tools/fig_architecture.py`) from any
folder. matplotlib is not a dependency of lore; the script header names it for `uv`.

The figure shows Lore as one plugin (ADR 0021): the build skills, the session, the state on
this machine, the repository, the team wiki, the logbook reader and a colleague. The badges
map each part to Anthropic's five context-engineering categories.
The first version came from the ADASS 2026 talk.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

# A fixed salt keeps the element ids stable, so a redraw gives a small diff.
plt.rcParams["svg.hashsalt"] = "lore-architecture"
plt.rcParams["font.sans-serif"] = ["Liberation Sans", "DejaVu Sans"]

ACCENT, ACCENT2 = "#1b3a5c", "#b0413e"
INK, MUTED, FILL = "#222222", "#666666", "#eef2f6"

fig, ax = plt.subplots(figsize=(14, 7.5))
ax.set_xlim(0, 14)
ax.set_ylim(-0.1, 7.4)
ax.set_aspect("equal")
ax.axis("off")


def rbox(x, y, w, h, fc=FILL, ec=ACCENT, lw=1.6, ls="-"):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.15", fc=fc, ec=ec, lw=lw, ls=ls
        )
    )


def arrow(x0, y0, x1, y1, both=False):
    ax.add_patch(
        FancyArrowPatch(
            (x0, y0),
            (x1, y1),
            arrowstyle="<|-|>" if both else "-|>",
            mutation_scale=18,
            lw=1.8,
            color=ACCENT,
        )
    )


def badge(x, y, n, r=0.19, size=11):
    ax.add_patch(Circle((x, y), r, fc=ACCENT2, ec="white", lw=1.5, zorder=5))
    ax.text(
        x,
        y,
        str(n),
        ha="center",
        va="center",
        fontsize=size,
        weight="bold",
        color="white",
        zorder=6,
    )


def note(x, y, text, ha="center", va="center", color=MUTED, size=10.5, weight="normal"):
    ax.text(x, y, text, ha=ha, va=va, fontsize=size, color=color, weight=weight, linespacing=1.2)


def flow(x, y, text, ha="center", va="center"):
    note(x, y, text, ha=ha, va=va, color=ACCENT2, size=11, weight="bold")


def title(x, y, text, size=15):
    ax.text(x, y, text, ha="center", va="center", fontsize=size, weight="bold", color=INK)


def pill(xc, y, w, text, size=11.5):
    rbox(xc - w / 2, y - 0.16, w, 0.32, fc=FILL, ec="none", lw=0)
    ax.text(xc, y, text, ha="center", va="center", fontsize=size, color=INK)


# the lore plugin: the chain of build skills
rbox(0.3, 6.5, 9.2, 0.72, fc="none", ec=MUTED, lw=1.1, ls="--")
note(0.5, 6.86, "lore skills", ha="left", color=ACCENT, size=11.5, weight="bold")
cx, cy = 2.2, 6.86
for i, step in enumerate(["orient", "grilling", "to-epic", "build", "document"]):
    w = 1.25
    rbox(cx, cy - 0.25, w, 0.5, fc="white", ec="#c9d3dd", lw=1.2)
    ax.text(cx + w / 2, cy, step, ha="center", va="center", fontsize=11, color=ACCENT)
    if step == "orient":
        badge(cx + 0.02, cy + 0.27, 2)
    if step == "build":
        badge(cx + w - 0.05, cy + 0.27, 4)
    if i < 4:
        ax.annotate(
            "",
            (cx + w + 0.22, cy),
            (cx + w + 0.02, cy),
            arrowprops={"arrowstyle": "-|>", "color": MUTED, "lw": 1.2},
        )
    cx += w + 0.25
note(9.7, 7.0, "skills, each loaded when its step comes", ha="left", size=10)
note(9.7, 6.7, "+ handover at any stop", ha="left", size=10)

# session, with the state kept on this machine
rbox(0.3, 3.8, 3.0, 2.15)
title(1.8, 5.55, "Session")
note(1.8, 5.1, "one consistent workflow", size=10.5)
note(1.8, 4.82, "starts with ≤ 10,000 characters", size=9.5)
rbox(0.45, 3.97, 2.7, 0.58, fc="white", ec=MUTED, lw=1.1, ls="--")
note(1.8, 4.38, "this machine", color=MUTED, size=9.5, weight="bold")
note(1.8, 4.13, "handover · ledger · transcripts", color=INK, size=9)
badge(0.47, 4.55, 1, r=0.15, size=9)

# the repository
rbox(5.8, 3.8, 3.7, 2.15, fc="white")
title(7.65, 5.6, "The repository")
for i, it in enumerate(
    [
        "issues · comments · PRs",
        "ADRs · PRDs · epic board",
        "CLAUDE.md · CONTEXT.md",
        "docs · log notes",
    ]
):
    pill(7.65, 5.17 - i * 0.37, 3.3, it)
badge(5.8, 5.17 - 2 * 0.37, 1, r=0.15, size=9)
badge(9.5, 5.95, 3)

# project-manage
rbox(11.4, 4.9, 2.3, 1.05, fc="white")
title(12.55, 5.6, "project-manage", size=13)
note(12.55, 5.15, "the team's daily report", size=10)
note(13.0, 4.75, "ours; any tracker\ncan take it", ha="right", size=9, va="top")

# the team wiki
rbox(0.3, 1.3, 3.0, 1.65, fc="white")
title(1.8, 2.65, "Team wiki", size=14)
note(1.8, 2.33, "markdown, its own git repo", size=9.5)
pill(1.8, 1.97, 2.8, "concepts · projects · reference", size=10)
pill(1.8, 1.6, 2.8, "orientation · system maps", size=10)
badge(0.3, 1.6, 1, r=0.15, size=9)
badge(3.3, 2.95, 3)

# the colleague
rbox(5.8, 1.3, 7.9, 1.65)
title(9.75, 2.5, "Colleague", size=14)
pill(9.75, 1.97, 4.6, "own sessions, same workflow", size=11)
pill(9.75, 1.6, 4.6, "reviews, says yes", size=11)
badge(5.8, 2.95, 5)

# session and repository
arrow(3.3, 5.45, 5.8, 5.45)
flow(4.55, 5.55, "files each fact\nwhere it has a reader", va="bottom")
arrow(5.8, 4.3, 3.3, 4.3)
flow(4.64, 4.18, "pulled on demand\nover MCP", va="top")
badge(3.49, 4.04, 2, r=0.15, size=9)

# session and wiki
arrow(1.2, 3.8, 1.2, 2.95)
note(1.05, 3.37, "a note\nby PR", ha="right", size=9.5)
arrow(2.4, 2.95, 2.4, 3.8)
note(2.55, 3.37, "on demand", ha="left", size=9.5)
badge(2.4, 3.37, 2, r=0.15, size=9)

# repository, project-manage and colleague
arrow(9.5, 5.45, 11.4, 5.45)
flow(10.45, 5.35, "log note\nper epic", va="top")
arrow(7.65, 3.8, 7.65, 2.95, both=True)
flow(7.8, 3.37, "same artifacts", ha="left")
arrow(13.2, 4.9, 13.2, 2.95)
flow(13.12, 3.75, "reads", ha="right")
arrow(3.3, 2.1, 5.8, 2.1, both=True)
flow(4.55, 2.2, "same wiki,\nwrites too", va="bottom")

# Anthropic's categories, numbered as the badges above
ax.plot([0.3, 13.7], [1.05, 1.05], color="#c9d3dd", lw=1)
note(
    0.3,
    0.83,
    "Anthropic's context-engineering guidance, by where the tokens live",
    ha="left",
    color=ACCENT,
    size=10.5,
    weight="bold",
)
cats = [
    ("Up front", "small, high-signal start"),
    ("On demand", "load when it is needed"),
    ("Outside the window", "notes, files and git"),
    ("Separate window", "subagents, own context"),
    ("Gates and measurement", "checks by tools, a human yes"),
]
w = 13.4 / len(cats)
for i, (name, sub) in enumerate(cats):
    x = 0.3 + i * w
    badge(x + 0.17, 0.33, i + 1)
    note(x + 0.4, 0.45, name, ha="left", color=INK, size=10, weight="bold")
    note(x + 0.4, 0.15, sub, ha="left", size=9.5)

out = Path(__file__).resolve().parent.parent / "docs" / "img" / "architecture.svg"
fig.savefig(out, bbox_inches="tight", pad_inches=0.15, metadata={"Date": None})
