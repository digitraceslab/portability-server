"""Tests for Celery tasks and the OAuth callback views that queue them."""
import os
import shutil
import tempfile
import time
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.utils import timezone

from django.test import TestCase, override_settings

from donations.models import Donation, GoogleDonation, TikTokDonation
from donations.tasks import (
    process_donation, check_pending_donations, remove_stale_archives,
    expire_donations, MAX_RETRIES,
)


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

    @patch('donations.views.process_donation')
    @patch.object(GoogleDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_google_callback_handles_queue_failure(self, mock_handle, mock_task):
        donation = GoogleDonation.objects.create(
            status='pending', oauth_state='test-state',
        )
        mock_task.delay.side_effect = RuntimeError('broker unavailable')

        response = self.client.get(
            '/oauth/google/callback/?state=test-state&code=testcode'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Authorization succeeded, but background processing could not be started.',
        )
        donation.refresh_from_db()
        self.assertIn('broker unavailable', donation.processing_log)

    @patch('donations.views.process_donation')
    @patch.object(TikTokDonation, 'handle_auth_callback', return_value=(True, ''))
    def test_tiktok_callback_handles_queue_failure(self, mock_handle, mock_task):
        donation = TikTokDonation.objects.create(
            status='pending', oauth_state='test-state',
        )
        mock_task.delay.side_effect = RuntimeError('broker unavailable')

        response = self.client.get(
            '/oauth/tiktok/callback/?state=test-state&code=testcode'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Authorization succeeded, but background processing could not be started.',
        )
        donation.refresh_from_db()
        self.assertIn('broker unavailable', donation.processing_log)


@override_settings(ARCHIVE_MAX_AGE_SECONDS=3600)
class RemoveStaleArchivesTests(TestCase):
    """Archives left behind by a crash are collected; live ones are not."""

    def setUp(self):
        self.archive_dir = tempfile.mkdtemp(prefix="archives-")
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)
        patcher = override_settings(ARCHIVE_DIR=self.archive_dir)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _archive(self, name, age_seconds):
        path = os.path.join(self.archive_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"archive")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_removes_archives_past_the_maximum_age(self):
        stale = self._archive("stale.zip", 7200)
        self.assertEqual(remove_stale_archives(), 1)
        self.assertFalse(os.path.exists(stale))

    def test_keeps_archives_being_worked_on(self):
        fresh = self._archive("fresh.zip", 60)
        self.assertEqual(remove_stale_archives(), 0)
        self.assertTrue(os.path.exists(fresh))

    def test_missing_directory_is_not_an_error(self):
        with override_settings(ARCHIVE_DIR=os.path.join(self.archive_dir, "gone")):
            self.assertEqual(remove_stale_archives(), 0)


@override_settings(
    RETENTION_DAYS=14, CAN_DELETE_RETENTION_DAYS=2, RETENTION_WARNING_DAYS=3,
    ADMIN_EMAILS=['admin@aalto.fi'],
)
class ExpireDonationsTests(TestCase):
    """Two clocks: from arrival, and from the researcher's confirmation."""

    def _donation(self, received_days_ago=None, can_delete_days_ago=None):
        donation = GoogleDonation.objects.create()
        if received_days_ago is not None:
            donation.data_received_at = timezone.now() - timedelta(days=received_days_ago)
        if can_delete_days_ago is not None:
            donation.can_delete_at = timezone.now() - timedelta(days=can_delete_days_ago)
        donation.save()
        return donation

    def test_deletes_after_the_retention_period(self):
        donation = self._donation(received_days_ago=15)
        self.assertEqual(expire_donations(), 1)
        self.assertFalse(Donation.objects.filter(pk=donation.pk).exists())

    def test_keeps_donations_within_the_retention_period(self):
        donation = self._donation(received_days_ago=13)
        self.assertEqual(expire_donations(), 0)
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_confirmation_shortens_the_clock(self):
        donation = self._donation(received_days_ago=1, can_delete_days_ago=3)
        self.assertEqual(expire_donations(), 1)
        self.assertFalse(Donation.objects.filter(pk=donation.pk).exists())

    def test_confirmation_alone_does_not_delete_at_once(self):
        donation = self._donation(received_days_ago=1, can_delete_days_ago=0)
        self.assertEqual(expire_donations(), 0)
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_donation_without_data_is_left_alone(self):
        donation = GoogleDonation.objects.create()
        self.assertEqual(expire_donations(), 0)
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_reports_deletions_and_upcoming_expiry(self):
        self._donation(received_days_ago=15)
        self._donation(received_days_ago=12)  # due in 2 days, inside the warning
        expire_donations()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['admin@aalto.fi'])
        self.assertIn('Deleted:', message.body)
        self.assertIn('Expiring within 3 days:', message.body)

    def test_no_mail_when_nothing_happened(self):
        self._donation(received_days_ago=1)
        expire_donations()
        self.assertEqual(mail.outbox, [])


class RequeueHeuristicTests(TestCase):
    """A donation being processed is left alone while its archives are there."""

    def setUp(self):
        self.archive_dir = tempfile.mkdtemp(prefix="requeue-")
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)

    def _archive(self, name="export.zip"):
        path = os.path.join(self.archive_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"archive")
        return path

    def _donation(self, archives, status='processing'):
        donation = GoogleDonation.objects.create(status=status)
        donation.downloaded_files = archives
        donation.job_status = {'job-1': {'completed': True}}
        donation.save()
        return donation

    @patch('donations.tasks.process_donation.apply_async')
    def test_processing_with_archives_is_left_alone(self, queue):
        self._donation([self._archive()])
        check_pending_donations()
        queue.assert_not_called()

    @patch('donations.tasks.process_donation.apply_async')
    def test_processing_without_archives_is_queued_again(self, queue):
        donation = self._donation([os.path.join(self.archive_dir, "gone.zip")])
        check_pending_donations()
        queue.assert_called_once()

        donation.refresh_from_db()
        self.assertEqual(donation.downloaded_files, [], "missing archive is forgotten")
        self.assertNotIn(
            'completed', donation.job_status['job-1'],
            "the job is downloaded again",
        )

    @patch('donations.tasks.process_donation.apply_async')
    def test_donation_waiting_for_its_export_is_queued(self, queue):
        self._donation([])
        check_pending_donations()
        queue.assert_called_once()

    @patch('donations.tasks.process_donation.apply_async')
    def test_authorized_donation_is_queued(self, queue):
        GoogleDonation.objects.create(status='authorized')
        check_pending_donations()
        queue.assert_called_once()

    @patch('donations.tasks.process_donation.apply_async')
    def test_partly_read_export_is_left_alone(self, queue):
        # One archive consumed and deleted, the next still waiting.
        self._donation([os.path.join(self.archive_dir, "done.zip"), self._archive("next.zip")])
        check_pending_donations()
        queue.assert_not_called()


@override_settings(PROCESSING_CLAIM_TIMEOUT_SECONDS=1800)
class RequeueHeuristicTests(TestCase):
    """A claim, not a file, says whether a worker is still on the job."""

    def setUp(self):
        self.archive_dir = tempfile.mkdtemp(prefix="requeue-")
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)

    def _archive(self, name="export.zip"):
        path = os.path.join(self.archive_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"archive")
        return path

    def _donation(self, archives=(), claimed_minutes_ago=None, status='processing'):
        donation = GoogleDonation.objects.create(status=status)
        donation.downloaded_files = list(archives)
        donation.job_status = {'job-1': {'completed': True}}
        if claimed_minutes_ago is not None:
            donation.processing_claimed_at = timezone.now() - timedelta(minutes=claimed_minutes_ago)
        donation.save()
        return donation

    @patch('donations.tasks.process_donation.apply_async')
    def test_live_claim_is_left_alone_even_with_nothing_on_disk(self, queue):
        # A worker part-way through its first download looks exactly like this.
        self._donation(archives=[], claimed_minutes_ago=5)
        check_pending_donations()
        queue.assert_not_called()

    @patch('donations.tasks.process_donation.apply_async')
    def test_stale_claim_is_queued_again(self, queue):
        self._donation(archives=[], claimed_minutes_ago=90)
        check_pending_donations()
        queue.assert_called_once()

    @patch('donations.tasks.process_donation.apply_async')
    def test_unclaimed_processing_donation_is_queued(self, queue):
        self._donation(archives=[])
        check_pending_donations()
        queue.assert_called_once()

    @patch('donations.tasks.process_donation.apply_async')
    def test_missing_archives_are_forgotten_so_they_are_fetched_again(self, queue):
        donation = self._donation(archives=[os.path.join(self.archive_dir, "gone.zip")])
        check_pending_donations()

        donation.refresh_from_db()
        self.assertEqual(donation.downloaded_files, [])
        self.assertNotIn('completed', donation.job_status['job-1'])

    @patch('donations.tasks.process_donation.apply_async')
    def test_archives_still_on_disk_are_kept(self, queue):
        archive = self._archive()
        donation = self._donation(archives=[archive])
        check_pending_donations()

        donation.refresh_from_db()
        self.assertEqual(donation.downloaded_files, [archive])
        self.assertIn('completed', donation.job_status['job-1'])

    @patch('donations.tasks.process_donation.apply_async')
    def test_authorized_donation_is_queued(self, queue):
        GoogleDonation.objects.create(status='authorized')
        check_pending_donations()
        queue.assert_called_once()


class ProcessingClaimTests(TestCase):
    """The claim is taken while working and given up afterwards."""

    def test_claim_is_released_when_processing_finishes(self):
        donation = GoogleDonation.objects.create(status='authorized')
        with patch.object(GoogleDonation, '_process_data', _fake_process_data):
            process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertIsNone(donation.processing_claimed_at)

    def test_claim_is_released_when_processing_fails(self):
        donation = GoogleDonation.objects.create(status='authorized')
        with patch.object(GoogleDonation, '_process_data', side_effect=RuntimeError('boom')):
            process_donation(donation.pk)
        donation.refresh_from_db()
        self.assertIsNone(donation.processing_claimed_at)
        self.assertEqual(donation.status, 'error')

    def test_claim_goes_stale(self):
        donation = GoogleDonation.objects.create()
        donation.claim_processing()
        self.assertTrue(donation.claim_is_live())

        donation.processing_claimed_at = timezone.now() - timedelta(hours=2)
        self.assertFalse(donation.claim_is_live())
