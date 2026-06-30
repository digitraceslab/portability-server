"""Custom security middleware for portability-server."""

from django.conf import settings


class CrossOriginResourcePolicyMiddleware:
    """Set Cross-Origin-Resource-Policy: same-origin on all responses.

    Django has no built-in setting for this header (unlike COOP), so it is
    added here. Skipped under DEBUG to match the project's hardening pattern.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not settings.DEBUG:
            response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response
