"""Test helper for setting the local encryption key.
"""
import os
from unittest import mock

from django.test.utils import TestContextDecorator

from donations.utils import keystore


class override_encryption_key(TestContextDecorator):
    """Set ``ENCRYPTION_KEY`` in the environment for the duration of a test.

    Usable as a class decorator, function decorator, or context manager,
    like ``django.test.override_settings``.
    """

    def __init__(self, key):
        self._key = key
        self._patcher = None
        super().__init__()

    def enable(self):
        self._patcher = mock.patch.dict(os.environ, {"ENCRYPTION_KEY": self._key})
        self._patcher.start()
        keystore.reset_backend()

    def disable(self):
        self._patcher.stop()
        keystore.reset_backend()
