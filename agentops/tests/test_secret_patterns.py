"""Secret detection and redaction must know the same shapes.

Two layers screen credentials: `security.scan_text()` reports them, and
`logging.redact_text()` removes them before text is persisted. They read one
table (`secret_patterns.SECRET_PATTERNS`), and these tests pin that every
family is caught by both -- a token the detector knows but the redactor does
not would leak straight into a log.

Every value below is an obvious placeholder. Never put a realistic credential
in a fixture.
"""

import unittest

from agentops.logging import redact_text
from agentops.secret_patterns import REDACTED, SECRET_PATTERNS, redact_secrets
from agentops.security import _SECRET_PATTERNS, scan_text

#: (label, sample line, the placeholder value that must disappear, pattern name)
TOKEN_FAMILIES = (
    (
        "anthropic",
        "ANTHROPIC_KEY=sk-ant-api03-FAKEfakeFAKEfake0123456789",
        "sk-ant-api03-FAKEfakeFAKEfake0123456789",
        "anthropic-api-key",
    ),
    (
        "stripe-secret-live",
        "export STRIPE=sk_live_FAKEFAKEFAKEFAKE1234",
        "sk_live_FAKEFAKEFAKEFAKE1234",
        "stripe-key",
    ),
    (
        "stripe-restricted-live",
        "export STRIPE=rk_live_FAKEFAKEFAKEFAKE1234",
        "rk_live_FAKEFAKEFAKEFAKE1234",
        "stripe-key",
    ),
    (
        "stripe-publishable-live",
        "export STRIPE=pk_live_FAKEFAKEFAKEFAKE1234",
        "pk_live_FAKEFAKEFAKEFAKE1234",
        "stripe-key",
    ),
    (
        "google-api-key",
        "const key = 'AIzaFAKEfake1234567890_-FAKEfake123456_';",
        "AIzaFAKEfake1234567890_-FAKEfake123456_",
        "google-api-key",
    ),
    (
        "github-fine-grained-pat",
        "token github_pat_FAKEfake1234567890_FAKEfake1234567890",
        "github_pat_FAKEfake1234567890_FAKEfake1234567890",
        "github-fine-grained-pat",
    ),
    (
        "jwt",
        "session=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKESIGNATUREfake0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKESIGNATUREfake0123456789",
        "jwt",
    ),
    (
        "aws-access-key-id",
        "aws AKIAFAKEFAKEFAKE1234",
        "AKIAFAKEFAKEFAKE1234",
        "aws-access-key-id",
    ),
    (
        "slack",
        "posting with xoxb-1234567890-abcdefghij",
        "xoxb-1234567890-abcdefghij",
        "slack-token",
    ),
    (
        "private-key-header",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "private-key-header",
    ),
    (
        "bearer",
        "Authorization: Bearer FAKEbearerValue0123456789",
        "FAKEbearerValue0123456789",
        "bearer-token",
    ),
    (
        "connection-string",
        "Server=db;User=app;Password=FAKEpass123;Database=app",
        "FAKEpass123",
        "connection-string-secret",
    ),
)

JSON_FORMS = (
    (
        "json-api-key",
        '  "api_key": "FAKEvalue1234",',
        "FAKEvalue1234",
    ),
    (
        "json-password",
        '{"password": "FAKEpass1234"}',
        "FAKEpass1234",
    ),
    (
        "json-client-secret",
        '{"client_secret": "FAKEfakeVALUE123"}',
        "FAKEfakeVALUE123",
    ),
    (
        "json-authorization-bearer",
        '  "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKESIG12",',
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKESIG12",
    ),
    (
        "json-token",
        '{"token": "FAKEtokenVALUE123"}',
        "FAKEtokenVALUE123",
    ),
)


def _patterns_found(text: str) -> set[str]:
    return {finding.pattern for finding in scan_text(text, "test")}


class DetectorCoversTokenFamilies(unittest.TestCase):
    def test_every_family_is_detected(self):
        for label, sample, _, expected in TOKEN_FAMILIES:
            with self.subTest(family=label):
                self.assertIn(expected, _patterns_found(sample))

    def test_json_forms_are_detected(self):
        for label, sample, _ in JSON_FORMS:
            with self.subTest(form=label):
                self.assertTrue(_patterns_found(sample), f"no finding for {sample!r}")

    def test_json_authorization_is_detected_as_bearer(self):
        sample = next(
            text for label, text, _ in JSON_FORMS if label == "json-authorization-bearer"
        )
        self.assertIn("bearer-token", _patterns_found(sample))

    def test_github_classic_prefixes_are_detected(self):
        for prefix in ("ghp", "gho", "ghu", "ghs", "ghr"):
            with self.subTest(prefix=prefix):
                token = f"{prefix}_FAKEfake0123456789abcd"
                self.assertIn("github-token", _patterns_found(f"token {token}"))

    def test_slack_prefixes_are_detected(self):
        for prefix in ("xoxb", "xoxa", "xoxp", "xoxr", "xoxs"):
            with self.subTest(prefix=prefix):
                token = f"{prefix}-1234567890-abcdefghij"
                self.assertIn("slack-token", _patterns_found(f"token {token}"))

    def test_findings_never_carry_the_value(self):
        for label, sample, value, _ in TOKEN_FAMILIES:
            with self.subTest(family=label):
                rendered = " ".join(
                    finding.describe() for finding in scan_text(sample, "test")
                )
                self.assertNotIn(value, rendered)


class RedactorCoversTokenFamilies(unittest.TestCase):
    def test_every_family_is_redacted(self):
        for label, sample, value, _ in TOKEN_FAMILIES:
            with self.subTest(family=label):
                redacted = redact_text(sample)
                self.assertNotIn(value, redacted)
                self.assertIn(REDACTED, redacted)

    def test_json_forms_are_redacted(self):
        for label, sample, value in JSON_FORMS:
            with self.subTest(form=label):
                redacted = redact_text(sample)
                self.assertNotIn(value, redacted)
                self.assertIn(REDACTED, redacted)

    def test_redaction_keeps_the_assignment_context(self):
        self.assertEqual("API_KEY=[REDACTED]", redact_text("API_KEY=FAKEvalue1234"))
        self.assertEqual(
            '  "api_key": "[REDACTED]",', redact_text('  "api_key": "FAKEvalue1234",')
        )


class LayersShareOneTable(unittest.TestCase):
    """The detector and the redactor must not drift apart again."""

    def test_detector_patterns_come_from_the_shared_table(self):
        self.assertEqual(
            [spec.name for spec in SECRET_PATTERNS],
            [name for name, _ in _SECRET_PATTERNS],
        )
        for spec, (_, regex) in zip(SECRET_PATTERNS, _SECRET_PATTERNS, strict=False):
            self.assertIs(spec.regex, regex)

    def test_every_detected_family_is_also_redacted(self):
        for label, sample, value, _ in TOKEN_FAMILIES:
            with self.subTest(family=label):
                self.assertTrue(_patterns_found(sample))
                self.assertNotIn(value, redact_secrets(sample))


class FalsePositiveGuards(unittest.TestCase):
    """Ordinary prose must survive redaction and stay unflagged."""

    PROSE = (
        "I am the bearer of bad news.",
        "The bearer of this letter is a friend.",
        "The password column is documented; tokens rotate on demand.",
        "Set the secret in your shell, then run the tests.",
        "Server=db;User=app;Database=app",
        "sk-learn notebooks describe hyphenated experiments",
    )

    #: Log lines that name a credential key but carry a count or a model name.
    #: Redacting these hides the thing the log is reporting, and in the
    #: security gate a false positive can block a benign diff.
    BENIGN_ASSIGNMENTS = (
        "token: 4096",
        "token = GPT-4",
        "max_tokens: 8192",
        "token_count 1234",
    )

    def test_prose_is_left_alone(self):
        for text in self.PROSE:
            with self.subTest(text=text):
                self.assertEqual(text, redact_text(text))

    def test_prose_is_not_flagged(self):
        for text in self.PROSE:
            with self.subTest(text=text):
                self.assertEqual([], scan_text(text, "test"))

    def test_benign_assignments_are_left_alone(self):
        for text in self.BENIGN_ASSIGNMENTS:
            with self.subTest(text=text):
                self.assertEqual(text, redact_text(text))

    def test_benign_assignments_are_not_flagged(self):
        for text in self.BENIGN_ASSIGNMENTS:
            with self.subTest(text=text):
                self.assertEqual([], scan_text(text, "test"))

    def test_bearer_length_floor(self):
        self.assertEqual("Bearer short", redact_text("Bearer short"))
        self.assertIn(REDACTED, redact_text("Bearer FAKEbearerValue01234"))


class AssignmentValueThreshold(unittest.TestCase):
    """An assigned value must look like a credential before it is replaced.

    Only the bare ``token`` key is ambiguous in ordinary logs, so only it
    needs a credential-like value. A value assigned to a named credential key
    (password, api_key, secret, ...) is a credential however short it is.
    """

    def test_long_values_are_redacted(self):
        self.assertEqual("token: [REDACTED]", redact_text("token: abcdefghijkl"))
        self.assertEqual("API_KEY=[REDACTED]", redact_text("API_KEY=FAKEvalue1234"))

    def test_short_values_with_a_symbol_are_redacted(self):
        self.assertEqual("api_key=[REDACTED]", redact_text("api_key=abcd1234!"))
        self.assertIn(REDACTED, redact_text("token: a-b-c-d-e"))

    def test_short_alphanumeric_token_values_are_left_alone(self):
        for text in ("token: 4096", "token = GPT-4", "max_token=4096"):
            with self.subTest(text=text):
                self.assertEqual(text, redact_text(text))

    def test_named_credential_keys_keep_the_low_floor(self):
        # e4ef50b applied the token threshold to every key, so plain
        # passwords and short keys under 12 characters slipped through the
        # security gate. These must stay redacted and flagged.
        cases = (
            ("password: hunter2", "password: [REDACTED]"),
            ("password: Summer2026", "password: [REDACTED]"),
            ("API_KEY=abcd1234ef", "API_KEY=[REDACTED]"),
            ('"secret": "Tr0ub4dor9"', '"secret": "[REDACTED]"'),
            ("client_secret: 9f8e7d6c5b", "client_secret: [REDACTED]"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, redact_text(text))
                self.assertIn("credential-assignment", _patterns_found(text))

    def test_prefixed_env_names_are_caught(self):
        for text in ("DB_PASSWORD=letmein1", "OPENAI_API_KEY=sk-abc123xyz"):
            with self.subTest(text=text):
                self.assertIn(REDACTED, redact_text(text))

    def test_words_containing_a_key_are_left_alone(self):
        for text in ("secretary: Jane", "max_tokens: 8192"):
            with self.subTest(text=text):
                self.assertEqual(text, redact_text(text))

    def test_assignment_family_is_still_reported(self):
        found = _patterns_found('{"token": "FAKEtokenVALUE123"}')
        self.assertIn("credential-assignment", found)


if __name__ == "__main__":
    unittest.main()
