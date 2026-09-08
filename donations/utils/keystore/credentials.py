"""Read secrets from systemd credentials, falling back to the environment.

Secrets (OpenBao AppRole ids, or the local encryption key) are never settings
values: they are handed to the process by systemd (``CREDENTIALS_DIRECTORY``)
in production, or come from the environment in development and tests.
"""
import os


def read_secret(name: str) -> str | None:
    """Return the named secret, or None if it is configured nowhere.

    Looks for ``$CREDENTIALS_DIRECTORY/portability.<name>`` first (the file
    systemd's ``LoadCredential``/``ImportCredential`` delivers), then the
    environment variable ``<NAME>`` upper-cased (populated from ``.env`` by
    django-environ, which covers development and tests).
    """
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if directory:
        path = os.path.join(directory, f"portability.{name}")
        if os.path.exists(path):
            with open(path, "r") as handle:
                return handle.read().rstrip("\n")
    return os.environ.get(name.upper())
