"""Data models for managing donations and data downloads."""
import hashlib
import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.crypto import get_random_string


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

    # Subclasses override to pin URL building to a configured base URL
    # (so OAuth redirect_uri and the API-returned donation URL stay on
    # the domain registered with the OAuth provider, regardless of
    # which domain the request came in on).
    BASE_URL_SETTING = None

    def absolute_url(self, request, view_name, **kwargs):
        """Build an absolute URL for ``view_name``. Uses the subclass's
        configured base URL if set; otherwise falls back to ``request``."""
        path = reverse(view_name, kwargs=kwargs)
        base = getattr(settings, self.BASE_URL_SETTING, '') if self.BASE_URL_SETTING else ''
        if base:
            return base.rstrip('/') + path
        return request.build_absolute_uri(path)

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
