"""Browser review surface for pending flags — ``lore flag review``.

The terminal walk shows one flag at a time in one colour. A flag's most
load-bearing signal is its verification stamp (``✓`` / ``(unchecked)`` /
``(not found)``), which decides how much the lead may claim
(``docs/adr/0004``) — and that stamp reads as plain text mid-origin-line.
This renders the same pending list as cards, coloured by that stamp,
grouped by owning note.

The page is the review, not a printout: verdict buttons POST back to this
process, which calls :mod:`lore_core.flag` directly. No second verdict
path exists, so spine events and note writes stay identical to the walk.

The server binds loopback on an ephemeral port and dies when the queue
empties or the user hits Done. A URL token gates every request: a page on
another origin cannot read it, so it cannot forge a verdict against a
port it guessed.
"""

from __future__ import annotations

import html
import json
import re
import secrets
import sys
import threading
import webbrowser
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lore_core import flag

# Stamps carried in the origin line, strongest first. The first one found
# decides the card's colour: a flag is only as good as its weakest ref, and
# ``_weakest`` already encoded that when it rendered the block.
_STAMP_CLASSES = (
    ("(not found)", "missing"),
    ("(unchecked)", "unchecked"),
    ("✓", "verified"),
)

_LEAD_BADGES = (
    (flag.LEAD_MISSING, "ref not found"),
    (flag.LEAD_UNCHECKED, "reported in session"),
)


def _inline(text: str) -> str:
    """Escape, then re-admit the three inline marks a flag block uses."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", r'<span class="wl">\1</span>', out)


def _parts(item: flag.PendingFlag) -> dict:
    """Split one pending flag into the pieces the card renders."""
    lines = item.block.split("\n")
    # Drop by position, never by prefix: ``render_block`` emits the lead
    # first and the origin line last, and a body paragraph may legitimately
    # open in bold or quote an origin-shaped line. Matching on the prefix
    # would withhold that prose from the card the reviewer votes on.
    inner = lines[1:-1]
    origin_at = flag._origin_index(inner)
    body_lines = inner[1:origin_at] if origin_at >= 0 else inner[1:]
    body = "\n".join(body_lines).strip()

    lead = item.lead
    badge = ""
    for prefix, label in _LEAD_BADGES:
        if lead.startswith(prefix):
            lead = lead[len(prefix) :].strip()
            badge = label
            break

    origin = item.origin.strip("_")
    if origin.startswith("flag · "):
        origin = origin[len("flag · ") :]
    meta = [p for p in origin.split(" · ") if p and p != flag.UNREVIEWED_TOKEN]

    status = "unchecked"
    for token, name in _STAMP_CLASSES:
        if token in item.origin:
            status = name
            break

    return {
        "id": item.id,
        "note": Path(item.note_path).stem,
        "lead": lead,
        "badge": badge,
        "body": body,
        "meta": meta,
        "status": status,
    }


def _card(part: dict) -> str:
    badge = ""
    if part["badge"]:
        badge = f'<span class="badge {part["status"]}">{html.escape(part["badge"])}</span>'
    paragraphs = "".join(
        f"<p>{_inline(block)}</p>" for block in re.split(r"\n\s*\n", part["body"]) if block.strip()
    )
    chips = "".join(f'<span class="chip">{_inline(m)}</span>' for m in part["meta"])
    return f"""<article class="card {part["status"]}" data-id="{part["id"]}">
  <header>{badge}<h3>{_inline(part["lead"])}</h3></header>
  <div class="body">{paragraphs}</div>
  <div class="meta">{chips}<span class="chip id">{part["id"]}</span></div>
  <div class="verdict" hidden></div>
  <div class="actions">
    <button class="accept" data-verdict="accept">Accept</button>
    <button class="retarget" data-verdict="retarget">Retarget…</button>
    <button class="decline" data-verdict="decline">Decline</button>
  </div>
</article>"""


def note_slugs(wiki_path: Path) -> list[str]:
    """Wiki-relative paths of every note, for the retarget field's completions.

    Retargeting by slug is otherwise blind typing: ``flag.retarget`` creates
    the note when the name misses, so a typo silently makes a new note
    rather than failing.
    """
    return sorted(str(p.relative_to(wiki_path)) for p in flag._note_files(wiki_path))


def render_page(
    items: list[flag.PendingFlag], *, wiki: str, token: str, slugs: Sequence[str] = ()
) -> str:
    """Whole page for one pending list. Pure — same inputs, same bytes."""
    parts = [_parts(i) for i in items]
    sections = []
    for note in dict.fromkeys(p["note"] for p in parts):
        cards = "".join(_card(p) for p in parts if p["note"] == note)
        sections.append(f"<section><h2>{html.escape(note)}</h2>{cards}</section>")
    empty = '<p class="empty">Nothing pending. Close the tab.</p>' if not parts else ""
    options = "".join(f'<option value="{html.escape(s)}">' for s in slugs)

    return (
        _PAGE.replace("__WIKI__", html.escape(wiki))
        .replace("__COUNT__", str(len(parts)))
        .replace("__TOKEN__", token)
        .replace("__NOTES__", options)
        .replace("__SECTIONS__", "".join(sections) + empty)
    )


# Every verdict rewrites its whole note, and the listener answers requests on
# threads, so two cards settled together would race read-modify-write and lose
# one silently. The terminal walk was serial and needed no such guard.
# ponytail: one global lock — a per-note lock only matters if a review ever
# runs verdicts in parallel on purpose, and a human clicks one card at a time.
_VERDICT_LOCK = threading.Lock()


def apply_verdict(wiki_path: Path, flag_id: str, verdict: str, target: str = "") -> dict:
    """One verdict, through the same functions the terminal walk calls."""
    with _VERDICT_LOCK:
        return _apply(wiki_path, flag_id, verdict, target)


def _apply(wiki_path: Path, flag_id: str, verdict: str, target: str) -> dict:
    if verdict in ("accept", "decline"):
        done = (flag.accept if verdict == "accept" else flag.decline)(wiki_path, flag_id)
        # A flag resolved elsewhere first — a second tab, a parallel `--tty`
        # run — must not report a verdict this call never applied.
        past = "accepted" if verdict == "accept" else "declined"
        return {"ok": done, "text": past if done else "already gone — reload"}
    if verdict == "retarget":
        if not target:
            return {"ok": False, "text": "no target given"}
        try:
            moved = flag.retarget(wiki_path, flag_id, target)
        except ValueError as e:
            return {"ok": False, "text": str(e)}
        # A retarget moves a flag, it does not endorse it (ADR 0008), so the
        # card must stay on the board rather than collapse like a verdict.
        return {
            "ok": bool(moved),
            "pending": True,
            "text": f"moved to {Path(moved).stem} — still pending" if moved else "not moved",
        }
    return {"ok": False, "text": f"unknown verdict {verdict}"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the terminal clean
        pass

    def _authorised(self, supplied: str) -> bool:
        # Compare bytes: compare_digest raises TypeError on a non-ASCII str,
        # which would kill the handler instead of answering 403.
        return secrets.compare_digest(supplied.encode(), self.server.token.encode())

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path != "/":
            self._send(404, b"not found", "text/plain")
            return
        if not self._authorised(parse_qs(url.query).get("t", [""])[0]):
            self._send(403, b"bad token", "text/plain")
            return
        page = render_page(
            flag.pending(self.server.wiki_path),
            wiki=self.server.wiki_path.name,
            token=self.server.token,
            slugs=note_slugs(self.server.wiki_path),
        )
        self._send(200, page.encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        try:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
        except (ValueError, TypeError):
            self._json(400, {"ok": False, "text": "bad request"})
            return
        if not self._authorised(str(payload.get("t", ""))):
            self._json(403, {"ok": False, "text": "bad token"})
            return
        if self.path == "/done":
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path != "/verdict":
            self._json(404, {"ok": False, "text": "not found"})
            return
        result = apply_verdict(
            self.server.wiki_path,
            str(payload.get("id", "")),
            str(payload.get("verdict", "")),
            str(payload.get("target", "")),
        )
        self._json(200, result)


def browser_available() -> bool:
    """Whether this run should open a page rather than prompt.

    Two ways the answer is no, and both end in the same hang if unchecked:
    ``serve`` blocks until a human clicks Done, so a run nobody is watching
    waits forever holding a port.

    * stdout is not a terminal — a piped run, a captured run, a test.
    * ``webbrowser.get()`` raises — no browser on this host, the SSH and
      headless case.

    Probing before binding keeps the fallback to the terminal prompts
    silent, instead of leaving a listener nobody can reach.
    """
    if not sys.stdout.isatty():
        return False
    try:
        webbrowser.get()
    except webbrowser.Error:
        return False
    return True


def build_server(wiki_path: Path) -> ThreadingHTTPServer:
    """Listener for one wiki, bound to loopback on an ephemeral port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.wiki_path = wiki_path
    server.token = secrets.token_urlsafe(16)
    return server


def serve(wiki_path: Path, *, open_browser: bool = True) -> str:
    """Run the review page until the user is done. Returns the URL served."""
    server = build_server(wiki_path)
    url = f"http://127.0.0.1:{server.server_port}/?t={server.token}"
    # Always print it. `browser_available` proves a handler resolves, not that
    # it launches — a stale $BROWSER still returns False here, and without the
    # URL the blocking serve_forever below is indistinguishable from a hang.
    print(f"review at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url


_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>lore flags · __WIKI__</title>
<link rel="icon" href="data:,">

<style>
:root {
  --bg: #f6f6f4; --fg: #16181d; --dim: #5d6470; --line: #dcdcd6; --card: #fff;
  --verified: #1f7a4d; --unchecked: #a2660a; --missing: #b3261e; --accent: #2f5fd0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --fg: #e7e9ee; --dim: #98a0ad; --line: #2b2f37; --card: #1b1e24;
    --verified: #55c48c; --unchecked: #e0a63c; --missing: #ef7a70; --accent: #7aa2f7;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
header.top { position: sticky; top: 0; z-index: 2; display: flex; gap: 1rem;
  align-items: center; flex-wrap: wrap; padding: .75rem 1.25rem;
  background: var(--card); border-bottom: 1px solid var(--line); }
header.top h1 { font-size: 1rem; margin: 0; font-weight: 600; }
header.top .count { color: var(--dim); }
header.top .spacer { flex: 1; }
.legend { display: flex; gap: .75rem; font-size: .78rem; color: var(--dim); }
.legend i { display: inline-block; width: .6rem; height: .6rem; border-radius: 50%;
  margin-right: .3rem; vertical-align: baseline; }
.legend .verified i { background: var(--verified); }
.legend .unchecked i { background: var(--unchecked); }
.legend .missing i { background: var(--missing); }
main { max-width: 62rem; margin: 0 auto; padding: 1.25rem; }
section h2 { font-size: .8rem; text-transform: none; letter-spacing: .02em;
  color: var(--dim); font-weight: 600; margin: 1.75rem 0 .6rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.card { background: var(--card); border: 1px solid var(--line);
  border-left: 4px solid var(--dim); border-radius: 8px;
  padding: .9rem 1.1rem; margin-bottom: .7rem; transition: opacity .25s, transform .25s; }
.card.verified { border-left-color: var(--verified); }
.card.unchecked { border-left-color: var(--unchecked); }
.card.missing { border-left-color: var(--missing); }
.card.gone { opacity: 0; transform: translateX(1.5rem); }
.card h3 { margin: .15rem 0 .4rem; font-size: 1.02rem; font-weight: 600; line-height: 1.4; }
.badge { display: inline-block; font-size: .68rem; letter-spacing: .04em;
  text-transform: uppercase; padding: .12rem .45rem; border-radius: 3px;
  border: 1px solid currentColor; margin-bottom: .35rem; }
.badge.unchecked { color: var(--unchecked); }
.badge.missing { color: var(--missing); }
.badge.verified { color: var(--verified); }
.body p { margin: .35rem 0; color: var(--fg); }
.body code, .meta code { font: .85em ui-monospace, SFMono-Regular, Menlo, monospace;
  background: color-mix(in srgb, var(--dim) 16%, transparent);
  padding: .05em .35em; border-radius: 3px; }
.wl { color: var(--accent); }
.meta { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .6rem; }
.chip { font-size: .74rem; color: var(--dim); border: 1px solid var(--line);
  border-radius: 999px; padding: .1rem .5rem; }
.chip.id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; opacity: .6; }
.actions { display: flex; gap: .4rem; margin-top: .7rem; }
button { font: inherit; font-size: .82rem; padding: .28rem .7rem; cursor: pointer;
  border-radius: 5px; border: 1px solid var(--line); background: transparent; color: var(--fg); }
button:hover { border-color: var(--accent); color: var(--accent); }
button.accept:hover { border-color: var(--verified); color: var(--verified); }
button.decline:hover { border-color: var(--missing); color: var(--missing); }
.verdict { margin-top: .5rem; font-size: .82rem; color: var(--dim); }
.retarget-row { display: flex; gap: .4rem; margin-top: .5rem; }
.retarget-row input { flex: 1; font: inherit; font-size: .82rem; padding: .28rem .5rem;
  border: 1px solid var(--line); border-radius: 5px; background: var(--bg); color: var(--fg); }
.empty { color: var(--dim); padding: 3rem 0; text-align: center; }
</style>
<header class="top">
  <h1>lore flags · __WIKI__</h1>
  <span class="count"><b id="count">__COUNT__</b> pending</span>
  <div class="legend">
    <span class="verified"><i></i>verified</span>
    <span class="unchecked"><i></i>unchecked</span>
    <span class="missing"><i></i>ref not found</span>
  </div>
  <span class="spacer"></span>
  <button onclick="location.reload()">Reload</button>
  <button id="done">Done</button>
</header>
<main>__SECTIONS__</main>
<datalist id="notes">__NOTES__</datalist>
<script>
const T = "__TOKEN__";
const post = (path, data) =>
  fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
               body: JSON.stringify({...data, t: T})}).then(r => r.json());

// A verdict that raises server-side answers 500, so r.json() rejects. Without
// this the click leaves no mark at all and reads as "nothing happened".
const sendVerdict = data =>
  post("/verdict", data).catch(() => ({ok: false, text: "failed — see the terminal"}));

function say(card, text) {
  const el = card.querySelector(".verdict");
  el.textContent = text;
  el.hidden = false;
}

function finish(message) {
  post("/done", {}).then(() => { document.body.innerHTML =
    '<p class="empty">' + message + '</p>'; });
}

function settle(card, res) {
  say(card, res.text);
  if (!res.ok || res.pending) return;
  card.classList.add("gone");
  const n = document.getElementById("count");
  const left = Math.max(0, +n.textContent - 1);
  n.textContent = left;
  setTimeout(() => {
    card.remove();
    // The queue is derived by scanning notes, so an empty board means an
    // empty queue: the listener has nothing left to serve.
    if (left === 0) finish("All flags reviewed. You can close this tab.");
  }, 260);
}

document.addEventListener("click", e => {
  const btn = e.target.closest("button[data-verdict]");
  if (!btn) return;
  const card = btn.closest(".card"), id = card.dataset.id, verdict = btn.dataset.verdict;
  if (verdict === "retarget") {
    if (card.querySelector(".retarget-row")) return;
    const row = document.createElement("div");
    row.className = "retarget-row";
    row.innerHTML = '<input list="notes" placeholder="wiki-relative path or slug">' +
                    '<button class="go">Move</button>';
    card.querySelector(".actions").after(row);
    const send = () => sendVerdict({id, verdict, target: row.querySelector("input").value})
      .then(res => { row.remove(); settle(card, res); });
    row.querySelector(".go").onclick = send;
    row.querySelector("input").onkeydown = ev => { if (ev.key === "Enter") send(); };
    row.querySelector("input").focus();
    return;
  }
  sendVerdict({id, verdict}).then(res => settle(card, res));
});

document.getElementById("done").onclick = () =>
  finish("Review stopped. You can close this tab.");
</script>
"""
