"""Allow-list validation for outbound archive download URLs.

Used to ensure archive downloads only ever fetch from expected Google
hostnames, guarding against unexpected redirects or tampered URLs.
"""
from urllib.parse import urlsplit

ALLOWED_DOWNLOAD_DOMAINS = ('google.com', 'googleapis.com', 'googleusercontent.com')


def is_allowed_google_download_url(url):
    """Return True if `url` is a safe https URL on an allowed Google domain.

    Requires the https scheme, no explicit port other than 443, no userinfo,
    and a hostname equal to or a subdomain of one of the allowed domains.
    """
    try:
        parts = urlsplit(url)

        if parts.scheme != 'https':
            return False

        if '@' in parts.netloc:
            return False

        if parts.port is not None and parts.port != 443:
            return False

        host = parts.hostname
        if not host:
            return False

        return any(
            host == domain or host.endswith('.' + domain)
            for domain in ALLOWED_DOWNLOAD_DOMAINS
        )
    except ValueError:
        return False
