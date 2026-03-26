"""Celery tasks for asynchronous donation processing."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


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

    donation._process_data()
    donation.refresh_from_db()
    logger.info("Donation %s processing complete, status: %s.", donation_id, donation.status)


@shared_task
def check_pending_donations():
    """Periodic task: queue processing for donations that need it.

    Picks up:
    - authorized: newly authorized donations
    - error: failed donations that haven't exceeded retry limit
    - processing: donations stuck longer than the task time limit
    """
    from donations.models import Donation

    needs_processing = Donation.objects.filter(
        status__in=('authorized', 'processing', 'error')
    )
    for donation in needs_processing:
        if donation.status == 'error' and donation.retry_count >= MAX_RETRIES:
            continue
        logger.info("Queueing donation %s (status=%s) for processing.", donation.pk, donation.status)
        process_donation.delay(donation.pk)
