"""OpenBao transit engine backend: wraps keys without ever holding them.

The application authenticates with an AppRole, then calls the transit
engine's encrypt/decrypt endpoints for the configured key. Only small values
(data keys, OAuth token strings) are sent; donated data itself never is.
"""
import base64

import requests
from cryptography.fernet import Fernet

from .errors import KeystoreError


class OpenBaoTransitBackend:
    """Wraps/unwraps and encrypts/decrypts via an OpenBao transit key."""

    name = "openbao"

    def __init__(self, addr, mount, key_name, ca_cert, role_id, secret_id, timeout=10):
        self._addr = addr.rstrip("/")
        self._mount = mount.strip("/")
        self._key_name = key_name
        self._role_id = role_id
        self._secret_id = secret_id
        self._timeout = timeout
        self._verify = ca_cert if ca_cert else True
        self._token = None

    def _login(self):
        response = requests.post(
            f"{self._addr}/v1/auth/approle/login",
            json={"role_id": self._role_id, "secret_id": self._secret_id},
            timeout=self._timeout,
            verify=self._verify,
        )
        if response.status_code != 200:
            raise KeystoreError(f"OpenBao login failed with status {response.status_code}")
        self._token = response.json()["auth"]["client_token"]

    def _call(self, path, payload):
        if self._token is None:
            self._login()
        response = self._request(path, payload)
        if response.status_code == 403:
            self._login()
            response = self._request(path, payload)
        if response.status_code != 200:
            raise KeystoreError(
                f"OpenBao request to {path} failed with status {response.status_code}"
            )
        return response.json()

    def _request(self, path, payload):
        try:
            return requests.post(
                f"{self._addr}/v1/{path}",
                headers={"X-Vault-Token": self._token},
                json=payload,
                timeout=self._timeout,
                verify=self._verify,
            )
        except requests.RequestException as exc:
            raise KeystoreError(f"OpenBao request to {path} failed") from exc

    def wrap(self, key_bytes: bytes) -> str:
        plaintext = base64.b64encode(key_bytes).decode()
        data = self._call(f"{self._mount}/encrypt/{self._key_name}", {"plaintext": plaintext})
        return data["data"]["ciphertext"]

    def unwrap(self, wrapped: str) -> bytes:
        data = self._call(f"{self._mount}/decrypt/{self._key_name}", {"ciphertext": wrapped})
        return base64.b64decode(data["data"]["plaintext"])

    def encrypt_text(self, text: str) -> str:
        return self.wrap(text.encode())

    def decrypt_text(self, text: str) -> str:
        return self.unwrap(text).decode()

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Envelope-encrypt: a local Fernet key, itself wrapped via transit.

        Large blobs must not be sent to the vault, so only the per-call key
        is; the header before the newline is that wrapped key.
        """
        data_key = Fernet.generate_key()
        ciphertext = Fernet(data_key).encrypt(data)
        wrapped_key = self.wrap(data_key)
        return wrapped_key.encode() + b"\n" + ciphertext

    def decrypt_bytes(self, data: bytes) -> bytes:
        wrapped_key, _, ciphertext = data.partition(b"\n")
        data_key = self.unwrap(wrapped_key.decode())
        return Fernet(data_key).decrypt(ciphertext)
