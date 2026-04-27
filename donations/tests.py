"""Tests for donations app functionality."""
import os
import tempfile
import uuid
from unittest.mock import patch, MagicMock

import requests

from cryptography.fernet import Fernet
from django.test import TestCase, Client, RequestFactory, override_settings
from rest_framework.test import APIRequestFactory

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken, Participant, hash_token
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


def _set_donation_session(client, raw_token):
    """Mimic donation_entry: store the hash in session."""
    session = client.session
    session['donation_token'] = hash_token(raw_token)
    session.save()


def _set_participant_session(client, raw_token):
    """Mimic participant_entry: store the hash and the raw for display."""
    session = client.session
    session['participant_token'] = hash_token(raw_token)
    session['participant_raw_token'] = str(raw_token)
    session.save()


class DonationLandingViewTests(TestCase):
    """Tests for the donation landing page view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = '/donate/'
        _set_donation_session(self.client, self.donation._raw_token)

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

    def test_landing_404_without_session(self):
        client = Client()
        response = client.get('/donate/')
        self.assertEqual(response.status_code, 404)

    def test_donation_entry_sets_session_and_redirects(self):
        client = Client()
        response = client.get(f'/donate/{self.donation._raw_token}/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/donate/')
        # Session stores the hash, never the raw UUID.
        self.assertEqual(client.session.get('donation_token'), self.donation.token)
        self.assertNotEqual(client.session.get('donation_token'), self.donation._raw_token)


class AcceptTermsViewTests(TestCase):
    """Tests for the terms acceptance view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = '/donate/terms/'
        _set_donation_session(self.client, self.donation._raw_token)

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
        self.url = '/donate/authorize/'
        _set_donation_session(self.client, self.donation._raw_token)

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
        self.url = '/donate/data/'
        _set_donation_session(self.client, self.donation._raw_token)

    def test_data_preview_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Data Preview')


class RevokeDonationViewTests(TestCase):
    """Tests for the donation revocation view."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.url = '/donate/revoke/'
        _set_donation_session(self.client, self.donation._raw_token)

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
        self.url = '/donate/'
        _set_donation_session(self.client, self.donation._raw_token)

    def test_landing_no_link_when_no_participant_in_session(self):
        # No participant raw in session: link is hidden.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['participant_link_url'])

    def test_post_creates_new_participant(self):
        new_token = str(uuid.uuid4())
        response = self.client.post(self.url, {'participant_token_input': new_token})
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertIsNotNone(self.donation.participant)
        self.assertEqual(self.donation.participant.token, hash_token(new_token))
        self.assertIsNotNone(Participant.get_by_raw_token(new_token))
        # Raw token is stashed in session for display.
        self.assertEqual(self.client.session.get('participant_raw_token'), new_token)

    def test_post_with_existing_participant_token(self):
        existing_participant = Participant.objects.create()
        initial_count = Participant.objects.count()
        response = self.client.post(
            self.url, {'participant_token_input': existing_participant._raw_token}
        )
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.participant, existing_participant)
        self.assertEqual(Participant.objects.count(), initial_count)

    def test_post_with_invalid_uuid_format(self):
        # Right length but not a valid UUID.
        response = self.client.post(
            self.url, {'participant_token_input': 'z' * 36})
        self.assertEqual(response.status_code, 200)
        self.assertIn('UUID', response.context['token_error'])

    def test_post_rejects_too_short_token(self):
        response = self.client.post(
            self.url, {'participant_token_input': 'short'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('too short', response.context['token_error'].lower())

    def test_post_blank_is_no_op(self):
        response = self.client.post(self.url, {'participant_token_input': ''})
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertIsNone(self.donation.participant)

    def test_landing_shows_link_when_session_has_matching_raw(self):
        # After linking a participant, the participant URL is rendered.
        existing_participant = Participant.objects.create()
        self.donation.participant = existing_participant
        self.donation.save()
        _set_participant_session(self.client, existing_participant._raw_token)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context['participant_link_url'])
        self.assertIn(existing_participant._raw_token,
                      response.context['participant_link_url'])
        # And it's in the rendered HTML so the user can copy it.
        self.assertContains(response, existing_participant._raw_token)

    def test_landing_no_link_when_linked_but_no_session_raw(self):
        # Participant linked, but no raw in session — the URL must not be
        # shown (we cannot recover the raw from the hashed DB value).
        existing_participant = Participant.objects.create()
        self.donation.participant = existing_participant
        self.donation.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['participant_link_url'])
        self.assertNotContains(response, existing_participant.token)

    def test_generate_creates_participant_and_displays_link(self):
        response = self.client.post('/donate/generate-participant/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/donate/')
        self.donation.refresh_from_db()
        self.assertIsNotNone(self.donation.participant)
        # Raw token in session, ready to render the link on next GET.
        raw = self.client.session.get('participant_raw_token')
        self.assertIsNotNone(raw)
        self.assertEqual(hash_token(raw), self.donation.participant.token)
        # Following GET shows the link in the rendered page.
        response = self.client.get(self.url)
        self.assertContains(response, raw)
        self.assertContains(response, '/participant/' + raw + '/')

    def test_generate_requires_donation_session(self):
        client = Client()
        response = client.post('/donate/generate-participant/')
        self.assertEqual(response.status_code, 404)

    def test_generate_get_not_allowed(self):
        response = self.client.get('/donate/generate-participant/')
        self.assertEqual(response.status_code, 405)

    def test_generate_relinks_when_existing_participant_is_unrecoverable(self):
        # Donation linked to a non-suggested participant (e.g. user previously
        # pasted a custom token). Clicking Generate must re-link the donation
        # to the suggested-token participant so the URL can be displayed.
        previous = Participant.objects.create()
        self.donation.participant = previous
        self.donation.save()
        suggested = self.donation.suggested_participant_token
        response = self.client.post('/donate/generate-participant/')
        self.assertEqual(response.status_code, 302)
        self.donation.refresh_from_db()
        self.assertEqual(
            self.donation.participant.token, hash_token(suggested))
        self.assertNotEqual(self.donation.participant_id, previous.pk)
        # The previous participant still exists; only the donation moved.
        self.assertTrue(Participant.objects.filter(pk=previous.pk).exists())
        # GET shows the link.
        response = self.client.get(self.url)
        self.assertContains(response, str(suggested))

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
            hash_token(self.donation.suggested_participant_token),
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
        self.url = '/participant/'
        _set_participant_session(self.client, self.participant._raw_token)

    def test_participant_home_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # Raw participant token (UUID) is displayed when present in session.
        self.assertContains(response, self.participant._raw_token)
        # The hash must not be displayed.
        self.assertNotContains(response, self.participant.token)

    def test_participant_home_hides_token_when_not_in_session(self):
        # Coming via switch_to_participant: only donation_token in session,
        # no raw participant token. Page must not leak the hash.
        client = Client()
        _set_donation_session(client, self.donation1._raw_token)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.participant.token)
        self.assertContains(response, 'token is saved')

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

    def test_404_without_session(self):
        client = Client()
        response = client.get('/participant/')
        self.assertEqual(response.status_code, 404)

    def test_participant_entry_sets_session_and_redirects(self):
        client = Client()
        response = client.get(f'/participant/{self.participant._raw_token}/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/participant/')
        # Session stores the hash for auth, the raw separately for display.
        self.assertEqual(client.session.get('participant_token'), self.participant.token)
        self.assertEqual(
            client.session.get('participant_raw_token'), self.participant._raw_token)

    def test_select_donation_switches_session_donation(self):
        response = self.client.get(f'/participant/select/{self.donation1.pk}/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/donate/')
        self.assertEqual(self.client.session.get('donation_token'), self.donation1.token)

    def test_select_donation_with_next_data(self):
        response = self.client.get(f'/participant/select/{self.donation1.pk}/?next=data')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/donate/data/')

    def test_select_donation_rejects_other_participants_donation(self):
        other_participant = Participant.objects.create()
        other_donation = GoogleDonation.objects.create(participant=other_participant)
        response = self.client.get(f'/participant/select/{other_donation.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_donation_session_grants_participant_access_via_fallback(self):
        # User authenticated via a donation token reaches the participant page
        # because the donation FK identifies the participant.
        client = Client()
        _set_donation_session(client, self.donation1._raw_token)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['donations']), 2)


class SwitchToParticipantTests(TestCase):
    """Tests for the donation -> participant session switch view."""
    def setUp(self):
        self.participant = Participant.objects.create()
        self.donation = GoogleDonation.objects.create(participant=self.participant)

    def test_switch_redirects_to_participant_home(self):
        _set_donation_session(self.client, self.donation._raw_token)
        response = self.client.get('/donate/switch-to-participant/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/participant/')

    def test_switch_404_when_donation_has_no_participant(self):
        unlinked = GoogleDonation.objects.create()
        _set_donation_session(self.client, unlinked._raw_token)
        response = self.client.get('/donate/switch-to-participant/')
        self.assertEqual(response.status_code, 404)

    def test_switch_404_without_donation_session(self):
        response = self.client.get('/donate/switch-to-participant/')
        self.assertEqual(response.status_code, 404)


class TokenNotInUrlTests(TestCase):
    """Tokens must not appear in URLs of pages rendered after entry."""
    def setUp(self):
        self.donation = GoogleDonation.objects.create()
        self.participant = Participant.objects.create()
        self.donation.participant = self.participant
        self.donation.save()

    def test_donation_pages_do_not_leak_token_in_links(self):
        _set_donation_session(self.client, self.donation._raw_token)
        response = self.client.get('/donate/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn(self.donation._raw_token, body)
        self.assertNotIn(self.donation.token, body)

    def test_participant_page_does_not_leak_other_tokens(self):
        # Participant page exposes the participant's *own* raw token (the
        # session key the user is supposed to keep). It must not show the
        # token hash, and donation-landing / data-preview links must not
        # contain donation tokens.
        _set_participant_session(self.client, self.participant._raw_token)
        response = self.client.get('/participant/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn(self.participant.token, body)
        self.assertNotIn(f'/donate/{self.donation._raw_token}/', body)
        self.assertNotIn(f'/donate/{self.donation._raw_token}/data/', body)


class HashingModelTests(TestCase):
    """Tests for token hashing on Donation and Participant."""
    def test_donation_token_stored_hashed(self):
        d = GoogleDonation.objects.create()
        self.assertEqual(len(d.token), 64)
        self.assertEqual(d.token, hash_token(d._raw_token))
        self.assertNotEqual(d.token, d._raw_token)

    def test_participant_token_stored_hashed(self):
        p = Participant.objects.create()
        self.assertEqual(len(p.token), 64)
        self.assertEqual(p.token, hash_token(p._raw_token))

    def test_get_by_raw_token_donation(self):
        d = GoogleDonation.objects.create()
        found = Donation.get_by_raw_token(d._raw_token)
        self.assertIsNotNone(found)
        self.assertEqual(found.pk, d.pk)
        self.assertIsNone(Donation.get_by_raw_token(uuid.uuid4()))

    def test_get_by_raw_token_participant(self):
        p = Participant.objects.create()
        found = Participant.get_by_raw_token(p._raw_token)
        self.assertEqual(found, p)
        self.assertIsNone(Participant.get_by_raw_token(uuid.uuid4()))

    def test_regenerate_donation_token(self):
        d = GoogleDonation.objects.create()
        old_hash = d.token
        old_raw = d._raw_token
        new_raw = d.regenerate_token()
        self.assertNotEqual(new_raw, old_raw)
        self.assertNotEqual(d.token, old_hash)
        self.assertEqual(d.token, hash_token(new_raw))
        self.assertIsNone(Donation.get_by_raw_token(old_raw))
        self.assertEqual(Donation.get_by_raw_token(new_raw).pk, d.pk)

    def test_regenerate_participant_token(self):
        p = Participant.objects.create()
        old_raw = p._raw_token
        new_raw = p.regenerate_token()
        self.assertNotEqual(new_raw, old_raw)
        self.assertEqual(p.token, hash_token(new_raw))
        self.assertIsNone(Participant.get_by_raw_token(old_raw))
        self.assertEqual(Participant.get_by_raw_token(new_raw), p)


class TestScopeFiltering(TestCase):
    """Tests for GoogleDonation._get_scopes_and_resources() scope/resource filtering."""

    SCOPE_PREFIX = 'https://www.googleapis.com/auth/dataportability.'

    def test_empty_requested_types_returns_none(self):
        gd = GoogleDonation.objects.create(requested_data_types=[])
        scopes, resources = gd._get_scopes_and_resources()
        self.assertEqual(scopes, [])
        self.assertEqual(resources, [])

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
