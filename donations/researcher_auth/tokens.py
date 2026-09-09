"""Lookups for static researcher tokens.

The researcher token itself is only ever used to obtain a session (see
``sessions.py``); it is never accepted as API credentials directly.
"""
from django.utils import timezone

from donations.models import ResearcherToken


def resolve_researcher_token(raw_key):
    """Return the live ResearcherToken for a raw key, or None.

    Looks the key up by its stored hash and rejects it once its
    ``expires_at`` has passed.
    """
    key_hash = ResearcherToken.hash_key(raw_key)
    try:
        token = ResearcherToken.objects.get(key=key_hash)
    except ResearcherToken.DoesNotExist:
        return None
    if token.expires_at <= timezone.now():
        return None
    return token
