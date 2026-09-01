"""Per-step tests for the ingest and retrieval pipeline.

The round trip in ``tests_roundtrip`` shows that data survives the whole
pipeline; these show which step broke when it does not.
"""
import os
import shutil
import tempfile

import pandas as pd
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch

from donations.models import GoogleDonation, ResearcherToken, TikTokExportDonation
from donations.utils import crypto, parquet_store

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

COLUMNS = ["timestamp", "activity"]


def _frame(start, rows):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=rows, freq="h"),
        "activity": ["still"] * rows,
    })


def _reader(path):
    """Claim any part whose columns match the one data type used here."""
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    if set(frame.columns) != set(COLUMNS):
        return None
    return frame


class StepTestCase(TestCase):
    """Runs in a temporary working directory, since data paths are relative."""

    def setUp(self):
        self._previous_cwd = os.getcwd()
        self._workdir = tempfile.mkdtemp(prefix="steps-")
        os.chdir(self._workdir)
        self.addCleanup(shutil.rmtree, self._workdir, ignore_errors=True)
        self.addCleanup(os.chdir, self._previous_cwd)

    def _store_archive(self, name, frame):
        """Write a frame where a downloaded archive would be stored."""
        path = os.path.join(self._workdir, name)
        with open(path, "wb") as handle:
            handle.write(frame.to_csv(index=False).encode())
        return path


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class TestEncryptionStep(StepTestCase):
    def test_file_round_trips_through_encryption(self):
        payload = b"timestamp,activity\n2024-01-01 00:00:00,still\n"
        path = os.path.join(self._workdir, "part")
        crypto.write_encrypted_bytes(path, payload)

        with open(path, "rb") as handle:
            self.assertNotIn(b"timestamp", handle.read(), "stored file is not plaintext")

        temp = crypto.decrypt_file_to_temp(path)
        try:
            with open(temp, "rb") as handle:
                self.assertEqual(handle.read(), payload)
        finally:
            os.remove(temp)

    def test_wrong_key_cannot_read(self):
        path = os.path.join(self._workdir, "part")
        crypto.write_encrypted_bytes(path, b"secret")
        with override_settings(ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(Exception):
                crypto.decrypt_file_to_temp(path)


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class TestExtractionStep(StepTestCase):
    def setUp(self):
        super().setUp()
        self.researcher = ResearcherToken.objects.create(name="steps")
        self.donation = GoogleDonation.objects.create(
            researcher=self.researcher, requested_data_types=["activity_log"],
        )
        self.donation.processing_status = "processing"
        self._original_readers = GoogleDonation.DATA_TYPE_READERS
        GoogleDonation.DATA_TYPE_READERS = {"activity_log": _reader}
        self.addCleanup(
            setattr, GoogleDonation, "DATA_TYPE_READERS", self._original_readers
        )

    def _extract(self, *frames):
        for index, frame in enumerate(frames):
            self.donation.downloaded_files.append(
                self._store_archive(f"part-{index}", frame)
            )
        self.donation.save()
        self.donation.extract_and_process()
        self.donation.refresh_from_db()

    def test_single_archive_produces_the_data_type(self):
        self._extract(_frame("2024-01-01", 3))
        self.assertEqual(self.donation.get_data_types(), ["activity_log"])
        self.assertEqual(self.donation.count_rows("activity_log"), 3)

    def test_archive_is_marked_processed(self):
        self._extract(_frame("2024-01-01", 3))
        self.assertTrue(all(
            status.get("processed") for status in self.donation.file_status.values()
        ))

    def test_unrequested_data_types_are_not_stored(self):
        self.donation.requested_data_types = []
        self._extract(_frame("2024-01-01", 3))
        self.assertEqual(self.donation.get_data_types(), [])

    def test_data_type_split_across_archives_keeps_every_row(self):
        """A type continued in a second archive keeps the rows from both."""
        self._extract(_frame("2024-01-01", 3), _frame("2024-02-01", 4))
        self.assertEqual(self.donation.count_rows("activity_log"), 7)


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class TestReadStep(StepTestCase):
    def setUp(self):
        super().setUp()
        self.researcher = ResearcherToken.objects.create(name="steps-read")
        self.donation = GoogleDonation.objects.create(
            researcher=self.researcher, requested_data_types=["activity_log"],
        )
        self.donation.processing_status = "processed"
        self.donation.data_type_status = {"activity_log": {"received": True}}
        self.donation.save()
        path = self.donation._combined_path("activity_log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        parquet_store.write_frames(path, [_frame("2024-01-01", 5)])

    def test_counts_and_pages_agree(self):
        total = self.donation.count_rows("activity_log")
        rows = self.donation.fetch_data("activity_log", limit=total, offset=0)
        self.assertEqual(len(rows), total)

    def test_unknown_data_type_is_empty(self):
        self.assertEqual(self.donation.fetch_data("not_a_type"), [])
        self.assertEqual(self.donation.count_rows("not_a_type"), 0)

    def test_filter_matching_nothing_is_empty(self):
        rows = self.donation.fetch_data(
            "activity_log", start_date="2030-01-01", end_date="2030-12-31",
        )
        self.assertEqual(rows, [])

    def test_timestamps_are_milliseconds(self):
        rows = self.donation.fetch_data("activity_log", limit=1)
        expected = int(pd.Timestamp("2024-01-01").value // 10**6)
        self.assertEqual(rows[0]["timestamp"], expected)


@override_settings(ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class TestUploadStep(StepTestCase):
    """An upload needs no authorization step, so it is ready to read at once."""

    def setUp(self):
        super().setUp()
        self.donation = TikTokExportDonation.objects.create()

    def test_upload_marks_the_donation_as_processing(self):
        self.assertEqual(self.donation.processing_status, "waiting")
        upload = SimpleUploadedFile("export.csv", _frame("2024-01-01", 2).to_csv(index=False).encode())
        ok, _message = self.donation.handle_file_upload(upload)
        self.assertTrue(ok)
        self.assertEqual(self.donation.processing_status, "processing")

    def test_extraction_waits_until_something_is_uploaded(self):
        self.donation.extract_and_process()
        self.assertEqual(self.donation.processing_status, "waiting")

    def test_extraction_retries_after_an_error(self):
        self.donation.requested_data_types = ["watch_history"]
        self.donation.uploaded_files = [self._store_archive("upload", _frame("2024-01-01", 2))]
        self.donation.processing_status = "error"
        self.donation.save()

        with patch.object(TikTokExportDonation, "DATA_TYPE_READERS", {"watch_history": _reader}):
            self.donation.extract_and_process()

        self.assertEqual(self.donation.processing_status, "processed")

    def test_uploaded_archive_is_plain_and_removed_after_reading(self):
        self.donation.requested_data_types = ["watch_history"]
        upload = SimpleUploadedFile("export.csv", _frame("2024-01-01", 2).to_csv(index=False).encode())
        self.donation.handle_file_upload(upload)
        stored = self.donation.uploaded_files[0]

        with open(stored, "rb") as handle:
            self.assertIn(b"timestamp", handle.read(), "archive is held as it arrived")

        with patch.object(TikTokExportDonation, "DATA_TYPE_READERS", {"watch_history": _reader}):
            self.donation.extract_and_process()
        self.assertFalse(os.path.exists(stored), "archive is removed once read")

    def test_archive_is_removed_even_when_reading_fails(self):
        self.donation.requested_data_types = ["watch_history"]
        self.donation.uploaded_files = [self._store_archive("upload", _frame("2024-01-01", 2))]
        self.donation.processing_status = "processing"
        self.donation.save()

        def explode(path):
            raise ValueError("unreadable")

        with patch.object(TikTokExportDonation, "DATA_TYPE_READERS", {"watch_history": explode}):
            self.donation.extract_and_process()
        self.assertFalse(os.path.exists(self.donation.uploaded_files[0]))
