"""Tests for donations app functionality."""
import os
import tempfile
import uuid
from unittest.mock import patch, MagicMock

import requests

from cryptography.fernet import Fernet
from django.test import TestCase, Client, RequestFactory, override_settings
from rest_framework.test import APIRequestFactory

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken, Participant
from donations.authentication import ResearcherTokenAuthentication
from donations.utils.crypto import (
    encrypt_text, decrypt_text, encrypt_bytes, decrypt_bytes,
    write_encrypted_bytes,
)

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
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
        self.assertIsNotNone(donation.token)
        self.assertIsNone(donation.researcher)

    def test_unique_tokens(self):
        d1 = Donation.objects.create(source_type='google_portability')
        d2 = Donation.objects.create(source_type='tiktok_portability')
        self.assertNotEqual(d1.token, d2.token)

    def test_donation_with_researcher(self):
        researcher = ResearcherToken.objects.create(name='lab-alpha')
        donation = Donation.objects.create(
            source_type='google_portability',
            researcher=researcher,
        )
        self.assertEqual(donation.researcher, researcher)


class ResearcherTokenModelTests(TestCase):
    """Tests for ResearcherToken model behavior."""
    def test_auto_generates_key(self):
        token = ResearcherToken.objects.create(name='test')
        self.assertEqual(len(token.key), 64)  # SHA-256 hex digest

    def test_key_is_unique_across_tokens(self):
        t1 = ResearcherToken.objects.create(name='token-one')
        t2 = ResearcherToken.objects.create(name='token-two')
        self.assertNotEqual(t1.key, t2.key)


class ResearcherTokenAuthTests(TestCase):
    """Tests for researcher token authentication."""
    def setUp(self):
        self.auth = ResearcherTokenAuthentication()
        self.factory = APIRequestFactory()
        self.token = ResearcherToken.objects.create(name='test-auth')

    def test_valid_token(self):
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Token {self.token._raw_key}')
        user, auth_token = self.auth.authenticate(request)
        self.assertIsNone(user)
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


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class GoogleDonationModelTests(TestCase):
    """Tests for GoogleDonation model behavior."""

    def test_create_google_donation(self):
        gd = GoogleDonation.objects.create()
        self.assertEqual(gd.source_type, 'google_portability')
        self.assertEqual(gd.status, 'pending')
        self.assertIsNotNone(gd.token)

    def test_source_type_display(self):
        gd = GoogleDonation.objects.create()
        self.assertEqual(gd.source_type_display, 'Google')

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
    def test_revoke(self, mock_post):
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
        result = gd.revoke()

        # Should return (True, ...) on success
        self.assertTrue(result[0])

        # Should have called: refresh token + revoke
        self.assertTrue(mock_post.called)
        call_urls = [call[0][0] for call in mock_post.call_args_list]
        self.assertTrue(any('oauth2.googleapis.com/token' in url for url in call_urls))
        self.assertTrue(any('authorization:reset' in url for url in call_urls))

    @patch('donations.models.google_portability.requests.post')
    def test_revoke_refresh_fails(self, mock_post):
        mock_post.side_effect = requests.RequestException("network error")

        gd = GoogleDonation.objects.create(
            access_token=encrypt_text('test_access'),
            refresh_token=encrypt_text('test_refresh'),
        )
        result = gd.revoke()

        # Should return (False, ...) when refresh fails
        self.assertFalse(result[0])

        # Only the refresh token call should have been attempted, not the revoke
        call_urls = [call[0][0] for call in mock_post.call_args_list]
        self.assertTrue(any('oauth2.googleapis.com/token' in url for url in call_urls))
        self.assertFalse(any('authorization:reset' in url for url in call_urls))

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


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class TikTokDonationModelTests(TestCase):
    """Tests for TikTokDonation model behavior."""

    def test_create_tiktok_donation(self):
        td = TikTokDonation.objects.create()
        self.assertEqual(td.source_type, 'tiktok_portability')
        self.assertEqual(td.status, 'pending')
        self.assertIsNotNone(td.token)

    def test_source_type_display(self):
        td = TikTokDonation.objects.create()
        self.assertEqual(td.source_type_display, 'TikTok')

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


class DonationLandingViewTests(TestCase):
    """Tests for the donation landing page view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = f'/donate/{self.donation.token}/'

    def test_landing_page_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Niimport')

    def test_landing_shows_terms_link_when_not_accepted(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Accept Terms')

    def test_landing_404_for_invalid_token(self):
        response = self.client.get(f'/donate/{uuid.uuid4()}/')
        self.assertEqual(response.status_code, 404)


class AcceptTermsViewTests(TestCase):
    """Tests for the terms acceptance view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = f'/donate/{self.donation.token}/terms/'

    def test_terms_page_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Terms of Data Donation')

    def test_accept_terms_post(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertIsNotNone(self.donation.terms_accepted_at)


class AuthorizeViewTests(TestCase):
    """Tests for the OAuth authorization redirect view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = f'/donate/{self.donation.token}/authorize/'

    def test_authorize_redirects_to_terms_if_not_accepted(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('terms', response.url)

    def test_authorize_redirects_to_oauth_when_terms_accepted(self):
        from django.utils import timezone
        self.donation.terms_accepted_at = timezone.now()
        self.donation.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('accounts.google.com', response.url)


class DataPreviewViewTests(TestCase):
    """Tests for the data preview view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = f'/donate/{self.donation.token}/data/'

    def test_data_preview_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Data Preview')


class RevokeDonationViewTests(TestCase):
    """Tests for the donation revocation view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = f'/donate/{self.donation.token}/revoke/'

    def test_revoke_confirm_page_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Revoke')

    @patch('donations.models.google_portability.GoogleDonation.revoke')
    def test_revoke_post_deletes_donation(self, mock_revoke):
        mock_revoke.return_value = (True, "Authorization revoked successfully.")
        pk = self.donation.pk
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Donation.objects.filter(pk=pk).exists())

    @patch('donations.models.google_portability.GoogleDonation.revoke')
    def test_revoke_post_keeps_donation_on_failure(self, mock_revoke):
        mock_revoke.return_value = (False, "Token refresh failed: network error")
        pk = self.donation.pk
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Donation.objects.filter(pk=pk).exists())
        self.assertContains(response, "Token refresh failed: network error")

    @patch('donations.models.google_portability.GoogleDonation.revoke')
    def test_revoke_post_deletes_donation_on_success(self, mock_revoke):
        mock_revoke.return_value = (True, "Authorization revoked successfully.")
        pk = self.donation.pk
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Donation.objects.filter(pk=pk).exists())


class OAuthCallbackViewTests(TestCase):
    """Tests for OAuth callback views."""
    def test_google_callback_404_without_state(self):
        response = self.client.get('/oauth/google/callback/')
        self.assertEqual(response.status_code, 404)

    def test_tiktok_callback_404_without_state(self):
        response = self.client.get('/oauth/tiktok/callback/')
        self.assertEqual(response.status_code, 404)

    def test_google_callback_404_with_invalid_state(self):
        response = self.client.get('/oauth/google/callback/?state=invalid')
        self.assertEqual(response.status_code, 404)

    def test_tiktok_callback_404_with_invalid_state(self):
        response = self.client.get('/oauth/tiktok/callback/?state=invalid')
        self.assertEqual(response.status_code, 404)


class ParticipantModelTests(TestCase):
    """Tests for Participant model behavior."""
    def test_create_participant(self):
        participant = Participant.objects.create()
        self.assertIsNotNone(participant.token)
        self.assertIsNotNone(participant.created_at)

    def test_auto_uuid(self):
        p1 = Participant.objects.create()
        p2 = Participant.objects.create()
        self.assertNotEqual(p1.token, p2.token)

    def test_str(self):
        participant = Participant.objects.create()
        self.assertEqual(str(participant), str(participant.token))


class DonationLandingParticipantTests(TestCase):
    """Tests for participant token handling in the donation landing view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = f'/donate/{self.donation.token}/'

    def test_landing_page_shows_prepopulated_token(self):
        response1 = self.client.get(self.url)
        response2 = self.client.get(self.url)
        self.assertEqual(response1.status_code, 200)
        token1 = response1.context['prepopulated_token']
        token2 = response2.context['prepopulated_token']
        self.assertEqual(token1, token2)
        uuid.UUID(str(token1))

    def test_post_creates_new_participant(self):
        new_token = str(uuid.uuid4())
        response = self.client.post(self.url, {'participant_token_input': new_token})
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertIsNotNone(self.donation.participant)
        self.assertEqual(str(self.donation.participant.token), new_token)
        self.assertTrue(Participant.objects.filter(token=new_token).exists())

    def test_post_with_existing_participant_token(self):
        existing_participant = Participant.objects.create()
        initial_count = Participant.objects.count()
        response = self.client.post(
            self.url, {'participant_token_input': str(existing_participant.token)}
        )
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.participant, existing_participant)
        self.assertEqual(Participant.objects.count(), initial_count)

    def test_post_with_invalid_uuid(self):
        response = self.client.post(self.url, {'participant_token_input': 'not-a-uuid'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('token_error', response.context)

    def test_prepopulated_token_uses_existing_participant(self):
        existing_participant = Participant.objects.create()
        self.donation.participant = existing_participant
        self.donation.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            str(response.context['prepopulated_token']),
            str(existing_participant.token),
        )

    @patch('donations.views.process_donation')
    @patch.object(GoogleDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_google_auth_callback_creates_participant(self, mock_handle, mock_task):
        self.donation.oauth_state = 'test-state-create'
        self.donation.save()
        self.client.get('/oauth/google/callback/?state=test-state-create&code=testcode')
        self.donation.refresh_from_db()
        self.assertIsNotNone(self.donation.participant)
        self.assertEqual(
            self.donation.participant.token,
            self.donation.suggested_participant_token,
        )

    @patch('donations.views.process_donation')
    @patch.object(GoogleDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_google_auth_callback_preserves_existing_participant(self, mock_handle, mock_task):
        existing_participant = Participant.objects.create()
        self.donation.participant = existing_participant
        self.donation.oauth_state = 'test-state-preserve'
        self.donation.save()
        self.client.get('/oauth/google/callback/?state=test-state-preserve&code=testcode')
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.participant, existing_participant)


class ParticipantHomeViewTests(TestCase):
    """Tests for the participant home page view."""
    def setUp(self):
        self.participant = Participant.objects.create()
        self.donation1 = GoogleDonation.objects.create(participant=self.participant)
        self.donation2 = GoogleDonation.objects.create(participant=self.participant)
        self.url = f'/participant/{self.participant.token}/'

    def test_participant_home_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.participant.token))

    def test_lists_donations(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'google_portability')
        self.assertEqual(len(response.context['donations']), 2)

    def test_only_shows_own_donations(self):
        other_participant = Participant.objects.create()
        GoogleDonation.objects.create(participant=other_participant)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['donations']), 2)

    def test_404_for_invalid_token(self):
        response = self.client.get(f'/participant/{uuid.uuid4()}/')
        self.assertEqual(response.status_code, 404)


class TestScopeFiltering(TestCase):
    """Tests for GoogleDonation._get_scopes_and_resources() scope/resource filtering."""

    SCOPE_PREFIX = 'https://www.googleapis.com/auth/dataportability.'

    def test_empty_requested_types_returns_none(self):
        gd = GoogleDonation.objects.create(requested_data_types=[])
        scopes, resources = gd._get_scopes_and_resources()
        self.assertEqual(scopes, [])
        self.assertEqual(resources, [])

    def test_all_requested_types_returns_all(self):
        gd = GoogleDonation.objects.create(requested_data_types=['all'])
        scopes, resources = gd._get_scopes_and_resources()
        self.assertGreater(len(scopes), 7)
        self.assertIn(self.SCOPE_PREFIX + 'myactivity.youtube', scopes)
        self.assertIn(self.SCOPE_PREFIX + 'youtube.channel', scopes)

    def test_single_type_returns_matching_scopes(self):
        gd = GoogleDonation.objects.create(requested_data_types=['youtube_history'])
        scopes, resources = gd._get_scopes_and_resources()

        self.assertEqual(scopes, [self.SCOPE_PREFIX + 'myactivity.youtube'])
        self.assertEqual(resources, ['myactivity.youtube'])

    def test_discover_returns_three_scopes(self):
        gd = GoogleDonation.objects.create(requested_data_types=['discover'])
        scopes, resources = gd._get_scopes_and_resources()

        expected_raw = ['discover.likes', 'discover.follows', 'discover.not_interested']
        self.assertEqual(len(scopes), 3)
        self.assertEqual(len(resources), 3)
        for raw in expected_raw:
            self.assertIn(self.SCOPE_PREFIX + raw, scopes)
            self.assertIn(raw, resources)

    def test_deduplication(self):
        gd = GoogleDonation.objects.create(
            requested_data_types=['google_play_games', 'google_play_store']
        )
        scopes, resources = gd._get_scopes_and_resources()

        # Both types map to myactivity.play — should appear exactly once
        self.assertEqual(scopes, [self.SCOPE_PREFIX + 'myactivity.play'])
        self.assertEqual(resources, ['myactivity.play'])

    def test_multiple_types(self):
        gd = GoogleDonation.objects.create(
            requested_data_types=['youtube_history', 'search']
        )
        scopes, resources = gd._get_scopes_and_resources()

        self.assertEqual(len(scopes), 2)
        self.assertEqual(len(resources), 2)
        self.assertIn(self.SCOPE_PREFIX + 'myactivity.youtube', scopes)
        self.assertIn(self.SCOPE_PREFIX + 'myactivity.search', scopes)
        self.assertIn('myactivity.youtube', resources)
        self.assertIn('myactivity.search', resources)
