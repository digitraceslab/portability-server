"""Key management: an OpenBao transit vault, or a local key as fallback.

Callers use ``get_backend()`` to encrypt/wrap new data and ``backend_for()``
to decrypt/unwrap existing ciphertext, which may have been written by a
different backend during a migration.
"""
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .credentials import read_secret
from .errors import KeystoreError
from .local import LocalFernetBackend
from .openbao import OpenBaoTransitBackend

logger = logging.getLogger(__name__)

OPENBAO_PREFIX = "vault:"

_backend = None
_local_backend = None
_local_backend_loaded = False


def reset_backend():
    """Drop cached backends, so the next call re-reads configuration.

    Used by tests that switch between backends or keys.
    """
    global _backend, _local_backend, _local_backend_loaded
    _backend = None
    _local_backend = None
    _local_backend_loaded = False


def get_backend():
    """Return the configured backend, creating it on first use."""
    global _backend
    if _backend is None:
        _backend = _build_backend()
    return _backend


def _build_backend():
    if settings.OPENBAO_ADDR:
        role_id = read_secret("openbao_role_id")
        if role_id is None:
            raise ImproperlyConfigured(
                "OpenBao AppRole role id is not configured. Provide it in "
                "/etc/credstore/portability.openbao_role_id."
            )
        secret_id = read_secret("openbao_secret_id")
        if secret_id is None:
            raise ImproperlyConfigured(
                "OpenBao AppRole secret id is not configured. Provide it in "
                "/etc/credstore/portability.openbao_secret_id."
            )
        return OpenBaoTransitBackend(
            settings.OPENBAO_ADDR,
            settings.OPENBAO_MOUNT,
            settings.OPENBAO_KEY_NAME,
            settings.OPENBAO_CACERT or None,
            role_id,
            secret_id,
        )
    key = read_secret("encryption_key")
    if key is None:
        raise ImproperlyConfigured(
            "No encryption key is configured. Provide one in "
            "/etc/credstore/portability.encryption_key, or set ENCRYPTION_KEY "
            "for development. Generate one with: python -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key()."
            "decode())\""
        )
    logger.warning(
        "Encryption key is held locally by the application; no OpenBao "
        "vault is configured (OPENBAO_ADDR is empty)."
    )
    return LocalFernetBackend(key)


def local_backend():
    """The local backend for legacy ciphertext, if a local key is present.

    Distinct from ``get_backend()``: this is used to read data that predates
    an OpenBao migration, regardless of which backend is now configured.
    """
    global _local_backend, _local_backend_loaded
    if not _local_backend_loaded:
        key = read_secret("encryption_key")
        _local_backend = LocalFernetBackend(key) if key is not None else None
        _local_backend_loaded = True
    return _local_backend


def backend_for(ciphertext):
    """Return the backend that can decrypt/unwrap this ciphertext."""
    if isinstance(ciphertext, bytes):
        prefix = ciphertext.split(b"\n", 1)[0].decode(errors="replace")
    else:
        prefix = ciphertext
    if prefix.startswith(OPENBAO_PREFIX):
        backend = get_backend()
        if not isinstance(backend, OpenBaoTransitBackend):
            raise KeystoreError(
                "ciphertext was wrapped by OpenBao, but no OpenBao vault is configured"
            )
        return backend
    backend = local_backend()
    if backend is None:
        raise KeystoreError(
            "ciphertext was written with a local key that is no longer configured"
        )
    return backend
