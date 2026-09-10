"""Formatter that masks token-shaped values in log lines.

Participant and donation tokens are UUIDs carried in URLs, so any log line
that quotes a request path (Django's request errors, for instance) would
otherwise record a usable credential.
"""
import logging
import re

UUID_RE = re.compile(
    r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'
)
REDACTED = '<token>'


def redact_tokens(text):
    """Replace every UUID in ``text`` with a placeholder."""
    return UUID_RE.sub(REDACTED, text)


class RedactingFormatter(logging.Formatter):
    """Standard formatter whose output has UUIDs masked."""

    def format(self, record):
        return redact_tokens(super().format(record))
