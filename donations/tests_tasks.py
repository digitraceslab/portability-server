"""Tests for Celery tasks and the OAuth callback views that queue them."""
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client

from donations.models import Donation, GoogleDonation, TikTokDonation
from donations.tasks import process_donation, check_pending_donations


class TestProcessDonation(TestCase):
    """Tests for the process_donation Celery task."""

    @patch.object(GoogleDonation, '_process_data')
    def test_processes_authorized_google_donation(self, mock_process_data):
        donation = GoogleDonation.objects.create(status='authorized')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')
        mock_process_data.assert_called_once()

    def test_skips_non_authorized_donation(self):
        donation = GoogleDonation.objects.create(status='pending')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'pending')

    @patch.object(GoogleDonation, '_process_data', side_effect=Exception('fail'))
    def test_sets_error_on_exception(self, mock_process_data):
        donation = GoogleDonation.objects.create(status='authorized')
        with self.assertRaises(Exception):
            process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'error')
        self.assertIn('fail', donation.processing_log)

    @patch.object(TikTokDonation, '_process_data')
    def test_processes_tiktok_donation(self, mock_process_data):
        donation = TikTokDonation.objects.create(status='authorized')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')
        mock_process_data.assert_called_once()


class TestCheckPendingDonations(TestCase):
    """Tests for the check_pending_donations periodic Celery task."""

    @patch('donations.tasks.process_donation')
    def test_queues_authorized_donations(self, mock_task):
        donation1 = GoogleDonation.objects.create(status='authorized')
        donation2 = TikTokDonation.objects.create(status='authorized')
        check_pending_donations()
        queued_pks = {call.args[0] for call in mock_task.delay.call_args_list}
        self.assertIn(donation1.pk, queued_pks)
        self.assertIn(donation2.pk, queued_pks)
        self.assertEqual(mock_task.delay.call_count, 2)

    @patch('donations.tasks.process_donation')
    def test_does_not_queue_other_statuses(self, mock_task):
        GoogleDonation.objects.create(status='pending')
        GoogleDonation.objects.create(status='processed')
        check_pending_donations()
        mock_task.delay.assert_not_called()


class TestOAuthCallbackQueuesTask(TestCase):
    """Tests that OAuth callback views queue process_donation on success."""

    @patch('donations.views.process_donation')
    @patch.object(GoogleDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_google_callback_queues_task(self, mock_handle, mock_task):
        donation = GoogleDonation.objects.create(
            status='pending',
            oauth_state='test-state',
        )
        response = self.client.get(
            '/oauth/google/callback/?state=test-state&code=testcode'
        )
        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once_with(donation.pk)

    @patch('donations.views.process_donation')
    @patch.object(TikTokDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_tiktok_callback_queues_task(self, mock_handle, mock_task):
        donation = TikTokDonation.objects.create(
            status='pending',
            oauth_state='test-state',
        )
        response = self.client.get(
            '/oauth/tiktok/callback/?state=test-state&code=testcode'
        )
        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once_with(donation.pk)
