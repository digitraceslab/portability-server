"""Data models for managing donations and data downloads."""
import hashlib
import logging
import uuid

from django.db import models
from django.utils.crypto import get_random_string

logger = logging.getLogger(__name__)


def hash_token(raw):
    """SHA-256 hash a raw UUID/string token for storage."""
    return hashlib.sha256(str(raw).encode()).hexdigest()


class Participant(models.Model):
    """Persistent participant identity across donations."""
    token = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.token:
            raw = uuid.uuid4()
            self._raw_token = str(raw)
            self.token = hash_token(raw)
        super().save(*args, **kwargs)

    @staticmethod
    def hash_token(raw):
        return hash_token(raw)

    @classmethod
    def get_by_raw_token(cls, raw):
        try:
            return cls.objects.get(token=hash_token(raw))
        except cls.DoesNotExist:
            return None

    def regenerate_token(self):
        """Generate a fresh token. Returns the raw UUID; cannot be recovered later."""
        raw = uuid.uuid4()
        self._raw_token = str(raw)
        self.token = hash_token(raw)
        self.save()
        return self._raw_token

    def __str__(self):
        return str(self.token)


class Donation(models.Model):
    """Track data donations with unique tokens."""
    token = models.CharField(max_length=64, unique=True, editable=False)
    participant = models.ForeignKey('Participant', on_delete=models.SET_NULL, null=True, blank=True, related_name='donations')
    suggested_participant_token = models.UUIDField(default=uuid.uuid4)
    researcher = models.ForeignKey('ResearcherToken', on_delete=models.CASCADE, related_name='donations', null=True, blank=True)
    source_type = models.CharField(max_length=50)

    @property
    def source_type_display(self):
        return self.source_type
    
    @property
    def type(self):
        """Returns 'oauth' for OAuth-based donations, 'upload' for file upload donations."""
        return 'oauth'  # Default for OAuth-based donations
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('authorized', 'Authorized'),
            ('processing', 'Processing'),
            ('processed', 'Processed'),
            ('error', 'Error'),
        ],
        default='pending',
    )
    data_start_date = models.DateField(null=True, blank=True)
    data_end_date = models.DateField(null=True, blank=True)
    requested_data_types = models.JSONField(default=list, blank=True)
    processing_log = models.TextField(blank=True, default='')
    retry_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    #: When the donated data arrived, and so when its retention starts.
    data_received_at = models.DateTimeField(null=True, blank=True)
    #: When the researcher confirmed they hold a verified copy. Starts a
    #: shorter clock: the data may be released, though not necessarily at once.
    can_delete_at = models.DateTimeField(null=True, blank=True)
    #: Refreshed by the worker while it is processing this donation. Work can
    #: take hours and can leave nothing on disk to show for it mid-download,
    #: so this is what separates work in progress from work abandoned.
    processing_claimed_at = models.DateTimeField(null=True, blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_changed = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.token:
            raw = uuid.uuid4()
            self._raw_token = str(raw)
            self.token = hash_token(raw)
        super().save(*args, **kwargs)

    @staticmethod
    def hash_token(raw):
        return hash_token(raw)

    @classmethod
    def get_by_raw_token(cls, raw):
        try:
            return cls.objects.get(token=hash_token(raw))
        except cls.DoesNotExist:
            return None

    def regenerate_token(self):
        """Generate a fresh token. Returns the raw UUID; cannot be recovered later."""
        raw = uuid.uuid4()
        self._raw_token = str(raw)
        self.token = hash_token(raw)
        self.save()
        return self._raw_token

    def __str__(self):
        return f"Donation {self.pk} ({self.source_type}, {self.status})"

    def _record_claim(self, claimed_at):
        """Store the claim, tolerating a failure to do so.

        The claim only tells the periodic check whether to leave this donation
        alone. Failing to record it risks duplicated work, which is recoverable;
        letting the failure escape would abandon work already under way, which
        is not.
        """
        self.processing_claimed_at = claimed_at
        try:
            self.save(update_fields=['processing_claimed_at'])
        except Exception:
            logger.warning("Could not record the processing claim on donation %s", self.pk)

    def claim_processing(self):
        """Mark this donation as being worked on, or refresh the mark."""
        from django.utils import timezone

        self._record_claim(timezone.now())

    def release_processing(self):
        """Give up the claim, so the donation can be picked up again."""
        if self.processing_claimed_at is None:
            return
        self._record_claim(None)

    def claim_is_live(self):
        """Whether a worker is still saying it is working on this donation."""
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone

        if self.processing_claimed_at is None:
            return False
        age = timezone.now() - self.processing_claimed_at
        return age < timedelta(seconds=settings.PROCESSING_CLAIM_TIMEOUT_SECONDS)

    def get_subclass(self):
        """Return the most specific subclass instance (e.g. GoogleDonation)."""
        from donations.models.google_portability import GoogleDonation
        from donations.models.tiktok_portability import TikTokDonation
        from donations.models.tiktok_export import TikTokExportDonation
        try:
            return self.googledonation
        except GoogleDonation.DoesNotExist:
            pass
        try:
            return self.tiktokdonation
        except TikTokDonation.DoesNotExist:
            pass
        try:
            return self.tiktokexportdonation
        except TikTokExportDonation.DoesNotExist:
            pass
        return self


class ResearcherToken(models.Model):
    """API tokens with granular permissions."""

    key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        """Auto-generate token key and store its SHA-256 hash."""
        if not self.key:
            raw_key = get_random_string(40)
            self._raw_key = raw_key
            self.key = hashlib.sha256(raw_key.encode()).hexdigest()
        super().save(*args, **kwargs)

    @staticmethod
    def hash_key(raw_key):
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def regenerate_key(self):
        """Generate a new token key, replacing the old one. Returns the raw key."""
        raw_key = get_random_string(40)
        self.key = hashlib.sha256(raw_key.encode()).hexdigest()
        self.save()
        return raw_key

    def __str__(self):
        return self.name or 'unnamed'


from donations.models.google_portability import GoogleDonation
from donations.models.tiktok_portability import TikTokDonation
from donations.models.tiktok_export import TikTokExportDonation

__all__ = ['Participant', 'Donation', 'ResearcherToken', 'GoogleDonation', 'TikTokDonation', 'TikTokExportDonation']
