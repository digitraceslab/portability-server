"""Celery tasks for asynchronous donation processing."""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
PROCESSING_TIMEOUT = timedelta(seconds=getattr(settings, 'CELERY_TASK_TIME_LIMIT', 1800))


@shared_task
def process_donation(donation_id):
    """Process a single donation: download data and run extract+process pipeline.

    Called by check_pending_donations (periodic) and OAuth callbacks (immediate).
    Skips donations that are already processed or have exceeded retry limit.
    """
    from donations.models import Donation

    donation = Donation.objects.get(pk=donation_id)
    donation = donation.get_subclass()

    if donation.status in ('processed', 'pending'):
        logger.info("Donation %s has status '%s', skipping.", donation_id, donation.status)
        return

    if donation.status == 'error' and donation.retry_count >= MAX_RETRIES:
        logger.error(
            "Donation %s has failed %d times, needs developer attention.",
            donation_id, donation.retry_count,
        )
        return

    donation.status = 'processing'
    donation.save()

    try:
        donation._process_data()
        donation.status = 'processed'
        donation.retry_count = 0
        donation.save()
        logger.info("Donation %s processed successfully.", donation_id)
    except Exception as e:
        donation.refresh_from_db()
        donation.status = 'error'
        donation.retry_count += 1
        donation.processing_log += f"Processing error (attempt {donation.retry_count}): {e}\n"
        try:
            donation.save()
        except Exception as save_err:
            logger.error(
                "Donation %s: failed to save error status: %s (original error: %s)",
                donation_id, save_err, e,
            )
        if donation.retry_count >= MAX_RETRIES:
            logger.error(
                "Donation %s: failed %d times, giving up. Needs developer attention: %s",
                donation_id, donation.retry_count, e,
            )
        else:
            logger.warning(
                "Donation %s: attempt %d failed: %s. Will retry on next beat cycle.",
                donation_id, donation.retry_count, e,
            )


@shared_task
def check_pending_donations():
    """Periodic task: queue processing for donations that need it.

    Picks up:
    - authorized: newly authorized donations
    - error: failed donations that haven't exceeded retry limit
    - processing: donations stuck longer than the task time limit
    """
    from donations.models import Donation

    needs_processing = Donation.objects.filter(status__in=('authorized', 'error'))
    for donation in needs_processing:
        if donation.status == 'error' and donation.retry_count >= MAX_RETRIES:
            continue
        logger.info("Queueing donation %s (status=%s) for processing.", donation.pk, donation.status)
        process_donation.delay(donation.pk)

    cutoff = timezone.now() - PROCESSING_TIMEOUT
    stuck = Donation.objects.filter(status='processing', created_at__lt=cutoff)
    for donation in stuck:
        logger.warning("Donation %s stuck in 'processing', re-queueing.", donation.pk)
        process_donation.delay(donation.pk)
