"""Tests for the token-redacting log formatter."""
import logging

from django.test import SimpleTestCase

from portability_server.logging.redaction import RedactingFormatter, redact_tokens


class RedactionTests(SimpleTestCase):
    def test_uuid_is_masked(self):
        line = "Not Found: /donate/123e4567-e89b-12d3-a456-426614174000/"
        self.assertEqual(redact_tokens(line), "Not Found: /donate/<token>/")

    def test_other_text_is_untouched(self):
        self.assertEqual(redact_tokens("donation 12 processed"), "donation 12 processed")

    def test_formatter_masks_message_and_args(self):
        record = logging.LogRecord(
            "donations", logging.ERROR, __file__, 1,
            "path %s failed", ("/participant/123e4567-e89b-12d3-a456-426614174000/",), None,
        )
        out = RedactingFormatter("%(levelname)s %(message)s").format(record)
        self.assertEqual(out, "ERROR path /participant/<token>/ failed")
