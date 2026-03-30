"""Celery tasks for asynchronous donation processing."""
import logging
from donations.models import Donation
from celery import shared_task

logger = logging.getLogger(__name__)

MAX_RETRIES = 10


@shared_task
def process_donation(donation_id):
    """Process a single donation: download data and run extract+process pipeline.

    Called by check_pending_donations (periodic) and OAuth callbacks (immediate).
    Skips donations that are already processed or have exceeded retry limit.
    """
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

    try:
        donation._process_data()
    except Exception as exc:
        attempt = donation.retry_count + 1
        donation.retry_count = attempt
        donation.status = 'error'
        donation.processing_log = (donation.processing_log or '') + (
            f"\nattempt {attempt}: {exc}"
        )
        try:
            donation.save(update_fields=['status', 'retry_count', 'processing_log'])
        except Exception as save_exc:
            logger.error(
                "Donation %s failed to save error status: %s", donation_id, save_exc
            )
        return

    logger.info("Donation %s processing complete, status: %s.", donation_id, donation.status)


@shared_task
def check_pending_donations():
    """Periodic task: queue processing for donations that need it.

    Picks up:
    - authorized: newly authorized donations
    - error: failed donations that haven't exceeded retry limit
    - processing: donations stuck longer than the task time limit
    """
    needs_processing = Donation.objects.filter(
        status__in=('authorized', 'processing', 'error')
    )
    for donation in needs_processing:
        if donation.status == 'error' and donation.retry_count >= MAX_RETRIES:
            continue
        logger.info("Queueing donation %s (status=%s) for processing.", donation.pk, donation.status)
        process_donation.apply_async(args=[donation.pk], task_id=f'process-donation-{donation.pk}')
