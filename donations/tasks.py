"""Celery tasks for asynchronous donation processing."""
import logging
import os
import time
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

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

    donation.claim_processing()
    try:
        donation._process_data()
    except Exception as exc:
        donation.release_processing()
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

    donation.release_processing()
    logger.info("Donation %s processing complete, status: %s.", donation_id, donation.status)


@shared_task
def check_pending_donations():
    """Periodic task: queue processing for donations that need it.

    Picks up newly authorized donations, and failed ones that have retries
    left. A donation already being processed is left alone while a worker's
    claim on it is live: the work can take hours, and mid-download there may
    be nothing on disk to show for it, so the claim is what separates work in
    progress from work abandoned. Once the claim goes stale, archives that are
    no longer on disk are forgotten so they will be fetched again.
    """
    needs_processing = Donation.objects.filter(
        status__in=('authorized', 'processing', 'error')
    )
    for donation in needs_processing:
        if donation.status == 'error' and donation.retry_count >= MAX_RETRIES:
            continue

        if donation.status == 'processing':
            if donation.claim_is_live():
                logger.debug("Donation %s is claimed by a worker; leaving it.", donation.pk)
                continue
            subclass = donation.get_subclass()
            if hasattr(subclass, 'forget_archives'):
                subclass.forget_archives()

        logger.info("Queueing donation %s (status=%s) for processing.", donation.pk, donation.status)
        process_donation.apply_async(args=[donation.pk], task_id=f'process-donation-{donation.pk}')


@shared_task
def remove_stale_archives():
    """Periodic task: delete archives no worker is reading any more.

    Archives are held unencrypted while they are processed and deleted as soon
    as they have been read, whether reading succeeded or failed. This collects
    what a crash or a reboot left behind. A worker touches an archive as it
    works through it, so an archive still being read keeps a recent
    modification time however long the reading takes.
    """
    directory = settings.ARCHIVE_DIR
    if not os.path.isdir(directory):
        return 0

    cutoff = time.time() - settings.ARCHIVE_MAX_AGE_SECONDS
    removed = 0
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path) or os.path.getmtime(path) > cutoff:
                continue
            os.remove(path)
        except OSError as exc:
            logger.warning("Could not remove stale archive %s: %s", path, exc)
            continue
        removed += 1
        logger.info("Removed stale archive %s", path)
    return removed


def _expiry(donation):
    """When a donation is due for deletion, or None if no clock has started."""
    due = []
    if donation.data_received_at:
        due.append(
            donation.data_received_at + timedelta(days=settings.RETENTION_DAYS)
        )
    if donation.can_delete_at:
        due.append(
            donation.can_delete_at + timedelta(days=settings.CAN_DELETE_RETENTION_DAYS)
        )
    return min(due) if due else None


def _delete_expired(donation):
    """Revoke the platform grant where there is one, then delete the donation."""
    subclass = donation.get_subclass()
    if hasattr(subclass, 'revoke'):
        try:
            subclass.revoke()
        except Exception:
            logger.exception("Donation %s could not be revoked before deletion", donation.pk)
    subclass.delete()


@shared_task
def expire_donations():
    """Periodic task: delete donations whose retention has run out.

    Two clocks run: one from the moment the data arrived, one from the moment
    a researcher confirmed they hold a verified copy. Whichever expires first
    decides. What was deleted, and what is about to be, goes to the
    administrators by mail.
    """
    now = timezone.now()
    warn_before = now + timedelta(days=settings.RETENTION_WARNING_DAYS)

    deleted, expiring = [], []
    for donation in Donation.objects.all():
        due = _expiry(donation)
        if due is None:
            continue
        if due <= now:
            deleted.append(f"donation {donation.pk} ({donation.source_type}), due {due:%Y-%m-%d %H:%M}")
            _delete_expired(donation)
        elif due <= warn_before:
            expiring.append(f"donation {donation.pk} ({donation.source_type}), due {due:%Y-%m-%d %H:%M}")

    if deleted or expiring:
        _report_expiry(deleted, expiring)
    logger.info("Expired %s donation(s); %s approaching expiry.", len(deleted), len(expiring))
    return len(deleted)


def _report_expiry(deleted, expiring):
    """Tell the administrators what went, and what is about to."""
    if not settings.ADMIN_EMAILS:
        logger.warning("No ADMIN_EMAILS configured; expiry report not sent.")
        return

    lines = []
    if deleted:
        lines.append("Deleted:")
        lines.extend(f"  {entry}" for entry in deleted)
    if expiring:
        if lines:
            lines.append("")
        lines.append(f"Expiring within {settings.RETENTION_WARNING_DAYS} days:")
        lines.extend(f"  {entry}" for entry in expiring)

    try:
        send_mail(
            subject=f"portability-server: {len(deleted)} donation(s) deleted, {len(expiring)} expiring",
            message="\n".join(lines) + "\n",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=settings.ADMIN_EMAILS,
            fail_silently=False,
        )
    except Exception:
        logger.exception("Could not send the expiry report")
