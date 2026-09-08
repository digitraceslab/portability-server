"""Encryption facade used by callers throughout the app.

Delegates to the configured keystore backend (OpenBao transit, or a local
key): encrypt/write use ``get_backend()``, decrypt/read dispatch by
ciphertext prefix via ``backend_for()``, so data written before a backend
change stays readable. See ``donations.utils.keystore``.
"""
import os
import tempfile

from donations.utils import keystore


def encrypt_bytes(data: bytes) -> bytes:
    return keystore.get_backend().encrypt_bytes(data)


def decrypt_bytes(data: bytes) -> bytes:
    return keystore.backend_for(data).decrypt_bytes(data)


def encrypt_file_inplace(path: str):
    with open(path, 'rb') as fh:
        plaintext = fh.read()
    encrypted = encrypt_bytes(plaintext)
    with open(path, 'wb') as fh:
        fh.write(encrypted)


def write_encrypted_bytes(path: str, data: bytes):
    encrypted = encrypt_bytes(data)
    dirpath = os.path.dirname(path)
    if dirpath and not os.path.exists(dirpath):
        os.makedirs(dirpath, exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(encrypted)


def decrypt_file_to_temp(path: str) -> str:
    with open(path, 'rb') as fh:
        encrypted = fh.read()
    plaintext = decrypt_bytes(encrypted)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.write(plaintext)
    tmp.flush()
    tmp.close()
    return tmp.name


def encrypt_text(text: str) -> str:
    return keystore.get_backend().encrypt_text(text)


def decrypt_text(text: str) -> str:
    return keystore.backend_for(text).decrypt_text(text)
