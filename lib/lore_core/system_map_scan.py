"""Candidate edges for the system map, from the checkouts of a scope folder.

``scan`` reads files alone: git submodules, git URLs and package names in
``pyproject.toml`` and ``package.json``, ``FROM`` images in Dockerfiles and
images in compose files (PRD 0019, the system map). It maps each candidate
to the ``owner/repo`` of a checkout and leaves out what no checkout
provides. It calls no model and no network; ``git remote get-url`` reads
the local config only.

The ``lore:map`` skill reads the candidates, fills ``interface`` and
``breaks_when`` and proposes the map change in a wiki PR. SessionStart
spawns a background scan and shows the count the map lacks.
"""

from __future__ import annotations

import configparser
import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from lore_core.config import user_config_path
from lore_core.git import canonical_repo_name, current_repo
from lore_core.system_map import SystemMap

#: How deep below the scope folder a checkout may sit.
MAX_DEPTH = 3
_SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
_COMPOSE = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GIT_URL = re.compile(r"git\+(?P<url>[^\s;#]+)")
_SETUP_NAME = re.compile(r"""\bname\s*=\s*["']([^"']+)["']""")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True)
class Candidate:
    """One edge the files suggest. ``source`` is relative to the consumer's checkout."""

    consumer: str
    producer: str
    kind: str  # submodule | git-url | package | image
    source: str
    detail: str
    pin: str | None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def find_checkouts(folder: Path, depth: int = MAX_DEPTH) -> dict[str, Path]:
    """Each git checkout below *folder*, keyed by the ``owner/repo`` of its remote."""
    found: dict[str, Path] = {}
    keys: dict[str, str] = {}  # casefold slug → the slug as first seen

    def walk(path: Path, level: int) -> None:
        if (path / ".git").exists():
            slug = current_repo(path)
            if not slug:
                return
            seen = keys.setdefault(slug.casefold(), slug)
            # A worktree holds a `.git` file and may sit on an old branch;
            # the main checkout, with a `.git` folder, wins.
            if seen not in found or (
                (path / ".git").is_dir() and not (found[seen] / ".git").is_dir()
            ):
                found[seen] = path
            return
        if level >= depth:
            return
        try:
            children = sorted(p for p in path.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            walk(child, level + 1)

    walk(Path(folder), 0)
    return found


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _split_ref(url: str) -> tuple[str, str | None]:
    """Split a pip ``…/repo.git@<ref>`` URL; the ref may hold a ``/``."""
    host_end = url.find("/", url.index("://") + 3) if "://" in url else url.find(":")
    at = url.find("@", max(host_end, 0))
    if host_end < 0 or at < 0:
        return url, None
    return url[:at], url[at + 1 :] or None


def _pin_for_ref(ref: str | None) -> str | None:
    if not ref:
        return None
    return "sha" if _SHA.match(ref) else "tag"


def _pyproject(path: Path) -> dict:
    text = _read(path)
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _package_json(path: Path) -> dict:
    text = _read(path)
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _python_deps(data: dict) -> list[str]:
    """Every PEP 508 string of a ``pyproject.toml``: project, extras and groups."""
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    deps: list[object] = list(project.get("dependencies") or [])
    for extra in (project.get("optional-dependencies") or {}).values():
        deps.extend(extra or [])
    for group in (data.get("dependency-groups") or {}).values():
        deps.extend(group or [])
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name, spec in (poetry.get("dependencies") or {}).items():
        if isinstance(spec, dict) and spec.get("git"):
            ref = spec.get("rev") or spec.get("tag") or ""
            deps.append(f"{name} @ git+{spec['git']}" + (f"@{ref}" if ref else ""))
        else:
            deps.append(str(name))
    return [d for d in deps if isinstance(d, str)]


def _image_name(image: str) -> str:
    """*image* without its tag and digest, casefolded.

    A tag may hold a ``${VAR:-default}`` expression, so a colon or slash
    inside ``${…}`` neither ends the name nor starts the tag.
    """
    depth, last_slash, cut = 0, -1, len(image)
    text = image.strip()
    for i, ch in enumerate(text):
        if text.startswith("${", i):
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif depth == 0 and ch == "/":
            last_slash, cut = i, len(text)
        elif depth == 0 and ch in ":@" and i > last_slash and cut == len(text):
            cut = i
    return text[:cut].casefold()


def _image_repo(image: str, checkouts: dict[str, Path], built: dict[str, str]) -> str | None:
    """The checkout that builds *image*: a compose ``build`` or the image path."""
    key = _image_name(image)
    if key in built:
        return built[key]
    parts = key.split("/")
    by_slug = {slug.casefold(): slug for slug in checkouts}
    if len(parts) >= 2 and "/".join(parts[-2:]) in by_slug:
        return by_slug["/".join(parts[-2:])]
    return None


def _compose_services(path: Path) -> list[dict]:
    text = _read(path)
    if text is None:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    return [s for s in services.values() if isinstance(s, dict)]


def _python_key(name: str) -> str:
    """A Python package name in PEP 503 form: ``CCAT_ops.db`` → ``ccat-ops-db``."""
    return re.sub(r"[-_.]+", "-", name).casefold()


def _python_names(path: Path) -> list[str]:
    """The package names a checkout declares: ``pyproject.toml``, ``setup.cfg``, ``setup.py``."""
    names: list[str] = []
    name = (_pyproject(path / "pyproject.toml").get("project") or {}).get("name")
    if isinstance(name, str):
        names.append(name)
    cfg = _read(path / "setup.cfg")
    if cfg:
        parser = configparser.ConfigParser()
        try:
            parser.read_string(cfg)
            if parser.get("metadata", "name", fallback=""):
                names.append(parser.get("metadata", "name"))
        except configparser.Error:
            pass
    setup = _read(path / "setup.py")
    match = _SETUP_NAME.search(setup) if setup else None
    if match:
        names.append(match[1])
    return names


def _producers(checkouts: dict[str, Path]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Packages and images each checkout provides: name (casefold) → ``owner/repo``.

    A package name maps to every checkout that declares it; more than one
    makes the name ambiguous.
    """
    packages: dict[str, list[str]] = {}
    images: dict[str, str] = {}
    for slug, path in checkouts.items():
        keys = [_python_key(n) for n in _python_names(path)]
        name = _package_json(path / "package.json").get("name")
        if isinstance(name, str):
            keys.append(name.casefold())
        for key in dict.fromkeys(keys):
            packages.setdefault(key, []).append(slug)
        for compose in _COMPOSE:
            for service in _compose_services(path / compose):
                if service.get("build") and isinstance(service.get("image"), str):
                    images[_image_name(service["image"])] = slug
    return packages, images


def ambiguous(folder: Path) -> dict[str, list[str]]:
    """Package names that more than one checkout below *folder* declares."""
    packages, _ = _producers(find_checkouts(folder))
    return {name: sorted(slugs) for name, slugs in sorted(packages.items()) if len(slugs) > 1}


def _from_images(text: str) -> list[str]:
    """The ``FROM`` images of a Dockerfile, without stage names and ``scratch``."""
    stages: set[str] = set()
    images: list[str] = []
    for line in text.splitlines():
        words = line.split()
        if not words or words[0].upper() != "FROM":
            continue
        args = [w for w in words[1:] if not w.startswith("--")]
        if not args:
            continue
        image = args[0]
        if len(args) >= 3 and args[1].upper() == "AS":
            stages.add(args[2].casefold())
        if image.casefold() in stages or image == "scratch":
            continue
        images.append(image)
    return images


def scan(folder: Path) -> list[Candidate]:
    """Candidate edges between the checkouts below *folder*, in checkout order."""
    return scan_all(folder)[0]


def scan_all(folder: Path) -> tuple[list[Candidate], list[dict[str, object]]]:
    """The candidates, and each ambiguous package with its checkouts and consumers.

    A package that several checkouts declare gives no candidate: the scan
    cannot tell which checkout the consumer installs.
    """
    checkouts = find_checkouts(folder)
    by_slug = {slug.casefold(): slug for slug in checkouts}
    packages, built = _producers(checkouts)
    found: list[Candidate] = []
    unclear: dict[str, list[str]] = {
        name: [] for name, slugs in sorted(packages.items()) if len(slugs) > 1
    }

    def package(consumer: str, key: str) -> str | None:
        slugs = packages.get(key, [])
        if len(slugs) > 1:
            if consumer not in unclear[key]:
                unclear[key].append(consumer)
            return None
        return slugs[0] if slugs else None

    def add(
        consumer: str, producer: str | None, kind: str, source: str, detail: str, pin: str | None
    ) -> None:
        if not producer or producer.casefold() == consumer.casefold():
            return
        cand = Candidate(consumer, producer, kind, source, detail, pin)
        if cand not in found:
            found.append(cand)

    def by_url(url: str) -> str | None:
        slug = canonical_repo_name(url)
        return by_slug.get(slug.casefold()) if slug else None

    for consumer, path in checkouts.items():
        # One malformed file skips the rest of its checkout, not the whole scan.
        try:
            modules = path / ".gitmodules"
            if modules.is_file():
                parser = configparser.ConfigParser()
                try:
                    # Git indents keys with a tab; configparser reads that as a continuation.
                    lines = (line.strip() for line in (_read(modules) or "").splitlines())
                    parser.read_string("\n".join(lines))
                except configparser.Error:
                    parser = configparser.ConfigParser()
                for section in parser.sections():
                    url = parser.get(section, "url", fallback="")
                    add(consumer, by_url(url), "submodule", ".gitmodules", url, "sha")

            for dep in _python_deps(_pyproject(path / "pyproject.toml")):
                git = _GIT_URL.search(dep)
                if git:
                    url, ref = _split_ref(git["url"])
                    add(
                        consumer,
                        by_url(url),
                        "git-url",
                        "pyproject.toml",
                        dep.strip(),
                        _pin_for_ref(ref),
                    )
                    continue
                name = _PEP508_NAME.match(dep)
                if name:
                    add(
                        consumer,
                        package(consumer, _python_key(name[1])),
                        "package",
                        "pyproject.toml",
                        name[1],
                        "package",
                    )

            pkg = _package_json(path / "package.json")
            for field in ("dependencies", "devDependencies", "peerDependencies"):
                deps = pkg.get(field)
                if not isinstance(deps, dict):
                    continue
                for name, spec in deps.items():
                    spec = str(spec)
                    if spec.startswith(("git", "github:")) or "://" in spec:
                        url = spec.removeprefix("github:")
                        url = url if "/" in url and ":" in url else f"https://github.com/{url}"
                        ref = url.split("#", 1)[1] if "#" in url else None
                        add(
                            consumer,
                            by_url(url.split("#", 1)[0]),
                            "git-url",
                            "package.json",
                            f"{name}: {spec}",
                            _pin_for_ref(ref),
                        )
                        continue
                    add(
                        consumer,
                        package(consumer, str(name).casefold()),
                        "package",
                        "package.json",
                        str(name),
                        "package",
                    )

            for dockerfile in sorted(path.glob("Dockerfile*")):
                for image in _from_images(_read(dockerfile) or ""):
                    add(
                        consumer,
                        _image_repo(image, checkouts, built),
                        "image",
                        dockerfile.name,
                        image,
                        "image",
                    )
            for compose in _COMPOSE:
                for service in _compose_services(path / compose):
                    image = service.get("image")
                    if isinstance(image, str) and not service.get("build"):
                        add(
                            consumer,
                            _image_repo(image, checkouts, built),
                            "image",
                            compose,
                            image,
                            "image",
                        )
        except (TypeError, AttributeError, ValueError):
            continue
    ambiguous_uses = [
        {"package": name, "checkouts": sorted(packages[name]), "consumers": sorted(users)}
        for name, users in unclear.items()
    ]
    return found, ambiguous_uses


def missing(candidates: Iterable[Candidate], maps: Iterable[SystemMap]) -> list[Candidate]:
    """The candidates whose consumer and producer no edge of *maps* joins."""
    held = {(e.consumer.casefold(), e.producer.casefold()) for m in maps for e in m.edges}
    return [c for c in candidates if (c.consumer.casefold(), c.producer.casefold()) not in held]


def no_checkout(maps: Iterable[SystemMap], checkouts: dict[str, Path]) -> list[str]:
    """Repos of the maps that touch *checkouts* but have no checkout themselves."""
    have = {slug.casefold() for slug in checkouts}
    gone: list[str] = []
    for m in maps:
        if not have & {r.casefold() for r in m.repos}:
            continue
        gone.extend(r for r in m.repos if r.casefold() not in have and r not in gone)
    return gone


def scope_for(cwd: Path) -> tuple[str | None, Path | None, bool]:
    """The person's scope whose folder holds *cwd*: name, folder, the tidy switch."""
    from lore_core.config_schema import _read_raw

    scopes = _read_raw(user_config_path()).get("scopes")
    best: tuple[int, str, Path, bool] | None = None
    target = Path(cwd).expanduser().resolve()
    for name, cfg in (scopes if isinstance(scopes, dict) else {}).items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("path"), str):
            continue
        folder = Path(cfg["path"]).expanduser().resolve()
        if not target.is_relative_to(folder):
            continue
        switch = cfg.get("map") if isinstance(cfg.get("map"), dict) else {}
        tidy = switch.get("tidy", True) is not False
        if best is None or len(folder.parts) > best[0]:
            best = (len(folder.parts), name, folder, tidy)
    return (best[1], best[2], best[3]) if best else (None, None, True)


def _record_path(lore_root: Path, scope: str) -> Path:
    return lore_root / ".lore" / "map-scan" / f"{scope}.json"


def record_missing(lore_root: Path, scope: str, count: int) -> None:
    """Store how many candidates the map lacks, for the next SessionStart banner."""
    from lore_core.io import atomic_write_text

    path = _record_path(lore_root, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"missing": count}))


def hint(lore_root: Path, scope: str) -> str | None:
    """The banner line for the count the last background scan recorded."""
    try:
        data = json.loads(_record_path(lore_root, scope).read_text())
    except (OSError, ValueError):
        return None
    count = data.get("missing") if isinstance(data, dict) else None
    if not isinstance(count, int) or count <= 0:
        return None
    noun = "edge" if count == 1 else "edges"
    return f"System map: {count} candidate {noun} the map lacks. Run `/lore:map` to review them."


def stamp_path(lore_root: Path, scope: str) -> Path:
    """The spawn stamp: SessionStart spawns at most one scan per cooldown."""
    return lore_root / ".lore" / "map-scan" / f"{scope}.stamp"
