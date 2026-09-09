"""Session issuing, resolving, and cleanup for researcher API access.

A session is a short-lived credential exchanged for a researcher's static
token (see ``tokens.py``). All ``/api/`` endpoints other than the exchange
itself authenticate with a session key, not the researcher token.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import get_random_string

from donations.models import ResearcherSession


def create_session(token):
    """Issue a new session for a researcher token.

    Returns ``(raw_key, session)``. The raw key is only available here; the
    session stores its hash. The session's ``expires_at`` is
    ``RESEARCHER_SESSION_LIFETIME_SECONDS`` from now, capped at the
    researcher token's own ``expires_at``.
    """
    raw_key = get_random_string(40)
    lifetime = timedelta(seconds=settings.RESEARCHER_SESSION_LIFETIME_SECONDS)
    expires_at = min(timezone.now() + lifetime, token.expires_at)
    session = ResearcherSession.objects.create(
        key=ResearcherSession.hash_key(raw_key),
        token=token,
        expires_at=expires_at,
    )
    return raw_key, session


def resolve_session(raw_key):
    """Return the live ResearcherSession for a raw key, or None.

    Looks the key up by its stored hash and rejects it once its
    ``expires_at`` has passed.
    """
    key_hash = ResearcherSession.hash_key(raw_key)
    try:
        session = ResearcherSession.objects.get(key=key_hash)
    except ResearcherSession.DoesNotExist:
        return None
    if session.expires_at <= timezone.now():
        return None
    return session


def revoke_session(session):
    """Delete a session, ending it immediately."""
    session.delete()


def purge_expired_sessions():
    """Delete all sessions past their expiry. Returns the number removed."""
    expired = ResearcherSession.objects.filter(expires_at__lte=timezone.now())
    count = expired.count()
    expired.delete()
    return count
