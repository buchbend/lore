"""Resolve a style document — wiki override wins, packaged default is the fallback.

Resolution is whole-file: `<wiki>/style/<name>.md` if present, else the copy
shipped as package data under `lore_core/styles/`. A style document is prose,
so there are no merge semantics — merging a rules essay is ill-defined, and one
lookup leaves no cascade to debug. Customizing means copying the default into
the wiki and editing it. There is no per-repo layer.

The defaults live outside `templates/` on purpose: `lore init` copies the whole
templates tree into the vault, and a copy of the rules sitting at
`<lore_root>/templates/` would look editable while the resolver ignores it.

The Vale config that lints the writing rules resolves the same way, one
directory over: `<wiki>/style/vale/vale.ini` wins, else the packaged
`styles/vale/vale.ini`. See `default_vale_config_path`/`resolve_vale_config_path`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

# Style names Lore ships a default for. The per-wiki override uses the same
# file name under `<wiki>/style/`.
KNOWN_STYLES: tuple[str, ...] = ("writing-rules",)

# Retired style names and the style each one resolves to. Instruction files in
# other repos already carry `lore style show issue-register`, so the old name
# keeps working; the CLI writes a notice naming the current one.
DEPRECATED_ALIASES: dict[str, str] = {"issue-register": "writing-rules"}


class UnknownStyle(ValueError):
    """Raised for a style name Lore does not ship."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown style {name!r} — known styles: {', '.join(KNOWN_STYLES)}")
        self.name = name


def default_style_path(name: str) -> Path:
    """Return the packaged default for ``name``.

    Raises FileNotFoundError if the file is missing — the symptom of a broken
    install where ``[tool.setuptools.package-data]`` failed to bundle it.
    """
    path = Path(__file__).resolve().parent / "styles" / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Could not locate the packaged {name} at {path}. Reinstall Lore.")
    return path


def resolve_style_path(name: str, wiki_dir: Path | None = None) -> Path:
    """Return the path of the style document that wins for ``wiki_dir``.

    A deprecated alias resolves to the style it was renamed to. The override
    lookup uses the current file name, so a wiki that overrode the document
    under its old name renames that file too.
    """
    name = DEPRECATED_ALIASES.get(name, name)
    if name not in KNOWN_STYLES:
        raise UnknownStyle(name)
    if wiki_dir is not None:
        override = wiki_dir / "style" / f"{name}.md"
        if override.is_file():
            return override
    return default_style_path(name)


def default_vale_config_path() -> Path:
    """Return the packaged default Vale config (``styles/vale/vale.ini``).

    Raises FileNotFoundError if packaging broke, same contract as
    ``default_style_path``.
    """
    path = Path(__file__).resolve().parent / "styles" / "vale" / "vale.ini"
    if not path.is_file():
        raise FileNotFoundError(
            f"Could not locate the packaged Vale config at {path}. Reinstall Lore."
        )
    return path


def resolve_vale_config_path(wiki_dir: Path | None = None) -> Path:
    """Return the Vale config that wins for ``wiki_dir``: the wiki's own
    ``style/vale/vale.ini`` if present, else the packaged default.

    Same whole-file resolution as ``resolve_style_path`` — a Vale config and
    its ``StylesPath`` rule directory are one unit, not merged.
    """
    if wiki_dir is not None:
        override = wiki_dir / "style" / "vale" / "vale.ini"
        if override.is_file():
            return override
    return default_vale_config_path()


# --- the short-name check's ignore list ------------------------------------

# The Vale rule reads its ignore list from `glossary.txt` beside the ini, and
# the packaged copy ships without one. `vale_config_for` writes the pair into
# the cache so no file lands in a repo Lore does not own (ADR 0006).
GLOSSARY_IGNORE_FILE = "glossary.txt"
_CHECK_OFF = "WritingRules.UnknownShortName = NO"
_CHECK_ON = "WritingRules.UnknownShortName = YES"

# A glossary entry names its term in bold — `- **Vault** — ...` in this repo,
# `**Order**:` in the format spec. Both shapes are one bold span.
_BOLD_TERM = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
# Vale reports `C-ext` as a single token, so a hyphen stays inside a word while
# a space splits an entry into the separate words Vale will see.
_WORD = re.compile(r"[0-9A-Za-z][0-9A-Za-z_'-]*")


def _cache_dir() -> Path:
    """Host-local derived state, same env override the rest of the CLI reads.

    Duplicated from `lore_cli.context_cache` rather than imported: `lore_core`
    does not depend on `lore_cli`.
    """
    return Path(os.environ.get("LORE_CACHE", str(Path.home() / ".cache" / "lore")))


def glossary_terms(repo_dir: Path) -> list[str]:
    """Return the words ``repo_dir``'s ``CONTEXT.md`` defines, in order, once each.

    Returns an empty list where the repo holds no ``CONTEXT.md``. Bold is the
    only signal, so a bold run that is emphasis rather than a term widens the
    list. A widened list lets a word through that should have been flagged,
    which is the safe direction for a check that only ever advises.

    A repo that splits its glossary across a ``CONTEXT-MAP.md`` is read as
    having none.
    """
    context = repo_dir / "CONTEXT.md"
    if not context.is_file():
        return []
    terms: dict[str, None] = {}
    for span in _BOLD_TERM.findall(context.read_text(encoding="utf-8")):
        for word in _WORD.findall(span):
            terms.setdefault(word, None)
    return list(terms)


def vale_config_for(repo_dir: Path | None, wiki_dir: Path | None = None) -> Path:
    """Return the Vale config that lints a draft written for ``repo_dir``.

    Where the repo names no glossary the resolved config travels unchanged, and
    its ``WritingRules.UnknownShortName = NO`` line keeps the short-name check
    off. Where the repo names one, the whole config directory is copied to the
    cache, the glossary lands beside it, and the copy switches the check on.

    Copying is what keeps the generated word list out of the user's checkout
    and out of the installed package. Vale resolves a rule's ``ignore`` path
    against ``StylesPath``, so the list has to sit next to the rules, and the
    packaged directory is shared by every repo on the host.

    Pass ``repo_dir=None`` for the resolved config itself. That is the copy to
    seed a wiki override from: an override taken from a generated copy carries
    one repo's glossary into every repo attached to that wiki.
    """
    base = resolve_vale_config_path(wiki_dir)
    if repo_dir is None:
        return base
    terms = glossary_terms(repo_dir)
    if not terms:
        return base
    out = _cache_dir() / "vale" / f"{repo_dir.name}-{_content_key(base, terms)}"
    if (out / base.name).is_file():
        return out / base.name
    try:
        # The tree is built under a private name and moved into place, so a
        # path another process already captured stays readable while this one
        # builds. `os.replace` needs a free destination, which the content key
        # gives it: a different glossary or rule set is a different directory.
        # ponytail: superseded directories are left behind rather than swept.
        # They are a few kilobytes each and only a glossary edit makes one.
        staging = out.with_name(f"{out.name}.{os.getpid()}")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(base.parent, staging)
        (staging / GLOSSARY_IGNORE_FILE).write_text("\n".join(terms) + "\n", encoding="utf-8")
        ini = staging / base.name
        # The switch line is what the packaged config carries to keep the check
        # off. A hand-rolled wiki override may not carry it, and there `replace`
        # does nothing — Vale runs every rule under `BasedOnStyles` by default,
        # so that override runs the check against the glossary copied here.
        ini.write_text(
            ini.read_text(encoding="utf-8").replace(_CHECK_OFF, _CHECK_ON), encoding="utf-8"
        )
        try:
            os.replace(staging, out)
        except OSError:
            # Another process built the same key first. Both trees hold the
            # same bytes, so theirs serves and this one goes.
            shutil.rmtree(staging, ignore_errors=True)
            if not (out / base.name).is_file():
                raise
    except OSError:
        # A cache Lore cannot write costs the short-name check, never the lint.
        # The caller runs the resolved config, whose rule is off (ADR 0006 —
        # Vale never blocks the flow).
        return base
    return out / base.name


def _content_key(base: Path, terms: list[str]) -> str:
    """Digest of everything the generated tree is built from.

    Keying the directory by content rather than by repository path means a
    rebuild only ever writes a directory that does not exist yet. A Lore
    upgrade that ships a changed rule lands on a new key rather than reusing a
    stale tree.
    """
    digest = hashlib.sha256(str(base).encode())
    for path in sorted(base.parent.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(base.parent).as_posix().encode())
            digest.update(path.read_bytes())
    digest.update("\n".join(terms).encode())
    return digest.hexdigest()[:16]
