"""Tests for `lore_core.redaction` — Secret redaction pre-pass."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from lore_core.redaction import RedactionHit, redact


class TestSkApiKeys:
    """Anthropic and OpenAI style sk-* API keys."""

    def test_redaction_catches_sk_ant_key(self):
        """Anthropic-style sk-ant-api03-<random> gets replaced."""
        token = secrets.token_urlsafe(40)
        text = f"my key is sk-ant-api03-{token} please"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "sk-api-key"
        assert "[REDACTED:sk-api-key]" in redacted
        assert token not in redacted

    def test_redaction_catches_sk_openai_key(self):
        """OpenAI-style sk-<random> (no -ant-api03) gets replaced."""
        token = secrets.token_urlsafe(40)
        text = f"key: sk-{token}"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "sk-api-key"
        assert "[REDACTED:sk-api-key]" in redacted


class TestGithubToken:
    """GitHub personal access tokens."""

    def test_redaction_catches_ghp_key(self):
        """ghp_<36 alphanum> replaced."""
        # 36 alphanumeric chars after ghp_
        token = secrets.token_hex(18)  # hex gives us alphanum, 18*2=36 chars
        text = f"github token ghp_{token} should be redacted"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "ghp-token"
        assert "[REDACTED:ghp-token]" in redacted
        assert token not in redacted


class TestGoogleApiKey:
    """Google API keys (AIza...)."""

    def test_redaction_catches_aiza_key(self):
        """Google API key pattern AIza<35 more> replaced."""
        # AIza followed by 35 alphanumeric chars (per spec: [0-9A-Za-z_-])
        import string
        charset = string.ascii_letters + string.digits + "_-"
        suffix = "".join(secrets.choice(charset) for _ in range(35))
        text = f"API key AIza{suffix} here"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "aiza-key"
        assert "[REDACTED:aiza-key]" in redacted
        assert suffix not in redacted


class TestAwsAccessKey:
    """AWS access key IDs."""

    def test_redaction_catches_aws_access_key(self):
        """AKIA<16 upper/digit> replaced."""
        # AKIA followed by 16 uppercase + digits
        suffix = "".join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(16))
        text = f"aws key AKIA{suffix} is secret"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "aws-access-key"
        assert "[REDACTED:aws-access-key]" in redacted
        assert suffix not in redacted


class TestJwt:
    """JSON Web Tokens (three base64url segments)."""

    def test_redaction_catches_jwt(self):
        """Three base64url segments total >60 chars replaced."""
        # Create a valid-looking JWT: header.payload.signature
        header = secrets.token_urlsafe(20)
        payload = secrets.token_urlsafe(20)
        signature = secrets.token_urlsafe(20)
        jwt_token = f"{header}.{payload}.{signature}"
        # Should be >60 chars: 20+1+20+1+20 = 62
        assert len(jwt_token) > 60
        text = f"token: {jwt_token} expired"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "jwt"
        assert "[REDACTED:jwt]" in redacted
        assert jwt_token not in redacted

    def test_redaction_jwt_short_not_matched(self):
        """Very short three-segment tokens (total <60 chars) not matched."""
        # Create a short JWT (total < 60 chars)
        jwt_token = "a.b.c"  # Only 5 chars total
        text = f"token: {jwt_token} short"
        redacted, hits = redact(text)
        # Should not match because total < 60 chars
        assert len(hits) == 0
        assert redacted == text


class TestPemPrivateKey:
    """PEM-formatted private keys."""

    def test_redaction_catches_pem_private_key(self):
        """Full PEM block replaced by single marker."""
        pem = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz
MIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz
-----END RSA PRIVATE KEY-----"""
        text = f"Private key:\n{pem}\n\nKeep this."
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "pem-private-key"
        assert "[REDACTED:pem-private-key]" in redacted
        assert "BEGIN RSA PRIVATE KEY" not in redacted

    def test_redaction_pem_ec_key(self):
        """EC PRIVATE KEY variant also matched."""
        pem = """-----BEGIN EC PRIVATE KEY-----
MIIEpAIBAAKCAQEA1234567890
-----END EC PRIVATE KEY-----"""
        text = f"key: {pem}"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "pem-private-key"


class TestHighEntropyCredential:
    """High-entropy credentials from assignment patterns."""

    def test_redaction_entropy_gate_low_entropy_password_not_redacted(self):
        """password=<low-entropy, e.g. repeated chars> NOT redacted."""
        # All 'a's: entropy ~0
        text = 'password=aaaaaaaaaaaaaaaaaaaaaaaaaa'
        redacted, hits = redact(text)
        assert len(hits) == 0
        assert redacted == text

    def test_redaction_entropy_gate_high_entropy_redacted(self):
        """password=<high-entropy random 24+ chars> redacted."""
        # Random high-entropy string, well over 24 chars
        random_val = secrets.token_urlsafe(32)
        assert len(random_val) >= 24
        text = f"password={random_val}"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "high-entropy-credential"
        assert "[REDACTED:high-entropy-credential]" in redacted
        assert random_val not in redacted

    def test_redaction_entropy_secret_assignment(self):
        """secret: <high-entropy> also matched."""
        random_val = secrets.token_urlsafe(32)
        text = f"secret: {random_val}"
        redacted, hits = redact(text)
        assert len(hits) == 1
        assert hits[0].kind == "high-entropy-credential"

    def test_redaction_entropy_api_key_assignment(self):
        """api_key=<high-entropy> or api-key=<high-entropy> matched."""
        random_val = secrets.token_urlsafe(32)
        text = f"api_key={random_val}"
        redacted_1, hits_1 = redact(text)
        assert len(hits_1) == 1

        text2 = f"api-key={random_val}"
        redacted_2, hits_2 = redact(text2)
        assert len(hits_2) == 1

    def test_redaction_entropy_quoted_value(self):
        """password=<quoted-high-entropy> also matched."""
        random_val = secrets.token_urlsafe(32)
        text = f'password="{random_val}"'
        redacted, hits = redact(text)
        assert len(hits) == 1


class TestNonSecretText:
    """Non-secret content should pass through unchanged."""

    def test_redaction_preserves_non_secret_text(self):
        """Text with no secrets comes back unchanged."""
        text = "hello world, no secrets here"
        redacted, hits = redact(text)
        assert redacted == text
        assert hits == []

    def test_redaction_multiple_paragraphs_no_secrets(self):
        """Multi-paragraph text without secrets unchanged."""
        text = """This is a normal document.

It talks about various things:
- First point
- Second point

And that's all."""
        redacted, hits = redact(text)
        assert redacted == text
        assert len(hits) == 0


class TestRedactionHitMetadata:
    """RedactionHit structure and preview field."""

    def test_redaction_preview_never_full_secret(self):
        """Preview is <= 7 chars (6 + ellipsis)."""
        token = secrets.token_urlsafe(40)
        text = f"sk-{token}"
        redacted, hits = redact(text)
        assert len(hits) == 1
        preview = hits[0].preview
        # Should be 6 chars + "…" = 7 chars max
        assert len(preview) <= 7
        # Should NOT contain the full token
        assert token not in preview

    def test_redaction_hit_start_end_offsets(self):
        """RedactionHit.start and .end mark the secret in original text."""
        text = "prefix sk-" + "X" * 40 + " suffix"
        redacted, hits = redact(text)
        assert len(hits) == 1
        hit = hits[0]
        # The secret starts at "sk-"
        original_secret = text[hit.start : hit.end]
        # Should contain "sk-" and part of the token
        assert original_secret.startswith("sk-")
        assert len(original_secret) >= 20


class TestRedactionLogging:
    """JSONL redaction log functionality."""

    def test_redaction_log_appends_jsonl(self, tmp_path):
        """Passing log_path appends one JSONL line per hit."""
        log_file = tmp_path / "redaction.log"
        token = secrets.token_urlsafe(40)
        text = f"key: sk-{token}"
        redacted, hits = redact(text, log_path=log_file)

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["schema_version"] == 1
        assert "ts" in entry
        assert entry["kind"] == "sk-api-key"
        assert entry["preview"] == hits[0].preview
        assert len(entry["preview"]) <= 7

    def test_redaction_log_creates_parent_dir(self, tmp_path):
        """log_path parent directories created if missing."""
        log_file = tmp_path / "a" / "b" / "c" / "redaction.log"
        token = secrets.token_urlsafe(40)
        text = f"sk-{token}"
        redact(text, log_path=log_file)

        assert log_file.exists()
        assert log_file.parent.exists()

    def test_redaction_multiple_hits_all_logged(self, tmp_path):
        """Text with multiple different secret kinds; all logged."""
        log_file = tmp_path / "redaction.log"
        sk_token = secrets.token_urlsafe(40)
        ghp_token = secrets.token_hex(18)
        aws_token = "".join(
            secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(16)
        )

        text = f"""
Here's a sk key: sk-{sk_token}
And a github token: ghp_{ghp_token}
AWS key: AKIA{aws_token}
"""
        redacted, hits = redact(text, log_path=log_file)
        assert len(hits) == 3

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3

        kinds = {json.loads(line)["kind"] for line in lines}
        assert kinds == {"sk-api-key", "ghp-token", "aws-access-key"}

    def test_redaction_log_ts_format(self, tmp_path):
        """Log timestamp is ISO 8601 format."""
        log_file = tmp_path / "redaction.log"
        token = secrets.token_urlsafe(40)
        redact(f"sk-{token}", log_path=log_file)

        entry = json.loads(log_file.read_text())
        ts = entry["ts"]
        # Should be parseable as ISO 8601
        assert "T" in ts
        assert "+" in ts or "Z" in ts  # timezone indicator


class TestNonOverlapping:
    """Patterns should not double-redact."""

    def test_redaction_single_marker_per_secret(self):
        """Each secret replaced once, not nested."""
        token = secrets.token_urlsafe(40)
        text = f"sk-{token}"
        redacted, hits = redact(text)
        assert redacted.count("[REDACTED") == 1
        # Should not have nested markers
        assert "[REDACTED:[REDACTED" not in redacted


class TestComplexScenarios:
    """Real-world mixed content."""

    def test_redaction_mixed_content(self):
        """Multi-secret message with surrounding legitimate text."""
        sk_token = secrets.token_urlsafe(40)
        ghp_token = secrets.token_hex(18)
        pem = """-----BEGIN PRIVATE KEY-----
abc123def456
-----END PRIVATE KEY-----"""

        text = f"""
User reported the following issue:

API Configuration:
  - OpenAI Key: sk-{sk_token}
  - GitHub Token: ghp_{ghp_token}

Private key stored below:
{pem}

No other details provided.
"""
        redacted, hits = redact(text)
        assert len(hits) == 3
        assert "User reported" in redacted
        assert "No other details" in redacted
        assert sk_token not in redacted
        assert "-----BEGIN PRIVATE KEY-----" not in redacted

    def test_redaction_no_false_positives_on_benign_similar(self):
        """Text that looks similar but isn't a secret doesn't get redacted."""
        text = "The sk- prefix is common in many projects."
        redacted, hits = redact(text)
        assert len(hits) == 0
        assert redacted == text
