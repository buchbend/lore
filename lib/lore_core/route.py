"""Route: which wiki, scope, tracker, sinks and sources apply to a repo.

ADR 0016. ``resolve_route(cwd)`` reads the person's config
(``~/.config/lore/config.yml``) and the nearest ``.lore.yml``.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from lore_core.config import user_config_path
from lore_core.config_schema import _read_raw

#: Route field -> key in a scope, ``defaults`` or ``.lore.yml``.
_KEYS = {"wiki": "wiki", "tracker": "tracker", "sinks": "logbook", "sources": "sources"}
_CODE_DEFAULTS: dict[str, object] = {
    "wiki": None,
    "tracker": "auto",
    "sinks": ("file",),
    "sources": (),
}
_WIKI_NAME = re.compile(r"[A-Za-z0-9][\w.-]*")
_ENV = {"scope": "LORE_SCOPE", "wiki": "LORE_WIKI", "tracker": "LORE_TRACKER"}


@dataclass(frozen=True)
class Route:
    """What applies at ``cwd``. ``origins`` names where each value came from."""

    cwd: Path
    repo_root: Path | None = None
    wiki: str | None = None
    scope: str | None = None
    tracker: str = "auto"
    sinks: tuple[str, ...] = ("file",)
    sources: tuple[str, ...] = ()
    origins: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def claude_md_path(self) -> Path:
        """Synthetic ``<repo>/CLAUDE.md``. Callers walk up from it to infer
        ``$LORE_ROOT`` (see ``lore_cli.hooks._infer_lore_root``)."""
        return (self.repo_root or self.cwd) / "CLAUDE.md"


def _expand(p: str) -> Path:
    return Path(p).expanduser().resolve()


_REMOTE_RE = re.compile(r"^(?:[\w+]+://)?(?:[^@/]+@)?([^/:]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$")


def _remote_id(cwd: Path) -> str | None:
    """``host/org/name`` of the repo's ``upstream`` or ``origin`` remote."""
    for name in ("upstream", "origin"):
        try:
            out = subprocess.run(
                ["git", "remote", "get-url", name],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = _REMOTE_RE.match(out.stdout.strip()) if out.returncode == 0 else None
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return None


def _as_list(v: object) -> list[str]:
    if isinstance(v, str):
        return [v]
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _match_scope(
    cwd: Path, repo_root: Path | None, scopes: dict
) -> tuple[str | None, str, list[str]]:
    """Longest scope folder containing ``cwd``, else a unique remote match."""
    by_path = [
        (len(_expand(s["path"]).parts), name, s["path"])
        for name, s in scopes.items()
        if isinstance(s.get("path"), str) and cwd.is_relative_to(_expand(s["path"]))
    ]
    if by_path:
        _, name, path = max(by_path)
        return name, f"path {path}", []
    with_remote = {n: _as_list(s.get("remote")) for n, s in scopes.items() if s.get("remote")}
    rid = _remote_id(cwd) if with_remote and repo_root is not None else None
    if rid is None:
        return None, "", []
    # GitHub owners and names are case-insensitive.
    hits = [
        n for n, pats in with_remote.items() if any(fnmatch(rid.lower(), p.lower()) for p in pats)
    ]
    if len(hits) > 1:
        names = ", ".join(repr(h) for h in sorted(hits))
        return None, "", [f"scopes {names} all match remote {rid}; no scope applies"]
    return (hits[0] if hits else None), f"remote {rid}", []


def _repo_root(cwd: Path) -> Path | None:
    return next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), None)


def _nearest_lore_yml(cwd: Path, repo_root: Path | None) -> Path | None:
    """The nearest ``.lore.yml`` between ``cwd`` and the repo root."""
    stop = repo_root or cwd
    for d in (cwd, *cwd.parents):
        if (d / ".lore.yml").is_file():
            return d / ".lore.yml"
        if d == stop:
            return None
    return None


def _stamp(path: Path | None) -> tuple:
    try:
        st = path.stat() if path else None
    except OSError:
        st = None
    return (path, st.st_mtime_ns, st.st_size) if st else (path,)


# ponytail: unbounded per-process dict; a key per (cwd, config mtimes). Hooks
# are short-lived; bound it if a long-lived process ever sees many cwds.
_CACHE: dict[tuple, Route] = {}


def resolve_route(cwd: Path | str) -> Route:
    """Resolve the route for ``cwd`` (PRD 0016 order).

    Per key: environment, nearest ``.lore.yml``, the matched scope, the
    person's ``defaults``, then the code defaults. Cached per process,
    keyed on the cwd, the environment overrides and the config files'
    modification times; every prompt hook calls this.
    """
    cwd = Path(cwd).expanduser().resolve()
    repo_root = _repo_root(cwd)
    lore_yml_path = _nearest_lore_yml(cwd, repo_root)
    cfg_path = user_config_path()
    key = (
        cwd,
        tuple(os.environ.get(v) for v in _ENV.values()),
        _stamp(cfg_path),
        _stamp(lore_yml_path),
    )
    if key not in _CACHE:
        _CACHE[key] = _resolve(cwd, repo_root, lore_yml_path, cfg_path)
    return _CACHE[key]


def _resolve(
    cwd: Path, repo_root: Path | None, lore_yml_path: Path | None, cfg_path: Path
) -> Route:
    person = _read_raw(cfg_path)
    raw_scopes = person.get("scopes")
    scopes = (
        {n: s for n, s in raw_scopes.items() if isinstance(s, dict)}
        if isinstance(raw_scopes, dict)
        else {}
    )
    lore_yml = _read_raw(lore_yml_path) if lore_yml_path else {}
    defaults = person.get("defaults") if isinstance(person.get("defaults"), dict) else {}

    origins: dict[str, str] = {}
    notes: list[str] = []
    if os.environ.get(_ENV["scope"]):
        scope, origins["scope"] = os.environ[_ENV["scope"]], f"env {_ENV['scope']}"
    elif isinstance(lore_yml.get("scope"), str) and lore_yml["scope"] in scopes:
        scope, origins["scope"] = lore_yml["scope"], ".lore.yml"
    else:
        # An old offer file names a scope the person never defined: match by folder.
        if isinstance(lore_yml.get("scope"), str):
            notes.append(
                f".lore.yml names scope {lore_yml['scope']!r}, which {cfg_path} "
                "does not define; matching by folder"
            )
        scope, origin, match_notes = _match_scope(cwd, repo_root, scopes)
        notes.extend(match_notes)
        if scope:
            origins["scope"] = origin
    scope_cfg = scopes.get(scope or "", {})

    values: dict[str, object] = {}
    for key, cfg_key in _KEYS.items():
        layers = [
            (os.environ.get(_ENV[key]) if key in _ENV else None, f"env {_ENV.get(key)}"),
            (lore_yml.get(cfg_key), ".lore.yml"),
            (scope_cfg.get(cfg_key), f"scopes.{scope}"),
            (defaults.get(cfg_key), "defaults"),
        ]
        values[key], origins[key] = _CODE_DEFAULTS[key], "code default"
        for value, origin in layers:
            if value:
                values[key] = tuple(_as_list(value)) if key in ("sinks", "sources") else str(value)
                origins[key] = origin
                break

    # A wiki name becomes a path under wiki/; a committed file must not
    # point outside it.
    wiki = values["wiki"]
    if wiki is not None and not _WIKI_NAME.fullmatch(str(wiki)):
        notes.append(f"wiki {wiki!r} from {origins['wiki']} is not a wiki name; ignored")
        values["wiki"], origins["wiki"] = None, "rejected"

    # Reporting boundary (ADR 0016 invariant): only the person's own config
    # defines a sink. A name it does not define falls back to `file`.
    sinks = person.get("sinks")
    defined = {"file", *(sinks if isinstance(sinks, dict) else ())}
    undefined = [s for s in values["sinks"] if s not in defined]  # type: ignore[union-attr]
    if undefined:
        notes.append(
            f"sink {', '.join(map(repr, undefined))} from {origins['sinks']} is not defined "
            f"in {cfg_path}; using file"
        )
        values["sinks"] = tuple(
            dict.fromkeys(s if s in defined else "file" for s in values["sinks"])
        )  # type: ignore[union-attr]

    return Route(
        cwd=cwd,
        repo_root=repo_root,
        scope=scope,
        origins=origins,
        notes=tuple(notes),
        **values,  # type: ignore[arg-type]
    )
