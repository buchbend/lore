"""Search sources: one list per source, never merged, nothing cached.

PRD 0017, ADR 0013. A source is a named entry under ``sources:`` in the
person's config with a ``kind``: ``github``, ``dokuwiki`` or ``command``.
Each answers ``search(query, k)`` with hits ``{ref, title, url, snippet}``;
``dokuwiki`` and ``command`` also answer ``read(ref)``. Refs are
``<source>:<id>``. ``github`` is defined implicitly and keeps its
``owner/repo#n`` refs, read live through ``gh``.

Every source runs in its own thread with its own timeout. One that fails or
times out yields ``hits: []`` and a ``note``; the others still return.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 5.0
GITHUB_TIMEOUT = 10.0


class SourceError(Exception):
    """A source could not answer; the message becomes the list's ``note``."""


def _timeout(cfg: dict) -> float:
    default = GITHUB_TIMEOUT if cfg.get("kind") == "github" else DEFAULT_TIMEOUT
    try:
        return float(cfg.get("timeout", default))
    except (TypeError, ValueError):
        return default


# -- github ----------------------------------------------------------------


def _github_search(name: str, cfg: dict, query: str, k: int) -> list[dict]:
    from lore_core.gh import gh_search_issues

    repos = list(cfg.get("repos") or [])
    if not repos:
        raise SourceError("no repo attached and the wiki has no git remote")
    hits = gh_search_issues(query, repos, limit=k)
    if hits is None:
        raise SourceError(
            f"`gh search issues` failed or the machine is offline ({', '.join(repos)})"
        )
    out = []
    for h in hits:
        url = h.get("url", "")
        parts = url.split("github.com/", 1)[-1].split("/")
        ref = (
            f"{parts[0]}/{parts[1]}#{h.get('number')}" if len(parts) >= 2 else f"#{h.get('number')}"
        )
        out.append(
            {
                "ref": ref,
                "title": h.get("title", ""),
                "state": h.get("state", ""),
                "url": url,
                "updated_at": h.get("updatedAt", ""),
            }
        )
    return out


# -- dokuwiki --------------------------------------------------------------


def _doku_call(cfg: dict, method: str, params: dict, timeout: float) -> Any:
    from lore_core.secrets import read_secrets

    base = str(cfg.get("url", "")).rstrip("/")
    if not base:
        raise SourceError("no url configured")
    headers = {"Content-Type": "application/json"}
    secret = cfg.get("secret")
    if secret:
        token = read_secrets().get(secret)
        if not token:
            raise SourceError(f"secret {secret!r} is not set")
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        f"{base}/lib/exe/jsonrpc.php", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read())
    except (OSError, ValueError) as e:
        raise SourceError(f"request failed: {e}") from None
    if not isinstance(reply, dict) or reply.get("error"):
        raise SourceError(
            f"JSON-RPC error: {(reply or {}).get('error') if isinstance(reply, dict) else reply}"
        )
    return reply.get("result")


def _doku_search(name: str, cfg: dict, query: str, k: int) -> list[dict]:
    rows = _doku_call(cfg, "core.searchPages", {"query": query}, _timeout(cfg))
    base = str(cfg.get("url", "")).rstrip("/")
    return [
        {
            "ref": f"{name}:{r['id']}",
            "title": r.get("title") or r["id"],
            "url": f"{base}/doku.php?id={urllib.parse.quote(r['id'], safe=':')}",
            "snippet": r.get("snippet", ""),
        }
        for r in (rows or [])[:k]
        if isinstance(r, dict) and r.get("id")
    ]


def _doku_read(name: str, cfg: dict, ident: str) -> str:
    text = _doku_call(cfg, "core.getPage", {"page": ident}, _timeout(cfg))
    return text if isinstance(text, str) else ""


# -- command ---------------------------------------------------------------


def _run(cfg: dict, payload: dict) -> str:
    run = cfg.get("run")
    if not run:
        raise SourceError("no `run` command configured")
    timeout = _timeout(cfg)
    from lore_core.secrets import SecretsError, secret_env

    try:
        env = secret_env(cfg.get("secret"))
    except SecretsError as e:
        raise SourceError(str(e)) from None
    try:
        proc = subprocess.run(
            run,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise SourceError(f"timed out after {timeout:g}s") from None
    except OSError as e:
        raise SourceError(f"could not run: {e}") from None
    if proc.returncode != 0:
        raise SourceError(f"exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _command_search(name: str, cfg: dict, query: str, k: int) -> list[dict]:
    try:
        data = json.loads(_run(cfg, {"op": "search", "query": query, "k": k}))
    except ValueError:
        raise SourceError("stdout is not JSON") from None
    if isinstance(data, dict):
        data = data.get("hits")
    if not isinstance(data, list):
        raise SourceError("stdout holds no list of hits")
    return [h for h in data if isinstance(h, dict)][:k]


def _command_read(name: str, cfg: dict, ident: str) -> str:
    return _run(cfg, {"op": "read", "ref": f"{name}:{ident}"})


_SEARCH = {"github": _github_search, "dokuwiki": _doku_search, "command": _command_search}
_READ = {"dokuwiki": _doku_read, "command": _command_read}


# -- fan-out ---------------------------------------------------------------


def defined_sources(github_repos: list[str]) -> dict[str, dict]:
    """The person's ``sources:`` plus the implicit ``github`` source."""
    from lore_core.root_config import load_root_config

    configured = load_root_config(Path(".")).sources
    defined: dict[str, dict] = {"github": {"kind": "github", "repos": github_repos}}
    for name, cfg in configured.items():
        if isinstance(cfg, dict):
            defined[name] = (
                {**cfg, "repos": cfg.get("repos") or github_repos}
                if (cfg.get("kind") == "github")
                else cfg
            )
    return defined


def _selected(cwd: Path, defined: dict[str, dict]) -> list[str]:
    """The route's sources; every defined source while the route names none."""
    from lore_core.route import resolve_route

    route = resolve_route(cwd)
    if not route.sources:
        return list(defined)
    # PRD 0017: the tracker's source plus the named sources.
    names = ["github"] if route.tracker in ("auto", "github") else []
    names += route.sources
    return [n for n in dict.fromkeys(names) if n in defined]


def search_sources(
    query: str, k: int, cwd: Path, github_repos: list[str], only: str | None = None
) -> list[dict]:
    """``[{name, kind, hits, note}]``, one entry per selected source, in order.

    ``only`` queries that one defined source, route or not (setup test query).
    """
    defined = defined_sources(github_repos)
    names = [only] if only in defined else _selected(cwd, defined)
    start = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=max(1, len(names)))
    jobs = {}
    for n in names:
        fn = _SEARCH.get(defined[n].get("kind"))
        jobs[n] = pool.submit(fn, n, defined[n], query, k) if fn else None
    out = []
    for n in names:
        cfg, job = defined[n], jobs[n]
        entry: dict[str, Any] = {"name": n, "kind": cfg.get("kind"), "hits": [], "note": None}
        if job is None:
            entry["note"] = f"unknown kind {cfg.get('kind')!r}"
        else:
            try:
                entry["hits"] = job.result(
                    timeout=max(0.0, start + _timeout(cfg) - time.monotonic())
                )
            except FutureTimeout:
                entry["note"] = f"timed out after {_timeout(cfg):g}s"
            except SourceError as e:
                entry["note"] = f"hits omitted: {e}"
            except Exception as e:  # noqa: BLE001 — one source must never sink the others
                entry["note"] = f"hits omitted: {type(e).__name__}: {e}"
        out.append(entry)
    pool.shutdown(wait=False, cancel_futures=True)
    return out


def read_source(ref: str, cwd: Path) -> str | None:
    """Page text for a ``<source>:<id>`` ref of a readable source; None otherwise."""
    name, sep, ident = ref.partition(":")
    if not sep:
        return None
    cfg = defined_sources([]).get(name)
    reader = _READ.get(cfg.get("kind")) if cfg else None
    if reader is None:
        return None
    return reader(name, cfg, ident)  # SourceError reaches the caller
