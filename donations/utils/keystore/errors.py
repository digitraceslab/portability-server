"""Errors raised by keystore backends.

Kept separate from ``__init__.py`` so backend modules can raise them without
importing the package's backend-selection logic (which imports the backends).
"""


class KeystoreError(Exception):
    """A keystore backend could not wrap, unwrap, encrypt or decrypt.

    Messages must never include plaintext or auth tokens.
    """
