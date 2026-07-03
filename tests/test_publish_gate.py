"""Tests for ``lore_core.publish_gate`` — the blocking publish gate.

The gate runs between compose and append, ordered cheapest-first:
deterministic scanners (secrets, email, phone), then deterministic
phrasing lint, then one small-model detection call for fuzzy PII. The
first hit short-circuits. Anything unexpected fails CLOSED (withheld),
never a silent pass. The gate is a tripwire, not a guarantee.
"""

from __future__ import annotations

import secrets as _secrets

from lore_core import publish_gate as pg

# ---------------------------------------------------------------------------
# Deterministic scanners — hits and near-misses
# ---------------------------------------------------------------------------


class TestSecretScanner:
    def test_secret_key_is_a_hit(self):
        token = _secrets.token_urlsafe(40)
        assert pg.has_secret(f"the key is sk-{token} keep it safe") is True

    def test_base64_asset_hash_is_not_a_secret(self):
        # A short-ish base64-looking asset hash without an assignment
        # context is a near-miss, not a credential.
        assert pg.has_secret("asset digest: q1w2e3r4t5") is False

    def test_prose_without_secrets_is_clean(self):
        assert pg.has_secret("We refactored the flush path and ran the suite.") is False


class TestEmailScanner:
    def test_real_email_is_a_hit(self):
        assert pg.has_email("ping alice@example.com about the merge") is True

    def test_at_handle_mention_is_not_an_email(self):
        # A bare @handle or an @-anchor has no domain-with-TLD.
        assert pg.has_email("see @turn anchor and @alice on chat") is False

    def test_local_without_tld_is_not_an_email(self):
        assert pg.has_email("user@localhost was mentioned") is False


class TestPhoneScanner:
    def test_international_number_is_a_hit(self):
        assert pg.has_phone("call +1 415 555 2671 later") is True

    def test_grouped_us_number_is_a_hit(self):
        assert pg.has_phone("reach the desk at (415) 555-2671") is True

    def test_version_string_is_not_a_phone(self):
        assert pg.has_phone("bumped to v0.13.1 and 1.2.3 in the lockfile") is False

    def test_issue_ref_is_not_a_phone(self):
        assert pg.has_phone("closes #126 and references org/repo#131") is False

    def test_commit_sha_is_not_a_phone(self):
        assert pg.has_phone("landed in 3d9d36e after review") is False

    def test_iso_date_is_not_a_phone(self):
        assert pg.has_phone("dated 2026-07-03 in the header") is False

    def test_byte_count_is_not_a_phone(self):
        assert pg.has_phone("buffer cap 120 turns / 240000 chars") is False


class TestPhrasingLint:
    def test_todo_marker_is_a_hit(self):
        assert pg.phrasing_lint("TODO: wire the sweep path") != []

    def test_fixme_marker_is_a_hit(self):
        assert pg.phrasing_lint("the flush is racy (FIXME)") != []

    def test_imperative_bold_lead_is_a_hit(self):
        assert pg.phrasing_lint("**Fix the flush race condition**\n\ndetail. @42") != []

    def test_past_tense_bold_lead_is_clean(self):
        assert pg.phrasing_lint("**Fixed the flush race condition**\n\ndetail. @42") == []

    def test_must_should_task_language_is_a_hit(self):
        assert pg.phrasing_lint("The buffer should be refactored before release.") != []

    def test_stative_prose_is_clean(self):
        text = (
            "**Traced the flush race** \n\nThe buffer accumulated turns and "
            "retried at the next trigger; the give-up bound was discussed. @42"
        )
        assert pg.phrasing_lint(text) == []


# ---------------------------------------------------------------------------
# GateResult + evaluate() — ordering, short-circuit, fail-closed
# ---------------------------------------------------------------------------


class TestEvaluateClean:
    def test_clean_chapter_passes(self):
        text = "**Traced the flush race** \n\nThe buffer accumulated turns. @42"
        result = pg.evaluate(text)
        assert result.passed is True
        assert result.category == ""

    def test_empty_chapter_passes(self):
        assert pg.evaluate("").passed is True


class TestEvaluateHits:
    def test_secret_withheld_with_feedback(self):
        token = _secrets.token_urlsafe(40)
        result = pg.evaluate(f"**Notes** \n\nkey sk-{token} leaked. @1")
        assert result.passed is False
        assert result.category == pg.CATEGORY_SECRET
        assert result.feedback  # non-empty retry-prompt injection
        # Feedback must never echo the matched secret value.
        assert token not in result.feedback

    def test_email_withheld(self):
        result = pg.evaluate("**Contact** \n\nmail bob@example.com about it. @1")
        assert result.passed is False
        assert result.category == pg.CATEGORY_EMAIL
        assert "bob@example.com" not in result.feedback

    def test_phrasing_withheld_with_feedback(self):
        result = pg.evaluate("**Fix the race** \n\ndetail. @1")
        assert result.passed is False
        assert result.category == pg.CATEGORY_PHRASING
        assert result.feedback


class TestEvaluateOrdering:
    def test_scanner_beats_phrasing_when_both_present(self):
        # A chapter with BOTH a secret and an imperative lead: cheapest-first
        # means the scanner (secret) short-circuits before the phrasing lint.
        token = _secrets.token_urlsafe(40)
        result = pg.evaluate(f"**Fix the race** \n\nkey sk-{token}. @1")
        assert result.category == pg.CATEGORY_SECRET

    def test_detector_not_called_when_scanner_hits(self):
        calls = []

        class Spy:
            def detect(self, text):
                calls.append(text)
                return "pii"

        token = _secrets.token_urlsafe(40)
        pg.evaluate(f"key sk-{token}", detector=Spy())
        assert calls == []  # short-circuited before detection

    def test_detector_not_called_when_phrasing_hits(self):
        calls = []

        class Spy:
            def detect(self, text):
                calls.append(text)
                return None

        pg.evaluate("**Fix the race**\n\ndetail. @1", detector=Spy())
        assert calls == []


class TestEvaluateDetection:
    def test_detector_hit_withholds(self):
        class Hit:
            def detect(self, text):
                return "pii"

        result = pg.evaluate("**Reviewed the merge**\n\nordinary prose. @1", detector=Hit())
        assert result.passed is False
        assert result.category == pg.CATEGORY_PII

    def test_detector_clean_passes(self):
        class Clean:
            def detect(self, text):
                return None

        result = pg.evaluate("**Reviewed the merge**\n\nordinary prose. @1", detector=Clean())
        assert result.passed is True

    def test_no_detector_runs_deterministic_layers_only(self):
        # Without a detector the gate still runs scanners + lint.
        result = pg.evaluate("**Reviewed the merge**\n\nordinary prose. @1")
        assert result.passed is True


class TestFailClosed:
    def test_detector_error_fails_closed(self):
        class Boom:
            def detect(self, text):
                raise RuntimeError("model unreachable")

        result = pg.evaluate("**Reviewed the merge**\n\nordinary prose. @1", detector=Boom())
        assert result.passed is False
        assert result.category == pg.CATEGORY_ERROR
        assert result.feedback


# ---------------------------------------------------------------------------
# LlmPiiDetector — the one small-model detection call (STUBBED, never live)
# ---------------------------------------------------------------------------


class _FakeToolBlock:
    type = "tool_use"

    def __init__(self, data):
        self.input = data


class _FakeResponse:
    def __init__(self, data):
        self.content = [_FakeToolBlock(data)]
        self.model = "fake-detector-model"


class _FakeMessages:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._data)


class _FakeClient:
    def __init__(self, data):
        self.messages = _FakeMessages(data)
        self.backend_name = "fake"


class TestLlmPiiDetectorContract:
    def test_call_contract_single_tool_choice(self):
        client = _FakeClient({"sensitive": False})
        detector = pg.LlmPiiDetector(
            llm_client=client, model_resolver=lambda tier: "resolved-model"
        )
        detector.detect("some chapter text")
        assert len(client.messages.calls) == 1
        call = client.messages.calls[0]
        assert call["model"] == "resolved-model"
        assert call["tool_choice"]["type"] == "tool"
        # exactly one user message carrying the chapter text
        assert len(call["messages"]) == 1
        assert call["messages"][0]["role"] == "user"
        assert "some chapter text" in str(call["messages"][0]["content"])

    def test_sensitive_true_returns_category(self):
        client = _FakeClient({"sensitive": True, "category": "pii"})
        detector = pg.LlmPiiDetector(llm_client=client, model_resolver=lambda tier: "m")
        assert detector.detect("text") == "pii"

    def test_sensitive_false_returns_none(self):
        client = _FakeClient({"sensitive": False})
        detector = pg.LlmPiiDetector(llm_client=client, model_resolver=lambda tier: "m")
        assert detector.detect("text") is None

    def test_detector_wired_into_gate(self):
        client = _FakeClient({"sensitive": True, "category": "pii"})
        detector = pg.LlmPiiDetector(llm_client=client, model_resolver=lambda tier: "m")
        result = pg.evaluate("**Reviewed the merge**\n\nordinary prose. @1", detector=detector)
        assert result.passed is False
        assert result.category == pg.CATEGORY_PII
