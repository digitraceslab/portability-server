""" Donation model for data exported from TikTok. Participant provided the donation by uploading a file downloaded from TikTok. """
import os
import secrets
from datetime import timedelta
import pandas as pd
from django.conf import settings
from django.db import models
from urllib.parse import urlencode
import donations.utils.crypto as crypto
from django.utils import timezone
from donations.utils.virus_scan import scan_bytes, scan_path


from donations.models import Donation
from donations.models.parquet_storage import ParquetStorageMixin


def watch_history_dummy_reader(file_path):
    """Dummy reader for watch history data type."""
    return [
        {
            'video_id': '12345',
            'watched_at': '2024-01-01T12:00:00Z',
            'duration_watched': 30,
        },
        {
            'video_id': '67890',
            'watched_at': '2024-01-02T15:30:00Z',
            'duration_watched': 45,
        }
    ]


class TikTokExportDonation(ParquetStorageMixin, Donation):
    source_type_display = "TikTok Export"

    PROCESSING_STATUS_CHOICES = (
        ('authorized', 'Authorized, waiting for upload'),
        ('processing', 'Processing'),
        ('processed', 'Processed successfully'),
        ('error', 'Error during processing'),
    )

    storage_name = 'tiktok_export'
    archive_field = 'uploaded_files'

    @property
    def type(self):
        """TikTok Export uses file upload instead of OAuth."""
        return 'upload'

    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='authorized',
    )
    uploaded_files = models.JSONField(default=list, blank=True)
    file_status = models.JSONField(
        default=dict, blank=True,
        help_text="Maps filepath to {'processed': bool, 'processed_at': timestamp}",
    )
    data_type_status = models.JSONField(
        default=dict, blank=True,
        help_text="Maps data_type to {'received': bool, 'received_at': timestamp}",
    )

    EXPECTED_DATA_TYPES = [
        'watch_history',
    ]

    DATA_TYPE_READERS = {
        'watch_history':  watch_history_dummy_reader,
    }

    def save(self, *args, **kwargs):
        if not self.source_type:
            self.source_type = 'tiktok_export'
        super().save(*args, **kwargs)

    def get_data_types(self):
        if self.processing_status not in ['processed', 'processing', 'error']:
            return []
        return [
            dt for dt, status in self.data_type_status.items()
            if status.get('received')
        ]

    def extract_and_process(self):
        # Unlike the OAuth sources, an upload is ready to read as soon as it
        # has been stored, so 'processing' and 'error' are not entry states.
        if self.processing_status in ['processing', 'error']:
            return
        try:
            if self._read_archives():
                self._combine_archives()
                missing = [
                    dt for dt in self._expected_data_types()
                    if not (self.data_type_status or {}).get(dt, {}).get('received')
                ]
                if missing:
                    self.processing_log += f"Missing data types after all files processed: {missing}\n"
                    self.processing_status = 'error'
                else:
                    self.processing_status = 'processed'
                    self.status = 'processed'
                self.save()
        except Exception as e:
            self.processing_log = f"Unexpected error during processing: {str(e)}\n"
            self.processing_status = 'error'
            self.save()


    def handle_file_upload(self, file):
        if file.size > settings.UPLOAD_MAX_BYTES:
            max_gb = settings.UPLOAD_MAX_BYTES / (1024 ** 3)
            return False, f"File is too large (maximum {max_gb:.0f} GB)."
        filename = file.name
        unique_suffix = secrets.token_hex(8)
        stored_filename = f"{self.pk}_{unique_suffix}_{filename}"
        stored_path = os.path.join('data', stored_filename)
        if not os.path.exists('data'):
            os.makedirs('data')
        if hasattr(file, "temporary_file_path"):
            clean, detail = scan_path(file.temporary_file_path())
            plaintext = None
        else:
            plaintext = b''.join(file.chunks())
            clean, detail = scan_bytes(plaintext)
        if not clean:
            self.processing_log += f"Upload rejected by virus scan: {detail}\n"
            self.save()
            return False, "File failed the security scan and was rejected."
        if plaintext is None:
            plaintext = b''.join(file.chunks())
        crypto.write_encrypted_bytes(stored_path, plaintext)
        self.uploaded_files.append(stored_path)
        self.save()
        return True, "File uploaded successfully"

    def _process_data(self):
        if self.uploaded_files:
            self.extract_and_process()

    def cleanup_files(self):
        for filepath in self.uploaded_files:
            if os.path.exists(filepath):
                os.remove(filepath)
        self.uploaded_files = []
        self.save()
