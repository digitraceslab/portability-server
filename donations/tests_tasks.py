"""Tests for Celery tasks and the OAuth callback views that queue them."""
from unittest.mock import patch

from django.test import TestCase

from donations.models import Donation, GoogleDonation, TikTokDonation
from donations.tasks import process_donation, check_pending_donations, MAX_RETRIES


def _fake_process_data(donation):
    """Simulate successful _process_data: set status to processed."""
    donation.status = 'processed'
    donation.retry_count = 0
    donation.save(update_fields=['status', 'retry_count'])


class TestProcessDonation(TestCase):
    """Tests for the process_donation Celery task."""

    @patch.object(GoogleDonation, '_process_data', autospec=True, side_effect=_fake_process_data)
    def test_processes_authorized_google_donation(self, mock_process_data):
        donation = GoogleDonation.objects.create(status='authorized')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')
        mock_process_data.assert_called_once()

    def test_skips_pending_donation(self):
        donation = GoogleDonation.objects.create(status='pending')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'pending')

    def test_skips_already_processed_donation(self):
        donation = GoogleDonation.objects.create(status='processed')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')

    @patch.object(GoogleDonation, '_process_data', side_effect=Exception('fail'))
    def test_sets_error_on_exception(self, mock_process_data):
        donation = GoogleDonation.objects.create(status='authorized')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'error')
        self.assertEqual(donation.retry_count, 1)
        self.assertIn('fail', donation.processing_log)
        self.assertIn('attempt 1', donation.processing_log)

    @patch.object(GoogleDonation, '_process_data', side_effect=Exception('fail'))
    def test_retries_error_donation(self, mock_process_data):
        """An errored donation with retries remaining is re-processed."""
        donation = GoogleDonation.objects.create(status='error', retry_count=1)
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'error')
        self.assertEqual(donation.retry_count, 2)

    @patch.object(GoogleDonation, '_process_data', autospec=True, side_effect=_fake_process_data)
    def test_error_donation_succeeds_on_retry(self, mock_process_data):
        """An errored donation can succeed and resets retry_count."""
        donation = GoogleDonation.objects.create(status='error', retry_count=1)
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')
        self.assertEqual(donation.retry_count, 0)

    def test_gives_up_after_max_retries(self):
        donation = GoogleDonation.objects.create(status='error', retry_count=MAX_RETRIES)
        with self.assertLogs('donations.tasks', level='ERROR') as cm:
            process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'error')
        self.assertEqual(donation.retry_count, MAX_RETRIES)
        self.assertTrue(any('developer attention' in msg for msg in cm.output))

    @patch.object(TikTokDonation, '_process_data', autospec=True, side_effect=_fake_process_data)
    def test_processes_tiktok_donation(self, mock_process_data):
        donation = TikTokDonation.objects.create(status='authorized')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')
        mock_process_data.assert_called_once()

    @patch.object(GoogleDonation, '_process_data', autospec=True, side_effect=_fake_process_data)
    def test_processes_stuck_processing_donation(self, mock_process_data):
        donation = GoogleDonation.objects.create(status='processing')
        process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'processed')

    @patch.object(GoogleDonation, '_process_data', side_effect=Exception('db down'))
    def test_logs_when_error_save_fails(self, mock_process_data):
        """If saving error status fails, the failure is logged."""
        donation = GoogleDonation.objects.create(status='authorized')

        def failing_save(self, *args, **kwargs):
            raise Exception('db error')

        with patch.object(GoogleDonation, 'save', failing_save):
            with self.assertLogs('donations.tasks', level='ERROR') as cm:
                process_donation(donation.pk)
            self.assertTrue(any('db error' in msg for msg in cm.output))


class TestCheckPendingDonations(TestCase):
    """Tests for the check_pending_donations periodic Celery task."""

    @patch('donations.tasks.process_donation')
    def test_queues_authorized_donations(self, mock_task):
        d1 = GoogleDonation.objects.create(status='authorized')
        d2 = TikTokDonation.objects.create(status='authorized')
        check_pending_donations()
        queued_pks = {call.kwargs['args'][0] for call in mock_task.apply_async.call_args_list}
        self.assertIn(d1.pk, queued_pks)
        self.assertIn(d2.pk, queued_pks)

    @patch('donations.tasks.process_donation')
    def test_queues_error_donations_with_retries_remaining(self, mock_task):
        donation = GoogleDonation.objects.create(status='error', retry_count=1)
        check_pending_donations()
        queued_pks = {call.kwargs['args'][0] for call in mock_task.apply_async.call_args_list}
        self.assertIn(donation.pk, queued_pks)

    @patch('donations.tasks.process_donation')
    def test_does_not_queue_exhausted_error_donations(self, mock_task):
        GoogleDonation.objects.create(status='error', retry_count=MAX_RETRIES)
        check_pending_donations()
        mock_task.apply_async.assert_not_called()

    @patch('donations.tasks.process_donation')
    def test_does_not_queue_pending_or_processed(self, mock_task):
        GoogleDonation.objects.create(status='pending')
        GoogleDonation.objects.create(status='processed')
        check_pending_donations()
        mock_task.apply_async.assert_not_called()

    @patch('donations.tasks.process_donation')
    def test_queues_stuck_processing_donations(self, mock_task):
        donation = GoogleDonation.objects.create(status='processing')
        check_pending_donations()
        queued_pks = {call.kwargs['args'][0] for call in mock_task.apply_async.call_args_list}
        self.assertIn(donation.pk, queued_pks)


class TestOAuthCallbackQueuesTask(TestCase):
    """Tests that OAuth callback views queue process_donation on success."""

    @patch('donations.views.process_donation')
    @patch.object(GoogleDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_google_callback_queues_task(self, mock_handle, mock_task):
        donation = GoogleDonation.objects.create(
            status='pending', oauth_state='test-state',
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
            status='pending', oauth_state='test-state',
        )
        response = self.client.get(
            '/oauth/tiktok/callback/?state=test-state&code=testcode'
        )
        self.assertEqual(response.status_code, 302)
        mock_task.delay.assert_called_once_with(donation.pk)
