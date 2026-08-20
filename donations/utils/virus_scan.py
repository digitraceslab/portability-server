"""Virus scanning utilities using clamdscan for files ingested from participants."""
import logging
import os
import subprocess
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)


def scan_path(path):
    """Scan a file with clamdscan. Returns (clean, detail)."""
    if not settings.CLAMAV_ENABLED:
        return True, "scanning disabled"
    try:
        result = subprocess.run(
            ["clamdscan", "--fdpass", "--no-summary", path],
            capture_output=True,
            text=True,
        )
    except (OSError, FileNotFoundError) as e:
        logger.error(f"Virus scanner unavailable: {e}")
        return False, "virus scanner unavailable"

    if result.returncode == 0:
        return True, "clean"
    elif result.returncode == 1:
        return False, result.stdout.strip()
    else:
        logger.error(f"clamdscan failed unexpectedly: {result.stderr.strip()}")
        return False, "virus scanner unavailable"


def scan_bytes(data):
    """Scan an in-memory bytes payload. Returns (clean, detail)."""
    if not settings.CLAMAV_ENABLED:
        return True, "scanning disabled"
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return scan_path(tmp.name)
    finally:
        os.remove(tmp.name)
