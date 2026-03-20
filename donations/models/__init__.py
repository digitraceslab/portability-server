"""Data models for managing donations and data downloads."""
import uuid

from django.db import models
from django.utils.crypto import get_random_string


class Donation(models.Model):
    """Track data donations with unique tokens."""
    participant_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
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
    processing_log = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_changed = models.BooleanField(default=False)

    def __str__(self):
        return f"Donation {self.pk} ({self.source_type}, {self.status})"


class ResearcherToken(models.Model):
    """API tokens with granular permissions."""

    key = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        """Auto-generate token key if not provided."""
        if not self.key:
            self.key = get_random_string(40)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or 'unnamed'


from donations.models.google_portability import GoogleDonation  # noqa: E402
from donations.models.tiktok_portability import TikTokDonation  # noqa: E402
