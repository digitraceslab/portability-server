"""Audit trail for access to donated data.

One log line per sensitive event, on the ``donations.audit`` logger, naming
the donation, the actor and the client address, never the data itself.
"""
import logging

logger = logging.getLogger('donations.audit')


def client_address(request):
    """Client IP as seen by nginx (X-Real-IP), falling back to REMOTE_ADDR."""
    return request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR') or '-'


def audit(event, request, donation=None, actor=None, **fields):
    """Record ``event`` for ``donation`` by ``actor`` with extra key=value fields."""
    parts = [f"event={event}"]
    if donation is not None:
        parts.append(f"donation={donation.pk}")
    parts.append(f"actor={actor or 'participant'}")
    parts.append(f"ip={client_address(request)}")
    parts.extend(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger.info(" ".join(parts))
