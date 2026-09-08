"""Tests for key management: credentials, local and OpenBao backends."""
import base64
import json
import os
import shutil
import tempfile
from unittest import mock

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from donations.testing import override_encryption_key
from donations.utils import crypto, parquet_store
from donations.utils.keystore import backend_for, get_backend, local_backend, reset_backend
from donations.utils.keystore.credentials import read_secret
from donations.utils.keystore.errors import KeystoreError
from donations.utils.keystore.local import LocalFernetBackend
from donations.utils.keystore.openbao import OpenBaoTransitBackend

ROLE_ID = "test-role-id"
SECRET_ID = "test-secret-id"


def _response(status_code, payload=None):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    return response


class ReadSecretTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="credstore-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def _write(self, name, value):
        with open(os.path.join(self.directory, f"portability.{name}"), "w") as handle:
            handle.write(value)

    def test_credentials_directory_takes_precedence_over_environment(self):
        self._write("encryption_key", "from-file\n")
        with mock.patch.dict(os.environ, {
            "CREDENTIALS_DIRECTORY": self.directory,
            "ENCRYPTION_KEY": "from-env",
        }):
            self.assertEqual(read_secret("encryption_key"), "from-file")

    def test_falls_back_to_environment_when_no_credentials_directory(self):
        with mock.patch.dict(os.environ, {"ENCRYPTION_KEY": "from-env"}, clear=False):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            self.assertEqual(read_secret("encryption_key"), "from-env")

    def test_none_when_configured_nowhere(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            os.environ.pop("ENCRYPTION_KEY", None)
            self.assertIsNone(read_secret("encryption_key"))


class LocalFernetBackendTests(SimpleTestCase):
    def setUp(self):
        self.backend = LocalFernetBackend(Fernet.generate_key())

    def test_wrap_unwrap_round_trip(self):
        key_bytes = os.urandom(32)
        wrapped = self.backend.wrap(key_bytes)
        self.assertEqual(self.backend.unwrap(wrapped), key_bytes)

    def test_text_round_trip(self):
        wrapped = self.backend.encrypt_text("secret value")
        self.assertEqual(self.backend.decrypt_text(wrapped), "secret value")

    def test_bytes_round_trip(self):
        wrapped = self.backend.encrypt_bytes(b"secret bytes")
        self.assertEqual(self.backend.decrypt_bytes(wrapped), b"secret bytes")


class OpenBaoTransitBackendTests(SimpleTestCase):
    def _backend(self):
        return OpenBaoTransitBackend(
            "https://vault.example.com", "transit", "portability", None, ROLE_ID, SECRET_ID,
        )

    @mock.patch("donations.utils.keystore.openbao.requests.post")
    def test_wrap_logs_in_then_encrypts(self, post):
        post.side_effect = [
            _response(200, {"auth": {"client_token": "tok-1"}}),
            _response(200, {"data": {"ciphertext": "vault:v1:abc"}}),
        ]
        backend = self._backend()
        self.assertEqual(backend.wrap(b"0" * 32), "vault:v1:abc")

        login_call, encrypt_call = post.call_args_list
        self.assertEqual(login_call.args[0], "https://vault.example.com/v1/auth/approle/login")
        self.assertEqual(login_call.kwargs["json"], {"role_id": ROLE_ID, "secret_id": SECRET_ID})
        self.assertEqual(
            encrypt_call.args[0], "https://vault.example.com/v1/transit/encrypt/portability"
        )
        self.assertEqual(encrypt_call.kwargs["headers"], {"X-Vault-Token": "tok-1"})
        sent_plaintext = base64.b64decode(encrypt_call.kwargs["json"]["plaintext"])
        self.assertEqual(sent_plaintext, b"0" * 32)

    @mock.patch("donations.utils.keystore.openbao.requests.post")
    def test_unwrap_returns_decoded_plaintext(self, post):
        key_bytes = os.urandom(32)
        post.side_effect = [
            _response(200, {"auth": {"client_token": "tok-1"}}),
            _response(200, {"data": {"plaintext": base64.b64encode(key_bytes).decode()}}),
        ]
        backend = self._backend()
        self.assertEqual(backend.unwrap("vault:v1:abc"), key_bytes)

    @mock.patch("donations.utils.keystore.openbao.requests.post")
    def test_relogs_in_once_on_403_then_succeeds(self, post):
        post.side_effect = [
            _response(200, {"auth": {"client_token": "tok-1"}}),
            _response(403),
            _response(200, {"auth": {"client_token": "tok-2"}}),
            _response(200, {"data": {"ciphertext": "vault:v1:abc"}}),
        ]
        backend = self._backend()
        self.assertEqual(backend.wrap(b"0" * 32), "vault:v1:abc")
        self.assertEqual(post.call_count, 4)
        retried_call = post.call_args_list[3]
        self.assertEqual(retried_call.kwargs["headers"], {"X-Vault-Token": "tok-2"})

    @mock.patch("donations.utils.keystore.openbao.requests.post")
    def test_server_error_raises_keystore_error(self, post):
        post.side_effect = [
            _response(200, {"auth": {"client_token": "tok-1"}}),
            _response(500),
        ]
        backend = self._backend()
        with self.assertRaises(KeystoreError):
            backend.wrap(b"0" * 32)

    @mock.patch("donations.utils.keystore.openbao.requests.post")
    def test_encrypt_bytes_envelope_round_trip(self, post):
        state = {}

        def fake_post(url, **kwargs):
            if url.endswith("/auth/approle/login"):
                return _response(200, {"auth": {"client_token": "tok-1"}})
            if "/encrypt/" in url:
                plaintext = kwargs["json"]["plaintext"]
                state["wrapped_key_bytes"] = base64.b64decode(plaintext)
                return _response(200, {"data": {"ciphertext": "vault:v1:wrapped-data-key"}})
            if "/decrypt/" in url:
                self.assertEqual(kwargs["json"]["ciphertext"], "vault:v1:wrapped-data-key")
                plaintext = base64.b64encode(state["wrapped_key_bytes"]).decode()
                return _response(200, {"data": {"plaintext": plaintext}})
            raise AssertionError(f"unexpected url {url}")

        post.side_effect = fake_post
        backend = self._backend()
        ciphertext = backend.encrypt_bytes(b"large plaintext payload")

        # Only the 32-byte Fernet data key crosses to the vault, never the
        # plaintext payload.
        for call in post.call_args_list:
            body = call.kwargs.get("json") or {}
            self.assertNotIn(b"large plaintext payload", json.dumps(body).encode())

        self.assertEqual(backend.decrypt_bytes(ciphertext), b"large plaintext payload")


class GetBackendTests(SimpleTestCase):
    def tearDown(self):
        reset_backend()

    @override_settings(OPENBAO_ADDR="")
    def test_local_backend_used_when_no_openbao_address(self):
        reset_backend()
        with override_encryption_key(Fernet.generate_key().decode()):
            self.assertIsInstance(get_backend(), LocalFernetBackend)

    @override_settings(OPENBAO_ADDR="")
    def test_improperly_configured_when_no_local_key(self):
        reset_backend()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            os.environ.pop("ENCRYPTION_KEY", None)
            with self.assertRaises(ImproperlyConfigured):
                get_backend()

    @override_settings(OPENBAO_ADDR="https://vault.example.com")
    def test_openbao_backend_used_when_address_is_set(self):
        reset_backend()
        with mock.patch.dict(os.environ, {
            "OPENBAO_ROLE_ID": ROLE_ID, "OPENBAO_SECRET_ID": SECRET_ID,
        }):
            self.assertIsInstance(get_backend(), OpenBaoTransitBackend)

    @override_settings(OPENBAO_ADDR="https://vault.example.com")
    def test_improperly_configured_when_approle_credentials_missing(self):
        reset_backend()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENBAO_ROLE_ID", None)
            os.environ.pop("OPENBAO_SECRET_ID", None)
            with self.assertRaises(ImproperlyConfigured):
                get_backend()


class BackendForTests(SimpleTestCase):
    def tearDown(self):
        reset_backend()

    @override_settings(OPENBAO_ADDR="https://vault.example.com")
    def test_vault_prefix_dispatches_to_openbao(self):
        reset_backend()
        with mock.patch.dict(os.environ, {
            "OPENBAO_ROLE_ID": ROLE_ID, "OPENBAO_SECRET_ID": SECRET_ID,
        }):
            self.assertIsInstance(backend_for("vault:v1:abc"), OpenBaoTransitBackend)

    @override_settings(OPENBAO_ADDR="")
    def test_fernet_token_dispatches_to_local(self):
        reset_backend()
        with override_encryption_key(Fernet.generate_key().decode()):
            token = get_backend().encrypt_text("hello")
            self.assertIsInstance(backend_for(token), LocalFernetBackend)

    @override_settings(OPENBAO_ADDR="https://vault.example.com")
    def test_legacy_fernet_ciphertext_decrypts_via_local_key_when_openbao_configured(self):
        reset_backend()
        key = Fernet.generate_key().decode()
        with mock.patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            legacy_ciphertext = LocalFernetBackend(key).encrypt_text("legacy value")
            self.assertEqual(local_backend(), local_backend())  # cached
            self.assertEqual(crypto.decrypt_text(legacy_ciphertext), "legacy value")


class ParquetKeystoreRoundTripTests(SimpleTestCase):
    """The Parquet KMS client only calls the keystore, never holds a key."""

    class _FakeBackend:
        def __init__(self, fernet):
            self._fernet = fernet

        def wrap(self, key_bytes):
            return self._fernet.encrypt(key_bytes).decode()

        def unwrap(self, wrapped):
            return self._fernet.decrypt(wrapped.encode())

    def test_round_trip_through_a_fake_backend(self):
        import pandas as pd

        backend = self._FakeBackend(Fernet(Fernet.generate_key()))
        workdir = tempfile.mkdtemp(prefix="parquet-keystore-")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        path = os.path.join(workdir, "data.parquet")
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=3, freq="h"),
            "value": [1, 2, 3],
        })

        with mock.patch("donations.utils.parquet_store.keystore.get_backend", return_value=backend), \
             mock.patch("donations.utils.parquet_store.keystore.backend_for", return_value=backend):
            parquet_store.write_frames(path, [frame])
            result = parquet_store.read_rows([path])

        pd.testing.assert_frame_equal(result, frame)
