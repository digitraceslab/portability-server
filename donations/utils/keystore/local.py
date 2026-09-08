"""Fernet-based keystore backend, used when no vault is configured.

The key is held in the application process, wherever it came from
(``donations.utils.keystore.credentials.read_secret``). This is the fallback
backend, not the recommended one; see ``donations.utils.keystore.get_backend``.
"""
from cryptography.fernet import Fernet


class LocalFernetBackend:
    """Wraps and encrypts using a single Fernet key held in this process."""

    name = "local"

    def __init__(self, key: str | bytes):
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    def wrap(self, key_bytes: bytes) -> str:
        return self._fernet.encrypt(key_bytes).decode()

    def unwrap(self, wrapped: str) -> bytes:
        return self._fernet.decrypt(wrapped.encode())

    def encrypt_text(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt_text(self, text: str) -> str:
        return self._fernet.decrypt(text.encode()).decode()

    def encrypt_bytes(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, data: bytes) -> bytes:
        return self._fernet.decrypt(data)
