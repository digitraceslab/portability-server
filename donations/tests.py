"""Tests for donations app functionality."""
import os
import tempfile
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory
from rest_framework.test import APIRequestFactory

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken
from donations.authentication import ResearcherTokenAuthentication
from donations.utils.crypto import (
    encrypt_text, decrypt_text, encrypt_bytes, decrypt_bytes,
    write_encrypted_bytes,
)


class CryptoTests(TestCase):
    """Tests for encryption and decryption utilities."""
    def test_text_roundtrip(self):
        original = "secret oauth token value"
        encrypted = encrypt_text(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(decrypt_text(encrypted), original)

    def test_bytes_roundtrip(self):
        original = b"binary data here"
        encrypted = encrypt_bytes(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(decrypt_bytes(encrypted), original)

    def test_empty_string(self):
        self.assertEqual(decrypt_text(encrypt_text("")), "")


class DonationModelTests(TestCase):
    """Tests for Donation model behavior."""
    def test_create_donation(self):
        donation = Donation.objects.create(source_type='google_portability')
        self.assertEqual(donation.status, 'pending')
        self.assertIsNotNone(donation.participant_token)
        self.assertIsNotNone(donation.researcher_token)
        self.assertNotEqual(donation.participant_token, donation.researcher_token)

    def test_unique_tokens(self):
        d1 = Donation.objects.create(source_type='google_portability')
        d2 = Donation.objects.create(source_type='tiktok_portability')
        self.assertNotEqual(d1.participant_token, d2.participant_token)
        self.assertNotEqual(d1.researcher_token, d2.researcher_token)


class ResearcherTokenModelTests(TestCase):
    """Tests for ResearcherToken model behavior."""
    def test_auto_generates_key(self):
        token = ResearcherToken.objects.create(permission='add_user', name='test')
        self.assertEqual(len(token.key), 40)

    def test_permission_choices(self):
        t1 = ResearcherToken.objects.create(permission='add_user')
        t2 = ResearcherToken.objects.create(permission='read_data')
        self.assertEqual(t1.permission, 'add_user')
        self.assertEqual(t2.permission, 'read_data')


class ResearcherTokenAuthTests(TestCase):
    """Tests for researcher token authentication."""
    def setUp(self):
        self.auth = ResearcherTokenAuthentication()
        self.factory = APIRequestFactory()
        self.token = ResearcherToken.objects.create(
            permission='add_user', name='test-auth'
        )

    def test_valid_token(self):
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Token {self.token.key}')
        user, auth_token = self.auth.authenticate(request)
        self.assertIsNone(user)
        self.assertEqual(auth_token.permission, 'add_user')
        self.assertEqual(auth_token.key, self.token.key)

    def test_invalid_token(self):
        request = self.factory.get('/', HTTP_AUTHORIZATION='Token invalidkey123')
        from rest_framework.exceptions import AuthenticationFailed
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_no_header(self):
        request = self.factory.get('/')
        result = self.auth.authenticate(request)
        self.assertIsNone(result)


class GoogleDonationModelTests(TestCase):
    """Tests for GoogleDonation model behavior."""

    def test_create_google_donation(self):
        gd = GoogleDonation.objects.create()
        self.assertEqual(gd.source_type, 'google_portability')
        self.assertEqual(gd.status, 'pending')
        self.assertIsNotNone(gd.participant_token)
        self.assertIsNotNone(gd.researcher_token)

    def test_inherits_donation(self):
        gd = GoogleDonation.objects.create()
        self.assertTrue(Donation.objects.filter(pk=gd.pk).exists())

    def test_csv_path(self):
        gd = GoogleDonation.objects.create()
        path = gd._csv_path('youtube_history')
        self.assertEqual(path, f'data/{gd.pk}/google_portability/youtube_history_processed.csv')

    def test_get_data_types_empty_when_not_processed(self):
        gd = GoogleDonation.objects.create()
        self.assertEqual(gd.get_data_types(), [])

    def test_get_data_types_returns_received(self):
        gd = GoogleDonation.objects.create(
            processing_status='processed',
            data_type_status={
                'youtube_history': {'received': True, 'received_at': '2026-01-01'},
                'search': {'received': True, 'received_at': '2026-01-01'},
            },
        )
        types = gd.get_data_types()
        self.assertIn('youtube_history', types)
        self.assertIn('search', types)
        self.assertNotIn('discover', types)

    def test_oauth_token_encryption(self):
        gd = GoogleDonation.objects.create()
        original_token = "ya29.a0AfH6SMBx-test-token"
        gd.access_token = encrypt_text(original_token)
        gd.save()
        gd.refresh_from_db()
        self.assertNotEqual(gd.access_token, original_token)
        self.assertEqual(decrypt_text(gd.access_token), original_token)

    @patch('donations.models.google_portability.requests.post')
    def test_revoke_before_delete(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            'access_token': 'new_token',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_response

        gd = GoogleDonation.objects.create(
            access_token=encrypt_text('test_access'),
            refresh_token=encrypt_text('test_refresh'),
        )
        gd.revoke_before_delete()

        # Should have called: refresh token + revoke
        self.assertTrue(mock_post.called)
        call_urls = [call[0][0] for call in mock_post.call_args_list]
        self.assertTrue(any('oauth2.googleapis.com/token' in url for url in call_urls))
        self.assertTrue(any('authorization:reset' in url for url in call_urls))

    def test_fetch_data_and_count_with_encrypted_csv(self):
        gd = GoogleDonation.objects.create(
            processing_status='processed',
            data_type_status={
                'youtube_history': {'received': True, 'received_at': '2026-01-01'},
            },
        )
        csv_content = "timestamp,title,device_id\n2026-01-15 10:00:00,Video A,1\n2026-01-16 11:00:00,Video B,1\n"
        csv_path = gd._csv_path('youtube_history')
        write_encrypted_bytes(csv_path, csv_content.encode())

        try:
            count = gd.count_rows('youtube_history')
            self.assertEqual(count, 2)

            rows = gd.fetch_data('youtube_history', limit=1)
            self.assertEqual(len(rows), 1)

            rows = gd.fetch_data('youtube_history', limit=10, offset=1)
            self.assertEqual(len(rows), 1)
        finally:
            # Clean up test files
            if os.path.exists(csv_path):
                os.remove(csv_path)
            # Remove empty dirs
            dirpath = os.path.dirname(csv_path)
            while dirpath and dirpath != 'data':
                try:
                    os.rmdir(dirpath)
                except OSError:
                    break
                dirpath = os.path.dirname(dirpath)
            try:
                os.rmdir('data')
            except OSError:
                pass


class TikTokDonationModelTests(TestCase):
    """Tests for TikTokDonation model behavior."""

    def test_create_tiktok_donation(self):
        td = TikTokDonation.objects.create()
        self.assertEqual(td.source_type, 'tiktok_portability')
        self.assertEqual(td.status, 'pending')
        self.assertIsNotNone(td.participant_token)

    def test_inherits_donation(self):
        td = TikTokDonation.objects.create()
        self.assertTrue(Donation.objects.filter(pk=td.pk).exists())

    def test_pkce_pair(self):
        verifier, challenge = TikTokDonation.generate_pkce_pair()
        self.assertTrue(len(verifier) > 40)
        self.assertTrue(len(challenge) > 40)
        self.assertNotEqual(verifier, challenge)

    def test_store_token_info(self):
        td = TikTokDonation.objects.create()
        token_info = {
            'data': {
                'access_token': 'test_access_token',
                'refresh_token': 'test_refresh_token',
                'expires_in': 3600,
                'open_id': 'user123',
            }
        }
        td._store_token_info(token_info)
        td.save()
        td.refresh_from_db()

        self.assertIsNotNone(td.access_token)
        self.assertEqual(decrypt_text(td.access_token), 'test_access_token')
        self.assertEqual(decrypt_text(td.refresh_token), 'test_refresh_token')
        self.assertEqual(td.tiktok_user_id, 'user123')
        self.assertEqual(td.processing_status, 'authorized')
        self.assertEqual(td.code_verifier, '')

    def test_store_token_info_missing_access_token(self):
        td = TikTokDonation.objects.create()
        with self.assertRaises(KeyError):
            td._store_token_info({'data': {}})

    def test_fetch_data_not_processed(self):
        td = TikTokDonation.objects.create()
        self.assertEqual(td.fetch_data('tiktok_portability'), [])
        self.assertEqual(td.count_rows('tiktok_portability'), 0)

    def test_get_data_types(self):
        td = TikTokDonation.objects.create()
        self.assertEqual(td.get_data_types(), ['tiktok_portability'])
