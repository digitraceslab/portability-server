"""Tests for the Google archive download URL allow-list."""
from django.test import SimpleTestCase

from donations.utils.download_urls import is_allowed_google_download_url


class IsAllowedGoogleDownloadUrlTests(SimpleTestCase):
    def test_accepts_googleusercontent_subdomain(self):
        self.assertTrue(
            is_allowed_google_download_url('https://storage.googleusercontent.com/x')
        )

    def test_accepts_google_com(self):
        self.assertTrue(is_allowed_google_download_url('https://google.com/x'))

    def test_rejects_http_scheme(self):
        self.assertFalse(is_allowed_google_download_url('http://google.com/x'))

    def test_rejects_disguised_host_as_path(self):
        self.assertFalse(is_allowed_google_download_url('https://evil.com/google.com'))

    def test_rejects_host_suffix_lookalike(self):
        self.assertFalse(is_allowed_google_download_url('https://google.com.evil.com/'))

    def test_rejects_userinfo(self):
        self.assertFalse(is_allowed_google_download_url('https://user@google.com/'))

    def test_rejects_unexpected_port(self):
        self.assertFalse(is_allowed_google_download_url('https://google.com:8443/'))

    def test_rejects_ftp_scheme(self):
        self.assertFalse(is_allowed_google_download_url('ftp://google.com/x'))

    def test_rejects_empty_string(self):
        self.assertFalse(is_allowed_google_download_url(''))

    def test_rejects_non_url_string(self):
        self.assertFalse(is_allowed_google_download_url('not a url'))
