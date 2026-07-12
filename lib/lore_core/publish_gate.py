"""Blocking publish gate — the last check before a chapter joins the note.

Every composed chapter passes through :func:`evaluate` before it is
appended to the shared session note. The gate is **safety-only** — voice
and style are the compose prompt's job and never withhold a chapter. It
runs cheapest-first and short-circuits on the first hit:

1. **Deterministic scanners** — high-entropy secrets (reusing the secret
   patterns in :mod:`lore_core.redaction`), email addresses, phone
   numbers.
2. **One small-model detection call** for fuzzy PII/secrets that slip the
   deterministic layers. Detection is *pattern recognition*, not truth
   verification, so it is exempt from the no-LLM-judges rule — but it is
   a **tripwire, not a guarantee**, and is documented as such.

The gate **fails closed**: any scanner error, and any error from the
detection call, withholds the chapter rather than letting it through. A
false withhold only costs a marker and a quarantine entry; a false pass
could leak a secret into the shared vault.

On a withheld verdict the caller runs :func:`apply_withhold`, which owns
the two terminal side-effects: a deterministic withheld-marker chapter is
appended to the note, and the composed text is stored in the private
quarantine sidecar (:mod:`lore_core.quarantine`) for review. The retry
loop that sits between the first hit and the terminal give-up lives in
its own layer and calls this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lore_core import note_document as _nd
from lore_core import quarantine as _quarantine
from lore_core.redaction import redact

__all__ = [
    "CATEGORY_SECRET",
    "CATEGORY_EMAIL",
    "CATEGORY_PHONE",
    "CATEGORY_PII",
    "CATEGORY_ERROR",
    "Gate",
    "GateResult",
    "PASS",
    "PassThroughGate",
    "PublishGate",
    "Detector",

    "LlmPiiDetector",
    "WithholdOutcome",
    "has_secret",
    "has_email",
    "has_phone",
    "evaluate",
    "apply_withhold",
]


# Gate categories. A category names *what tripped the gate*; it drives the
# retry feedback, the safe marker reason, and the quarantine record. Values
# are never the matched text — see the feedback map below.
CATEGORY_SECRET = "secret"
CATEGORY_EMAIL = "email"
CATEGORY_PHONE = "phone"
CATEGORY_PII = "pii"
CATEGORY_ERROR = "gate-error"

# Retry-prompt feedback per category. The retry loop injects this string
# verbatim into the next compose prompt, so it describes the class of
# problem and how to avoid it — and NEVER echoes the matched value, which
# would re-plant the secret straight back into the next prompt.
_FEEDBACK: dict[str, str] = {
    CATEGORY_SECRET: (
        "The draft contained a credential or secret-shaped token. Re-compose "
        "without any keys, tokens, passwords, or other secrets — refer to them "
        "only in the abstract (e.g. 'an API key was rotated')."
    ),
    CATEGORY_EMAIL: (
        "The draft contained an email address. Re-compose without personal "
        "contact details; refer to people by role or handle, never by contact "
        "information."
    ),
    CATEGORY_PHONE: (
        "The draft contained a phone number. Re-compose without personal contact details."
    ),
    CATEGORY_PII: (
        "A detection pass flagged possible personal or sensitive information. "
        "Re-compose without personal data or secrets; keep only what is safe to "
        "share with colleagues."
    ),
    CATEGORY_ERROR: (
        "The publish gate could not complete its checks; the chapter is withheld as a precaution."
    ),
}

# Safe, value-free marker reasons. These render into the note body, which
# travels to the shared vault, so they must never carry the matched text.
_MARKER_REASON: dict[str, str] = {
    CATEGORY_SECRET: "a credential/secret was detected",
    CATEGORY_EMAIL: "an email address was detected",
    CATEGORY_PHONE: "a phone number was detected",
    CATEGORY_PII: "possible personal or sensitive data was detected",
    CATEGORY_ERROR: "the publish gate errored (withheld as a precaution)",
}


@dataclass(frozen=True)
class GateResult:
    """Verdict for one chapter — the single type shared composer→gate→append.

    ``passed`` is the whole result; on a withhold, ``category`` names the
    class of problem and ``feedback`` is the retry-prompt injection. Both
    are empty on a pass, and ``feedback`` never contains the matched value.

    This is the one canonical verdict type: the chapter composer imports
    and re-exports it rather than defining a parallel copy, so a gate's
    verdict flows straight into the compose retry loop.
    """

    passed: bool
    category: str = ""
    feedback: str = ""

    @classmethod
    def ok(cls) -> GateResult:
        """A passing verdict (the composer's clean path)."""
        return PASS

    @classmethod
    def withheld(cls, category: str, feedback: str) -> GateResult:
        """A withheld verdict carrying its category and retry feedback."""
        return cls(passed=False, category=category, feedback=feedback)


# The singleton pass result — cheaper than allocating one per clean chapter.
PASS = GateResult(passed=True)


def _withheld(category: str) -> GateResult:
    return GateResult(passed=False, category=category, feedback=_FEEDBACK[category])


# ---------------------------------------------------------------------------
# Deterministic scanners
# ---------------------------------------------------------------------------


# Email: a local part, '@', and a domain that ends in a real TLD. Requiring
# the dotted TLD is what separates a real address from an ``@handle`` mention
# or a bare ``user@localhost``.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Phone: two shapes, each guarded so version strings, issue refs, commit
# SHAs, ISO dates, and byte counts do not read as numbers to call.
#   - international: a leading '+', then a run of digits/separators.
#   - NANP-grouped: NNN sep NNN sep NNNN, e.g. (415) 555-2671 / 415.555.2671.
# The leading ``(?<![\w.])`` stops a match starting mid-token (so ``v1.2.3``
# and ``3d9d36e`` never anchor a phone).
_PHONE_INTL_RE = re.compile(r"(?<![\w.])\+\d[\d\s().\-]{6,}\d(?!\w)")
_PHONE_NANP_RE = re.compile(r"(?<![\w.])\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\w)")
# International numbers carry 8–15 significant digits; the gate keeps the low
# end conservative so a stray signed integer is not read as a number to call.
_PHONE_INTL_MIN_DIGITS = 8


def has_secret(text: str) -> bool:
    """True if ``text`` contains a known secret pattern (see redaction)."""
    _redacted, hits = redact(text)
    return bool(hits)


def has_email(text: str) -> bool:
    """True if ``text`` contains an email address (local@domain.tld)."""
    return _EMAIL_RE.search(text) is not None


def has_phone(text: str) -> bool:
    """True if ``text`` contains something shaped like a phone number."""
    for m in _PHONE_INTL_RE.finditer(text):
        if sum(c.isdigit() for c in m.group()) >= _PHONE_INTL_MIN_DIGITS:
            return True
    return _PHONE_NANP_RE.search(text) is not None


# ---------------------------------------------------------------------------
# The gate seam consumed by the generative layers
# ---------------------------------------------------------------------------


@runtime_checkable
class Gate(Protocol):
    """Blocking check between what a model produced and what the note keeps.

    Implementations scan the exact text that would land in the note and return
    a :class:`GateResult`. The generative layers only react to the verdict; the
    scanners and the detection call live here. :class:`PublishGate` is the one
    implementation that ships.
    """

    def evaluate(self, chapter_text: str) -> GateResult: ...


class PassThroughGate:
    """Default gate: everything passes. Lets replay tests run standalone."""

    def evaluate(self, chapter_text: str) -> GateResult:  # noqa: ARG002
        return GateResult.ok()


# ---------------------------------------------------------------------------
# Detection seam (the one small-model call)
# ---------------------------------------------------------------------------


@runtime_checkable
class Detector(Protocol):

    """A fuzzy PII/secret detector.

    ``detect`` returns a category string on a hit (e.g. ``"pii"`` or
    ``"secret"``), or ``None`` when clean. It may raise on backend
    failure — the gate treats any raise as a withhold (fail closed).
    """

    def detect(self, text: str) -> str | None: ...


_DETECT_TOOL = {
    "name": "flag_sensitive",
    "description": (
        "Flag whether the text contains personal data (names tied to contact "
        "info, addresses, IDs) or secrets/credentials. Pattern recognition "
        "only — do not judge whether claims in the text are true."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sensitive": {
                "type": "boolean",
                "description": "True if the text contains PII or a secret.",
            },
            "category": {
                "type": "string",
                "description": "One of: pii, secret, none.",
            },
            "reason": {"type": "string"},
        },
        "required": ["sensitive"],
    },
}

_DETECT_PROMPT = (
    "You are a privacy tripwire. Inspect the text below and decide whether it "
    "contains personal data (a person tied to contact details, an address, a "
    "government/account identifier) or a secret/credential (API key, token, "
    "password, private key). This is pattern recognition, not fact-checking — "
    "do not judge whether the text's claims are correct, only whether "
    "sensitive strings are present. Respond via the `flag_sensitive` tool.\n\n"
    "--- text ---\n"
)


class LlmPiiDetector:
    """Detector backed by one small-model tool-use call.

    Mirrors the curator LLM-call convention: a single forced-tool
    ``messages.create`` and a walk of ``resp.content`` for the tool_use
    block. The model tier is resolved by the caller-supplied
    ``model_resolver`` (same shape the curators use), defaulting to the
    cheapest tier — detection is a fast tripwire, not deep reasoning.
    """

    def __init__(
        self,
        *,
        llm_client: Any,
        model_resolver: Callable[[str], str],
        tier: str = "simple",
        max_tokens: int = 256,
    ) -> None:
        self._client = llm_client
        self._resolve = model_resolver
        self._tier = tier
        self._max_tokens = max_tokens

    def detect(self, text: str) -> str | None:
        model = self._resolve(self._tier)
        resp = self._client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            tools=[_DETECT_TOOL],
            tool_choice={"type": "tool", "name": "flag_sensitive"},
            messages=[{"role": "user", "content": _DETECT_PROMPT + text}],
        )
        data = _extract_tool_input(resp)
        if not data.get("sensitive"):
            return None
        category = str(data.get("category") or "").strip().lower()
        if category in {CATEGORY_PII, CATEGORY_SECRET}:
            return category
        return CATEGORY_PII


def _extract_tool_input(resp: Any) -> dict[str, Any]:
    """Pull the tool_use input dict from an Anthropic-style response."""
    for block in getattr(resp, "content", []) or []:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype == "tool_use":
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input")
            if isinstance(inp, dict):
                return inp
    raise ValueError("detection: no tool_use block in response")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _run_scanners(text: str) -> str | None:
    """Return the first scanner category that hits, else ``None``.

    Ordered by severity, not cost (all three are cheap regex): a leaked
    secret is the worst outcome, so it is checked first.
    """
    if has_secret(text):
        return CATEGORY_SECRET
    if has_email(text):
        return CATEGORY_EMAIL
    if has_phone(text):
        return CATEGORY_PHONE
    return None


def evaluate(chapter_text: str, *, detector: Detector | None = None) -> GateResult:
    """Evaluate one composed chapter. Returns PASS or a withheld verdict.

    Cheapest-first, short-circuiting on the first hit: deterministic
    scanners, then — only if they pass and a detector is supplied — one
    detection call. Fails closed: an error in any layer withholds rather
    than passes. Style/voice is deliberately not judged here.
    """
    try:
        category = _run_scanners(chapter_text)
        if category is not None:
            return _withheld(category)

        if detector is not None:
            try:
                hit = detector.detect(chapter_text)
            except Exception:  # noqa: BLE001 — detection error must fail closed
                return _withheld(CATEGORY_ERROR)
            if hit:
                # Report under the detector's category when it is one we know,
                # else the generic PII bucket; feedback stays value-free.
                category = hit if hit in _FEEDBACK else CATEGORY_PII
                return _withheld(category)

        return PASS
    except Exception:  # noqa: BLE001 — any gate failure withholds, never passes
        return _withheld(CATEGORY_ERROR)


class PublishGate:
    """The real gate as a ``Gate`` object the chapter composer can inject.

    The composer consumes a ``gate.evaluate(chapter_text) -> GateResult``
    seam; this binds an optional :class:`Detector` once and forwards each
    call to the module-level :func:`evaluate`. Constructing it with no
    detector runs the deterministic scanners only (the fuzzy-PII
    detection call is skipped), which is the safe default when no
    small-model backend is configured.
    """

    def __init__(self, *, detector: Detector | None = None) -> None:
        self._detector = detector

    def evaluate(self, chapter_text: str) -> GateResult:
        return evaluate(chapter_text, detector=self._detector)


# ---------------------------------------------------------------------------
# Terminal withhold side-effects (marker + quarantine)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WithholdOutcome:
    """Result of :func:`apply_withhold`."""

    chapter_n: int  # 1-based number of the appended marker chapter
    entry_id: str  # id of the quarantine entry holding the composed text


def apply_withhold(
    note_path: Path,
    *,
    result: GateResult,
    composed_text: str,
    slice_from_turn: int,
    slice_to_turn: int,
    lore_root: Path | None = None,
    quarantine_dir: Path | None = None,
    wiki_root: Path | None = None,
) -> WithholdOutcome:
    """Run the terminal side-effects for a withheld chapter.

    Appends a deterministic withheld-marker chapter to the note (safe,
    value-free reason) and stores the full composed text in the private
    quarantine sidecar. The unsafe text never touches the shared note.

    ``result`` must be a withheld :class:`GateResult`; passing a PASS is a
    programming error.
    """
    if result.passed:
        raise ValueError("apply_withhold called with a passing GateResult")

    category = result.category or CATEGORY_ERROR
    reason = _MARKER_REASON.get(category, _MARKER_REASON[CATEGORY_ERROR])

    entry = _quarantine.add_entry(
        category=category,
        note_path=str(note_path),
        from_turn=slice_from_turn,
        to_turn=slice_to_turn,
        composed_text=composed_text,
        lore_root=lore_root,
        quarantine_dir=quarantine_dir,
    )
    chapter_n = _nd.append_marker_chapter(
        note_path,
        kind=_nd.MARKER_WITHHELD,
        reason=reason,
        slice_from_turn=slice_from_turn,
        slice_to_turn=slice_to_turn,
        wiki_root=wiki_root,
    )
    return WithholdOutcome(chapter_n=chapter_n, entry_id=entry.id)
