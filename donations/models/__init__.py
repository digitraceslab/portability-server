"""Data models for managing donations and data downloads."""
import hashlib
import uuid

from django.db import models
from django.utils.crypto import get_random_string


class Participant(models.Model):
    """Persistent participant identity across donations."""
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.token)


class Donation(models.Model):
    """Track data donations with unique tokens."""
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    participant = models.ForeignKey('Participant', on_delete=models.SET_NULL, null=True, blank=True, related_name='donations')
    suggested_participant_token = models.UUIDField(default=uuid.uuid4)
    researcher = models.ForeignKey('ResearcherToken', on_delete=models.CASCADE, related_name='donations', null=True, blank=True)
    source_type = models.CharField(max_length=50)
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
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_changed = models.BooleanField(default=False)

    def __str__(self):
        return f"Donation {self.pk} ({self.source_type}, {self.status})"

    def get_subclass(self):
        """Return the most specific subclass instance (e.g. GoogleDonation)."""
        from donations.models.google_portability import GoogleDonation
        from donations.models.tiktok_portability import TikTokDonation
        try:
            return self.googledonation
        except GoogleDonation.DoesNotExist:
            pass
        try:
            return self.tiktokdonation
        except TikTokDonation.DoesNotExist:
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


from donations.models.google_portability import GoogleDonation  # noqa: E402
from donations.models.tiktok_portability import TikTokDonation  # noqa: E402

__all__ = ['Participant', 'Donation', 'ResearcherToken', 'GoogleDonation', 'TikTokDonation']
