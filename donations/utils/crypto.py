from django.conf import settings
import os
import base64
import hashlib
import tempfile
from cryptography.fernet import Fernet

def _resolve_key():
    key = getattr(settings, 'ENCRYPTION_KEY', None)
    if key:
        if isinstance(key, str):
            try:
                key_b = key.encode()
                # assume already base64 urlsafe encoded
                return key_b
            except Exception:
                pass
        return key
    # Fallback: derive from SECRET_KEY (not ideal for production)
    secret = getattr(settings, 'SECRET_KEY', None) or ''
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet():
    key = _resolve_key()
    return Fernet(key)


def encrypt_bytes(data: bytes) -> bytes:
    f = _get_fernet()
    return f.encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    f = _get_fernet()
    return f.decrypt(data)


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
    return encrypt_bytes(text.encode()).decode()


def decrypt_text(text: str) -> str:
    return decrypt_bytes(text.encode()).decode()
