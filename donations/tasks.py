"""Celery tasks for asynchronous donation processing."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _resolve_donation(donation):
    """Resolve a base Donation to its specific subclass."""
    from donations.models import GoogleDonation, TikTokDonation
    try:
        return donation.googledonation
    except GoogleDonation.DoesNotExist:
        pass
    try:
        return donation.tiktokdonation
    except TikTokDonation.DoesNotExist:
        pass
    return donation


@shared_task
def process_donation(donation_id):
    """Process a single donation: download data and run extract+process pipeline."""
    from donations.models import Donation

    donation = Donation.objects.get(pk=donation_id)
    donation = _resolve_donation(donation)

    if donation.status != 'authorized':
        logger.info("Donation %s has status '%s', skipping.", donation_id, donation.status)
        return

    donation.status = 'processing'
    donation.save()

    try:
        donation._process_data()
        donation.status = 'processed'
        donation.save()
    except Exception as e:
        donation.status = 'error'
        donation.processing_log += f"Task error: {e}\n"
        donation.save()
        raise


@shared_task
def check_pending_donations():
    """Periodic task: queue processing for any authorized donations."""
    from donations.models import Donation

    authorized = Donation.objects.filter(status='authorized')
    for donation in authorized:
        process_donation.delay(donation.pk)
