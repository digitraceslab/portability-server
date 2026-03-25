from rest_framework import authentication, exceptions

from donations.models import ResearcherToken


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
        key_hash = ResearcherToken.hash_key(key)
        try:
            token = ResearcherToken.objects.get(key=key_hash)
        except ResearcherToken.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid token.')
        return (None, token)
