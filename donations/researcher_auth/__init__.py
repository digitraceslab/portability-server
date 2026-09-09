"""Researcher authentication: token exchange and session lifecycle.

This package holds the logic behind researcher API authentication, so that
``donations.authentication`` and the session views in ``donations.api`` stay
short orchestration code. See ``tokens.py`` for researcher token lookups and
``sessions.py`` for session issuing, resolving, and cleanup.
"""
