"""Bound on how large a zip member may be before the archive is opened.

The readers load a whole member into memory, so a member larger than the
worker can hold must be refused up front. Python's ``zipfile`` never returns
more bytes than a member's declared size, so the declared sizes are a
reliable bound.
"""
import os
import zipfile

from django.conf import settings


def physical_memory_bytes():
    """Total physical memory of this host in bytes (0 if unknown)."""
    try:
        return os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (ValueError, OSError, AttributeError):
        return 0


def check_archive_bounds(path):
    """Return ``(ok, detail)``: refuse a zip whose largest member exceeds
    ``ARCHIVE_MAX_MEMBER_BYTES`` or whose member count exceeds
    ``ARCHIVE_MAX_MEMBERS``. Non-zip files are accepted unchanged."""
    if not zipfile.is_zipfile(path):
        return True, "not a zip archive"
    limit = settings.ARCHIVE_MAX_MEMBER_BYTES
    max_members = settings.ARCHIVE_MAX_MEMBERS
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            largest = max((info.file_size for info in members), default=0)
    except (zipfile.BadZipFile, OSError) as exc:
        return False, f"unreadable archive: {exc}"
    if len(members) > max_members:
        return False, f"{len(members)} members exceeds limit of {max_members}"
    if largest > limit:
        return False, f"member of {largest} bytes exceeds limit of {limit} bytes"
    return True, "within limits"
