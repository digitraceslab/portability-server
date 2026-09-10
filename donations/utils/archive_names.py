"""Generates storage filenames for uploaded archives that carry no trace of the original filename."""
import os
import re
import secrets

_SAFE_EXTENSION_RE = re.compile(r'^\.[a-z0-9]{1,5}$')


def generated_archive_name(donation_pk, original_name):
    """Build a random storage filename for an uploaded archive.

    Keeps only the donation's primary key and a lower-cased extension from
    ``original_name`` (when it looks like a normal extension); nothing else
    from the original name is retained.
    """
    _, ext = os.path.splitext(original_name or '')
    ext = ext.lower()
    if not _SAFE_EXTENSION_RE.match(ext):
        ext = ''
    return f"{donation_pk}_{secrets.token_hex(8)}{ext}"
