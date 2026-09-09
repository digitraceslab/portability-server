"""DRF authentication backend for the researcher API.

Accepts only session keys (see ``donations.researcher_auth.sessions``); a
static researcher token in the header is rejected. Sessions are obtained by
exchanging a researcher token at ``POST /api/session/``.
"""
from rest_framework import authentication, exceptions

from donations.researcher_auth.sessions import resolve_session


class ResearcherTokenAuthentication(authentication.BaseAuthentication):
    keyword = 'Token'

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).split()
        if not auth_header or auth_header[0].lower() != self.keyword.lower().encode():
            return None
        if len(auth_header) != 2:
            raise exceptions.AuthenticationFailed('Invalid token header.')
        try:
            token_key = auth_header[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed('Invalid token header.')
        return self.authenticate_credentials(token_key)

    def authenticate_credentials(self, key):
        session = resolve_session(key)
        if session is None:
            raise exceptions.AuthenticationFailed('Invalid token.')
        return (None, session.token)
