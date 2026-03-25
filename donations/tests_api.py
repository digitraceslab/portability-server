"""Tests for the researcher REST API."""
import uuid
from unittest.mock import patch, MagicMock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

from donations.models import (
    Donation, GoogleDonation, TikTokDonation, ResearcherToken, Participant,
)


class DonationAPITestCase(TestCase):
    """Base test case with researcher token authentication."""
    def setUp(self):
        self.researcher = ResearcherToken.objects.create(name='test-researcher')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.researcher._raw_key}')


class TestCreateDonation(DonationAPITestCase):
    def test_create_google_donation(self):
        response = self.client.post('/api/donations/', {'source_type': 'google_portability'})
        self.assertEqual(response.status_code, 201)
        self.assertIn('token', response.data)
        self.assertIn('id', response.data)
        self.assertEqual(response.data['source_type'], 'google_portability')
        self.assertEqual(response.data['status'], 'pending')
        # Verify it's actually a GoogleDonation
        donation = Donation.objects.get(pk=response.data['id'])
        self.assertTrue(GoogleDonation.objects.filter(pk=donation.pk).exists())

    def test_create_tiktok_donation(self):
        response = self.client.post('/api/donations/', {'source_type': 'tiktok_portability'})
        self.assertEqual(response.status_code, 201)
        donation = Donation.objects.get(pk=response.data['id'])
        self.assertTrue(TikTokDonation.objects.filter(pk=donation.pk).exists())

    def test_create_sets_researcher(self):
        response = self.client.post('/api/donations/', {'source_type': 'google_portability'})
        donation = Donation.objects.get(pk=response.data['id'])
        self.assertEqual(donation.researcher, self.researcher)

    def test_create_invalid_source_type(self):
        response = self.client.post('/api/donations/', {'source_type': 'invalid'})
        self.assertEqual(response.status_code, 400)

    def test_create_missing_source_type(self):
        response = self.client.post('/api/donations/', {})
        self.assertEqual(response.status_code, 400)

    def test_create_without_auth(self):
        client = APIClient()
        response = client.post('/api/donations/', {'source_type': 'google_portability'})
        self.assertIn(response.status_code, [401, 403])


class TestListDonations(DonationAPITestCase):
    def test_list_own_donations(self):
        GoogleDonation.objects.create(researcher=self.researcher)
        TikTokDonation.objects.create(researcher=self.researcher)
        response = self.client.get('/api/donations/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_does_not_list_other_researchers_donations(self):
        other = ResearcherToken.objects.create(name='other')
        GoogleDonation.objects.create(researcher=other)
        GoogleDonation.objects.create(researcher=self.researcher)
        response = self.client.get('/api/donations/')
        self.assertEqual(len(response.data), 1)

    def test_empty_list(self):
        response = self.client.get('/api/donations/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class TestRetrieveDonation(DonationAPITestCase):
    def test_retrieve_own_donation(self):
        donation = GoogleDonation.objects.create(researcher=self.researcher)
        response = self.client.get(f'/api/donations/{donation.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], donation.pk)
        self.assertEqual(response.data['source_type'], 'google_portability')

    def test_retrieve_other_researchers_donation(self):
        other = ResearcherToken.objects.create(name='other')
        donation = GoogleDonation.objects.create(researcher=other)
        response = self.client.get(f'/api/donations/{donation.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_retrieve_nonexistent(self):
        response = self.client.get('/api/donations/99999/')
        self.assertEqual(response.status_code, 404)


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class TestDeleteDonation(DonationAPITestCase):
    def test_delete_own_donation(self):
        donation = GoogleDonation.objects.create(researcher=self.researcher)
        pk = donation.pk
        response = self.client.delete(f'/api/donations/{pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Donation.objects.filter(pk=pk).exists())

    def test_delete_other_researchers_donation(self):
        other = ResearcherToken.objects.create(name='other')
        donation = GoogleDonation.objects.create(researcher=other)
        response = self.client.delete(f'/api/donations/{donation.pk}/')
        self.assertEqual(response.status_code, 404)

    @patch('donations.models.google_portability.requests.post')
    def test_delete_calls_revoke(self, mock_post):
        from donations.utils.crypto import encrypt_text
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {'access_token': 'new', 'expires_in': 3600}
        mock_post.return_value = mock_response

        donation = GoogleDonation.objects.create(
            researcher=self.researcher,
            access_token=encrypt_text('test_access'),
            refresh_token=encrypt_text('test_refresh'),
        )
        response = self.client.delete(f'/api/donations/{donation.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertTrue(mock_post.called)


class TestDataEndpoint(DonationAPITestCase):
    def test_data_types_listing(self):
        donation = GoogleDonation.objects.create(
            researcher=self.researcher,
            processing_status='processed',
            data_type_status={
                'youtube_history': {'received': True, 'received_at': '2026-01-01'},
            },
        )
        response = self.client.get(f'/api/donations/{donation.pk}/data/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('data_types', response.data)
        self.assertIn('youtube_history', response.data['data_types'])

    def test_data_with_invalid_type(self):
        donation = GoogleDonation.objects.create(researcher=self.researcher)
        response = self.client.get(f'/api/donations/{donation.pk}/data/?data_type=nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['data'], [])

    def test_data_other_researchers_donation(self):
        other = ResearcherToken.objects.create(name='other')
        donation = GoogleDonation.objects.create(researcher=other)
        response = self.client.get(f'/api/donations/{donation.pk}/data/')
        self.assertEqual(response.status_code, 404)

    def test_without_auth(self):
        donation = GoogleDonation.objects.create(researcher=self.researcher)
        client = APIClient()
        response = client.get(f'/api/donations/{donation.pk}/data/')
        self.assertIn(response.status_code, [401, 403])
