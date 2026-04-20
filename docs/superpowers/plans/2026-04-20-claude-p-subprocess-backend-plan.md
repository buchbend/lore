# claude -p Subprocess Backend — Implementation Plan (Plan 2.5 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Per-step TDD detail expanded by the executing subagent using current repo state.

**Goal:** Pivot all four curator LLM call sites from the `anthropic` Python SDK to `claude -p` subprocess invocations, so users on a Claude Code Pro/Max subscription do not need a separate `ANTHROPIC_API_KEY` and never double-pay for inference.

**Architecture:** Introduce an `LlmClient` protocol with a minimal `.messages.create(**kwargs) → response` shape (duck-typed subset of `anthropic.Anthropic`). Two implementations: `SDKClient` wraps `anthropic.Anthropic` unchanged (backward compat + CI path); `SubprocessClient` shells out to `claude -p --output-format json --json-schema <…> --tools "" --model <…>` and wraps the reply into an anthropic-shaped fake response so every existing call site keeps working untouched. A `make_llm_client(...)` factory selects the backend (config + env + PATH probe). Existing fake-anthropic test pattern is preserved; new tests use a dependency-injected fake subprocess runner.

**Tech Stack:** Python 3.11+, stdlib `subprocess` (stdlib `json`), `anthropic` (unchanged, still optional), Claude Code CLI **≥ 2.1.114** (requires `--json-schema`, `--tools ""`, and `--output-format json` with the `structured_output` field).

**Spec reference:** Issue [#16](https://github.com/buchbend/lore/issues/16); `docs/superpowers/HANDOVER-2026-04-19.md` (“Plan 2.5”); `docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md` §13 (model tier / cost).

**Verified CLI facts (v2.1.114, confirmed against the installed binary on 2026-04-20):**
- `claude -p "<prompt>" --output-format json` emits a single JSON object on stdout.
- `--json-schema '<schema>'` populates the top-level `structured_output` field with a schema-validated object (the `result` field then comes back as `""`).
- `--model <full-name>` accepts `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-7`, etc.
- `--tools ""` disables built-in tools; pure text/structured-output generation still works.
- `--append-system-prompt <text>` appends to the default system prompt (the curator instruction text goes here).
- **There is no `--max-turns` flag.** With `--tools ""`, `num_turns` is 1 or 2 (model occasionally retries for schema compliance); this is acceptable for a background curator.
- **Do NOT pass `--bare`.** Per `--help`: `--bare` forces `ANTHROPIC_API_KEY`/`apiKeyHelper` auth and ignores OAuth/keychain — exactly the subscription path Plan 2.5 is trying to preserve. Whole point of this plan is that `claude -p` *without* `--bare` reuses the signed-in Claude Code subscription.
- JSON response surfaces token + cost telemetry in `usage.input_tokens`, `usage.output_tokens`, `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, and `total_cost_usd`.

**Phases:**
- **A. Seam** (T1–T2): `LlmClient` protocol + `SDKClient` wrapper; nothing behaviourally changes.
- **B. Subprocess backend** (T3–T4): `SubprocessClient` with dependency-injected runner; error paths.
- **C. Factory + wiring** (T5–T6): backend resolver, `core.py` uses the factory, user-facing warning copy updated.
- **D. End-to-end** (T7): fake-subprocess E2E confirming a full Curator A pass works against the subprocess client.

Each task is independently committable. After every commit: `cd /home/buchbend/git/lore && pytest -q` — must stay green (524 passing at start of plan).

**Carry-over from Plan 2 review (memory: `feedback_lore_implementation_gotchas.md`):**
- `rich.Console.print()` does not accept `file=`. For stderr warnings, instantiate `err_console = Console(stderr=True)` and call `err_console.print(...)`.
- Avoid circular imports: `lib/lore_curator/llm_client.py` may import from `anthropic` (optional) but MUST NOT import from `lore_cli.*`.
- Tests import the fake via `from lore_curator.llm_client import ...` — keep the module under `lore_curator` so it travels with the curator package.
- Fake-anthropic test pattern (`FakeAnthropic(responses_by_tool={"classify": resp, "merge_judgment": resp, "cluster": resp, "abstract": resp})`) works unchanged as an `LlmClient` — it already has the `.messages.create(**kwargs)` shape. Only the *name* of the parameter users pass shifts from `anthropic_client` → `llm_client`, and we keep the old name as a compat alias to avoid rewriting every test in one commit.

---

## Phase A — Seam

### Task 1: `LlmClient` protocol + response types

**Files:**
- Create: `lib/lore_curator/llm_client.py`
- Test: `tests/test_llm_client_protocol.py`

**Goal:** Define the minimal interface every curator call site speaks to, plus the lightweight response types the `SubprocessClient` will synthesize. No behavior change; just types.

**Key API:**

```python
# lib/lore_curator/llm_client.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LlmClientError(RuntimeError):
    """Raised by LlmClient implementations on unrecoverable failure.

    Concrete reasons (exit-nonzero, timeout, malformed JSON, missing
    `structured_output`, binary-not-found) go in the exception message.
    Callers treat this the same way they treated an anthropic SDK error:
    log it and skip the slice (existing call sites already catch Exception).
    """


@dataclass(frozen=True)
class ToolUseBlock:
    """Mimics an anthropic.types.ToolUseBlock for the subset curators use.

    Existing code walks `resp.content`, checks `block.type == "tool_use"`
    and reads `block.input`. This dataclass satisfies that shape.
    """
    input: dict[str, Any]
    type: str = "tool_use"
    name: str = ""
    id: str = ""


@dataclass(frozen=True)
class LlmResponse:
    """Mimics an anthropic.types.Message for the subset curators use."""
    content: list[ToolUseBlock] = field(default_factory=list)
    model: str = ""
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens, output_tokens, …
    total_cost_usd: float | None = None


class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class LlmClient(Protocol):
    """The minimum shape the curators speak.

    Real anthropic.Anthropic satisfies this. SubprocessClient also does.
    FakeAnthropic in tests also does.
    """
    messages: _MessagesAPI
```

**Acceptance:**
- `test_llm_client_protocol_accepts_anthropic_shape` — given an object with `.messages.create(**kw)`, `isinstance(..., LlmClient)` is True (use `typing.runtime_checkable` or a structural check).
- `test_tool_use_block_matches_anthropic_contract` — `ToolUseBlock(input={"a":1}).type == "tool_use"` and `.input == {"a":1}`; satisfies the existing `_extract_tool_input` helper without modification.
- `test_llm_response_round_trip` — a `LlmResponse` containing one `ToolUseBlock` is walkable by the existing extractor pattern.
- `test_llm_client_error_is_runtimeerror` — `issubclass(LlmClientError, RuntimeError)`.

**Notes for the executing subagent:** Do **not** re-export from `lore_curator/__init__.py` yet — keep the surface internal until T5 wires it up. No other modules change in this task.

**Commit:** `feat(curator): add LlmClient protocol + LlmResponse/ToolUseBlock wrappers`

---

### Task 2: `SDKClient` — thin wrapper over `anthropic.Anthropic`

**Files:**
- Modify: `lib/lore_curator/llm_client.py`
- Test: `tests/test_llm_client_sdk.py`

**Goal:** Provide an explicit `SDKClient` class that holds an `anthropic.Anthropic` instance and exposes `.messages.create(**kwargs)` straight through. This is not required for correctness (the raw `anthropic.Anthropic` already satisfies `LlmClient`), but having an explicit class gives us one seam to add cost telemetry, retries, or debug logging later, and keeps the factory symmetric with `SubprocessClient`.

**Key API:**

```python
# Append to lib/lore_curator/llm_client.py

class SDKClient:
    """LlmClient backend that delegates to anthropic.Anthropic."""

    def __init__(self, *, api_key: str):
        import anthropic  # lazy — keeps anthropic an optional dep
        self._anthropic = anthropic.Anthropic(api_key=api_key)
        self.messages = self._anthropic.messages

    @property
    def backend_name(self) -> str:
        return "sdk"
```

**Acceptance:**
- `test_sdk_client_passes_through_to_anthropic` — monkeypatch `anthropic.Anthropic` with a fake; construct `SDKClient(api_key="x")`; call `.messages.create(model="m", …)`; assert the fake recorded the call with the same kwargs.
- `test_sdk_client_backend_name_is_sdk`.
- `test_sdk_client_raises_if_anthropic_missing` — monkeypatch `sys.modules["anthropic"]` to `None` (or use `importlib`'s mechanism) and assert `ImportError` surfaces cleanly.

**Commit:** `feat(curator): add SDKClient wrapper around anthropic.Anthropic`

---

## Phase B — Subprocess backend

### Task 3: `SubprocessClient` — happy path

**Files:**
- Modify: `lib/lore_curator/llm_client.py`
- Test: `tests/test_llm_client_subprocess.py`

**Goal:** Translate the anthropic-style `.messages.create(model=…, max_tokens=…, tools=[{…, input_schema}], tool_choice={type:"tool", name:…}, messages=[{role:"user", content: "<prompt>"}])` call into a `claude -p` subprocess invocation, parse the JSON, and return a `LlmResponse` whose `.content[0]` is a `ToolUseBlock` matching the tool schema's validated output. Dependency-inject the subprocess runner so tests never shell out to a real `claude` binary.

**Key API:**

```python
# Append to lib/lore_curator/llm_client.py
import json
import shutil
import subprocess
from typing import Callable

# A subprocess runner has the same shape as subprocess.run for the kwargs we use.
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


class _SubprocessMessagesAPI:
    def __init__(
        self,
        *,
        binary: str,
        runner: SubprocessRunner,
        timeout_s: float,
    ):
        self._binary = binary
        self._runner = runner
        self._timeout_s = timeout_s

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int | None = None,   # accepted for API compat; ignored by claude -p
        **_extra: Any,
    ) -> LlmResponse:
        prompt = _extract_user_text(messages)
        schema = _resolve_tool_schema(tools, tool_choice)  # None if plain text

        cmd = [
            self._binary, "-p", prompt,
            "--output-format", "json",
            "--tools", "",
            "--model", model,
        ]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema)]

        try:
            completed = self._runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LlmClientError(f"claude binary not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LlmClientError(f"claude -p timed out after {self._timeout_s}s") from exc

        if completed.returncode != 0:
            raise LlmClientError(
                f"claude -p exit {completed.returncode}: "
                f"{(completed.stderr or '').strip()[:500]}"
            )

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LlmClientError(
                f"claude -p returned non-JSON: {completed.stdout[:200]!r}"
            ) from exc

        if payload.get("is_error"):
            raise LlmClientError(
                f"claude -p reported error: subtype={payload.get('subtype')!r}, "
                f"api={payload.get('api_error_status')!r}"
            )

        if schema is not None:
            structured = payload.get("structured_output")
            if not isinstance(structured, dict):
                raise LlmClientError(
                    "claude -p returned no structured_output despite --json-schema"
                )
            tool_name = (tool_choice or {}).get("name") or ""
            block = ToolUseBlock(input=structured, name=tool_name)
            content = [block]
        else:
            text = payload.get("result") or ""
            # Plain-text path — synthesize a text-shaped block. Curators
            # don't use this path today, but the factory stays symmetric.
            content = [ToolUseBlock(input={"text": text}, type="text", name="")]

        return LlmResponse(
            content=content,
            model=payload.get("model", model),
            stop_reason=payload.get("stop_reason", "end_turn"),
            usage=(payload.get("usage") or {}),
            total_cost_usd=payload.get("total_cost_usd"),
        )


def _extract_user_text(messages: list[dict[str, Any]]) -> str:
    """Pull the single user-role text blob. Curators only ever send one user message."""
    if not messages:
        raise LlmClientError("messages=[] — nothing to send to claude -p")
    msg = messages[-1]
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(texts)
    raise LlmClientError(f"unsupported message content type: {type(content)!r}")


def _resolve_tool_schema(
    tools: list[dict[str, Any]] | None,
    tool_choice: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Find the tool the caller picked and return its input_schema.

    Curator calls always use `tool_choice = {type: "tool", name: "<X>"}`.
    Plain text → returns None.
    """
    if not tools or not tool_choice or tool_choice.get("type") != "tool":
        return None
    want = tool_choice.get("name")
    for t in tools:
        if t.get("name") == want:
            schema = t.get("input_schema")
            if isinstance(schema, dict):
                return schema
    raise LlmClientError(f"tool {want!r} not found in tools=[...]")


class SubprocessClient:
    """LlmClient backend that shells out to `claude -p`."""

    def __init__(
        self,
        *,
        binary: str = "claude",
        runner: SubprocessRunner | None = None,
        timeout_s: float = 120.0,
    ):
        self._binary = binary
        self.messages = _SubprocessMessagesAPI(
            binary=binary,
            runner=runner if runner is not None else subprocess.run,
            timeout_s=timeout_s,
        )

    @property
    def backend_name(self) -> str:
        return "subprocess"

    @classmethod
    def is_available(cls, *, binary: str = "claude") -> bool:
        """Cheap PATH probe — used by make_llm_client to decide backend."""
        return shutil.which(binary) is not None
```

**Acceptance (fake-runner tests, no real shelling):**
- `test_subprocess_builds_expected_cmdline` — given `tools=[{name:"classify", input_schema:{…}}], tool_choice={type:"tool", name:"classify"}, model="claude-haiku-4-5-20251001"`, the injected runner receives a `cmd` list that starts with `["claude","-p","<prompt>","--output-format","json","--tools","","--model","claude-haiku-4-5-20251001","--json-schema","<json>"]`. Assert each flag individually.
- `test_subprocess_passes_user_prompt_as_argv` — the user-role message content ends up at `cmd[2]`.
- `test_subprocess_parses_structured_output_to_tool_use_block` — runner returns a fake `CompletedProcess(returncode=0, stdout='{"is_error": false, "structured_output": {"noteworthy": true, "reason": "x", "title": "t"}, "usage": {"input_tokens": 5, "output_tokens": 3}, "total_cost_usd": 0.0001, "model": "claude-haiku-4-5-20251001", "stop_reason": "end_turn"}', stderr='')`; result is a `LlmResponse` whose `content[0]` is a `ToolUseBlock` with `input == {"noteworthy": True, "reason": "x", "title": "t"}`.
- `test_subprocess_surfaces_usage_and_cost` — `resp.usage["input_tokens"] == 5`, `resp.total_cost_usd == 0.0001`.
- `test_subprocess_passes_model_name_through` — `cmd` contains `["--model","claude-sonnet-4-6"]` when caller asked for sonnet.
- `test_subprocess_is_available_honours_path` — use `monkeypatch` on `shutil.which` to force both True and False.

**Commit:** `feat(curator): add SubprocessClient that translates messages.create → claude -p`

---

### Task 4: `SubprocessClient` — error paths

**Files:**
- Modify: `lib/lore_curator/llm_client.py` (no logic change expected; tests drive)
- Test: extend `tests/test_llm_client_subprocess.py`

**Goal:** Lock down every failure mode with a targeted test so the surface is predictable when we wire it into the curator.

**Acceptance:**
- `test_subprocess_raises_llmclienterror_on_nonzero_exit` — runner returns `returncode=2, stderr="auth failed\n"`; raises `LlmClientError` whose message includes `exit 2` and the stderr snippet.
- `test_subprocess_raises_on_malformed_json` — runner returns `stdout="<<not json>>"`; raises `LlmClientError` naming `non-JSON`.
- `test_subprocess_raises_on_api_error_payload` — runner returns `{"is_error": true, "subtype": "error_rate_limit", "api_error_status": 429, "structured_output": null}`; raises `LlmClientError` mentioning `subtype` and `429`.
- `test_subprocess_raises_on_missing_structured_output` — schema requested, runner returns success payload with `structured_output=None`; raises `LlmClientError`.
- `test_subprocess_raises_on_timeout` — runner raises `subprocess.TimeoutExpired(cmd=[...], timeout=0.1)`; translated to `LlmClientError` mentioning `timed out`.
- `test_subprocess_raises_on_missing_binary` — runner raises `FileNotFoundError("claude")`; translated to `LlmClientError` mentioning `claude binary not found`.
- `test_subprocess_raises_on_unknown_tool_name` — caller passes `tool_choice={type:"tool", name:"nope"}` with no matching entry in `tools`; raises `LlmClientError` mentioning `'nope' not found`.

**Commit:** `feat(curator): SubprocessClient surfaces every failure mode as LlmClientError`

---

## Phase C — Factory + wiring

### Task 5: `make_llm_client` factory + config env var

**Files:**
- Modify: `lib/lore_curator/llm_client.py`
- Test: `tests/test_make_llm_client.py`

**Goal:** One entry point chooses the backend given (a) explicit arg, (b) `LORE_LLM_BACKEND` env var, (c) PATH probe for `claude`, (d) presence of `ANTHROPIC_API_KEY`. Single source of truth consumed by `core.py`.

**Resolution rules (documented in the docstring — tested):**

```
make_llm_client(backend=..., api_key=..., binary="claude")

backend priority (first rule that applies wins):
  1. if `backend == "subscription"` → SubprocessClient if `claude` on PATH,
     else raise LlmClientError("subscription backend requested but claude binary not on PATH").
  2. if `backend == "api"` → SDKClient if `api_key` truthy,
     else raise LlmClientError("api backend requested but no ANTHROPIC_API_KEY provided").
  3. if `backend in (None, "auto")`:
       env = os.environ.get("LORE_LLM_BACKEND", "").strip().lower()
       if env == "subscription" → apply rule 1.
       if env == "api"          → apply rule 2.
       else (unset / "auto"):
         if SubprocessClient.is_available(binary=binary): return SubprocessClient(...)
         elif api_key:                                     return SDKClient(api_key=api_key)
         else:                                             return None
  4. unknown backend string → ValueError.

Returning None is deliberately allowed so the caller can render the existing
"AI classification skipped" warning with no behavior change from Plan 1/2.
```

**Key API:**

```python
# Append to lib/lore_curator/llm_client.py
import os

def make_llm_client(
    *,
    backend: str | None = None,
    api_key: str | None = None,
    binary: str = "claude",
) -> LlmClient | None:
    # ... implementation per rules above ...
```

**Acceptance:**
- `test_factory_explicit_subscription_returns_subprocess_client` — `shutil.which` stubbed to `/usr/bin/claude`.
- `test_factory_explicit_subscription_raises_if_binary_missing`.
- `test_factory_explicit_api_returns_sdk_client`.
- `test_factory_explicit_api_raises_without_api_key`.
- `test_factory_auto_prefers_subprocess_when_available` — both `claude` on PATH *and* api_key set → SubprocessClient wins.
- `test_factory_auto_falls_back_to_sdk_when_no_claude_binary` — `which` stubbed to None, api_key set → SDKClient.
- `test_factory_auto_returns_none_when_nothing_available`.
- `test_factory_reads_env_var` — `monkeypatch.setenv("LORE_LLM_BACKEND", "api")` + `backend=None` → SDKClient.
- `test_factory_explicit_arg_overrides_env` — env says "api", arg says "subscription" → SubprocessClient.
- `test_factory_raises_on_unknown_backend_string`.

**Commit:** `feat(curator): add make_llm_client factory (subscription | api | auto)`

---

### Task 6: Wire factory into `cmd_session_curator_run` and rewrite warning copy

**Files:**
- Modify: `lib/lore_curator/core.py` — replace the `anthropic.Anthropic(...)` block (~L853–L866) with a call to `make_llm_client(...)`.
- Modify: `lib/lore_curator/noteworthy.py`, `lib/lore_curator/session_filer.py`, `lib/lore_curator/cluster.py`, `lib/lore_curator/abstract.py`, `lib/lore_curator/curator_a.py`, `lib/lore_curator/curator_b.py` — accept a new keyword `llm_client` as an alias for `anthropic_client` (both parameter names work; internally prefer `llm_client`). Deprecate `anthropic_client` by leaving it but no longer passing it from `core.py`.
- Test: `tests/test_core_llm_wiring.py`
- Test: update `tests/test_curator_a.py` & `tests/test_curator_b.py` if any call sites assert on parameter *names*.

**Goal:** `lore session curator run` now resolves a backend via `make_llm_client`, and the user-facing warning adapts to what actually happened (subscription used, SDK used, nothing available).

**Warning copy rules (executed via a stderr-attached `Console(stderr=True)`):**

| State returned by factory | Warning line (yellow) |
| --- | --- |
| `SubprocessClient` instance | *no warning* — print `Curator backend: Claude Code subscription (claude -p)` at info level |
| `SDKClient` instance | *no warning* — print `Curator backend: Anthropic API (anthropic SDK)` at info level |
| `None` | `Curator will skip AI classification: neither 'claude' CLI on PATH nor ANTHROPIC_API_KEY set. Install Claude Code for subscription inference, or export ANTHROPIC_API_KEY for API inference.` |

**Implementation sketch (inside `cmd_session_curator_run` in core.py):**

```python
from lore_curator.llm_client import make_llm_client, LlmClientError

api_key = os.environ.get("ANTHROPIC_API_KEY", "") or None
try:
    llm_client = make_llm_client(api_key=api_key)
except LlmClientError as exc:
    err_console.print(f"[yellow]Warning:[/yellow] {exc}")
    llm_client = None

if llm_client is None:
    err_console.print(
        "[yellow]Warning:[/yellow] Curator will skip AI classification: "
        "neither 'claude' CLI on PATH nor ANTHROPIC_API_KEY set. Install "
        "Claude Code for subscription inference, or export "
        "ANTHROPIC_API_KEY for API inference."
    )
else:
    backend = getattr(llm_client, "backend_name", "sdk")
    label = {
        "subprocess": "Claude Code subscription (claude -p)",
        "sdk": "Anthropic API (anthropic SDK)",
    }.get(backend, backend)
    console.print(f"[dim]Curator backend: {label}[/dim]")

result = run_curator_a(
    lore_root=lore_root,
    scope=scope_obj,
    anthropic_client=llm_client,   # keyword kept for back-compat
    dry_run=dry_run,
    now=datetime.now(UTC),
)
# … and similarly for run_curator_b below.
```

**Acceptance:**
- `test_core_wires_subprocess_backend_when_claude_on_path` — monkeypatch `shutil.which` to return a path; monkeypatch `subprocess.run` (or inject via the factory) with a canned response; invoke `cmd_session_curator_run` via typer's `CliRunner`; assert the printed output contains `Claude Code subscription` and the fake runner was called at least once per curator step reached.
- `test_core_wires_sdk_backend_when_only_api_key_set` — stub `which` to None; set `ANTHROPIC_API_KEY=sk-x`; monkeypatch `anthropic.Anthropic` with a fake; assert the printed output contains `Anthropic API`.
- `test_core_prints_skip_warning_when_nothing_available` — stub `which` to None; clear env; assert warning copy matches.
- Entire existing suite (`pytest -q`) still passes — this is the critical gate. If a pre-existing test fails, it is a real signal about parameter-name coupling that must be preserved, not a reason to loosen the test.

**Commit:** `feat(curator): choose LLM backend via make_llm_client; prefer Claude Code subscription when available`

---

## Phase D — End-to-end

### Task 7: E2E — full Curator A run against fake subprocess backend

**Files:**
- Create: `tests/test_mvp_capture_subprocess_e2e.py`

**Goal:** One integration test that stands up Curator A exactly like `test_mvp_capture_e2e.py` does, but with a `SubprocessClient` wired to a fake runner returning canned `structured_output` payloads. This is the belt-and-suspenders proof that the translation layer composes with the rest of the pipeline end-to-end.

**Pattern (pseudo):**

```python
def test_e2e_subprocess_backend_produces_session_note(lore_root_with_attached_wiki, monkeypatch):
    lore_root, work = lore_root_with_attached_wiki

    # Canned claude -p responses keyed on what the prompt is asking for.
    def fake_runner(cmd, **kwargs):
        # Find --json-schema to know which schema/tool is being invoked.
        schema_idx = cmd.index("--json-schema")
        schema = json.loads(cmd[schema_idx + 1])
        keys = set(schema.get("properties", {}).keys())
        if "noteworthy" in keys:
            payload = {
                "is_error": False,
                "structured_output": {
                    "noteworthy": True, "reason": "real work",
                    "title": "Refactor the thing",
                    "bullets": ["touched X", "shipped Y"],
                    "files_touched": ["x.py"], "entities": [], "decisions": [],
                },
                "usage": {"input_tokens": 100, "output_tokens": 50,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                "total_cost_usd": 0.001,
                "model": "claude-haiku-4-5-20251001",
                "stop_reason": "end_turn",
            }
        elif keys == {"new"} or "merge" in keys:  # merge_judgment tool
            payload = {
                "is_error": False,
                "structured_output": {"new": True},
                "usage": {"input_tokens": 30, "output_tokens": 5,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                "total_cost_usd": 0.0001,
                "model": "claude-haiku-4-5-20251001",
                "stop_reason": "end_turn",
            }
        else:
            raise AssertionError(f"unexpected schema keys: {keys}")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(payload), stderr="")

    # Inject: make_llm_client returns a SubprocessClient wired to fake_runner.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    from lore_curator import llm_client as llm_mod
    real_subprocess_client = llm_mod.SubprocessClient
    monkeypatch.setattr(
        llm_mod, "SubprocessClient",
        lambda **kw: real_subprocess_client(runner=fake_runner, **{k: v for k, v in kw.items() if k != "runner"}),
    )

    # Now drive exactly the same steps test_mvp_capture_e2e.py drives,
    # then assert: (a) a session note was created, (b) it has draft: true,
    # (c) the ledger advanced, (d) fake_runner was called at least twice
    # (classify + merge_judgment).
    ...
```

**Acceptance:**
- `test_e2e_subprocess_backend_produces_session_note` — the vault now has `wiki/<wiki>/sessions/<stem>.md` with `draft: true` and the classify-supplied title & bullets; ledger watermark advanced; fake_runner was called twice with two distinct `--json-schema` payloads.
- `test_e2e_subprocess_backend_binary_missing_path` — `shutil.which` stubbed to None, `ANTHROPIC_API_KEY` unset → curator skips AI classification, warning rendered, no session note created, suite green.

**Commit:** `test(curator): end-to-end curator A run against fake claude -p subprocess backend`

---

## Follow-up issues to file (out of scope for Plan 2.5; track separately)

These surfaced during the CLI verification phase and should be written up in GitHub but are deliberately **not** in this plan, per YAGNI:

1. **Prompt-cache cost on subscription.** Each `claude -p` call creates ~17k cache tokens of default Claude Code system prompt + env context. On subscription this doesn't show up as $, but it counts against rate-limit windows. Consider `--system-prompt` (replace, not append) with a minimal curator-only prompt once we've measured the real impact over a week of background runs. File as `perf(curator): trim claude -p default system prompt for cache reuse`.
2. **Cost-per-run telemetry.** `LlmResponse.total_cost_usd` now flows out of every call but we still only surface counts in the `Curator A/B` summary. File as `feat(curator): surface total_cost_usd + token aggregates in curator run summary`.
3. **`--exclude-dynamic-system-prompt-sections`.** Once the minimum prompt work above lands, this flag improves cross-run cache hits. File as a follow-up to (1).

---

## Self-review pass

| Check | Result |
| --- | --- |
| Spec coverage — every affected module from the handover (noteworthy / session_filer / cluster / abstract) | All four reached via T6 wiring, since they each receive the `LlmClient` through existing `anthropic_client` kwarg. No per-module surgery needed. |
| Placeholder scan | None found — every task has full code or explicit acceptance tests. |
| Type consistency | `LlmClient`, `LlmResponse`, `ToolUseBlock`, `LlmClientError`, `SubprocessClient`, `SDKClient`, `make_llm_client` names appear identical in every task. The `backend_name` property is identical across both clients (`"subprocess"` vs `"sdk"`). |
| DRY / YAGNI | One protocol, two implementations, one factory. No retry framework, no telemetry beyond what the JSON naturally surfaces, no abstract BaseClient parent. |
| TDD | Every task lists acceptance tests before the implementation sketch. |
| Commit cadence | Seven small commits, each atomically green. |
| Gotchas from memory | Addressed: `Console(stderr=True)` for warnings; `lore_curator/` (not `lore_cli/`) hosts the new module; optional `anthropic` import is lazy; fake-anthropic test pattern preserved. |

---

## How to execute

```bash
cd /home/buchbend/git/lore

# Worktree for isolation (optional but recommended per gotchas):
git worktree add /home/buchbend/git/lore-claudep -b feat/claude-p-subprocess main
pip install -e /home/buchbend/git/lore-claudep

# Or in-place on main if you prefer (current state is ~40 commits ahead of origin):
pip install -e /home/buchbend/git/lore

# Then: superpowers:subagent-driven-development, one task per fresh subagent.
pytest -q   # baseline: 524 passed
```

After T7 commits green, run the full smoke test end-to-end:

```bash
unset ANTHROPIC_API_KEY                  # prove subscription path works standalone
lore session curator run --dry-run
# Expected: "Curator backend: Claude Code subscription (claude -p)"
```
