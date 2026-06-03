""" Donation model for data exported from TikTok. Participant provided the donation by uploading a file downloaded from TikTok. """
import os
import secrets
from datetime import timedelta
import pandas as pd
from django.db import models
from urllib.parse import urlencode
import donations.utils.crypto as crypto
from django.utils import timezone


from donations.models import Donation


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


class TikTokExportDonation(Donation):
    source_type_display = "TikTok Export"

    @property
    def type(self):
        """TikTok Export uses file upload instead of OAuth."""
        return 'upload'

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
    
    def _csv_path(self, data_type):
        return f'data/{self.pk}/tiktok_export/{data_type}.csv'


    def fetch_data(self, data_type, limit=1000, start_date=None, end_date=None, offset=0):
        if data_type not in self.get_data_types():
            return []
        csv_path = self._csv_path(data_type)
        if not os.path.exists(csv_path):
            return []
        try:
            tmp = crypto.decrypt_file_to_temp(csv_path)
            try:
                df = pd.read_csv(tmp, parse_dates=['timestamp'])
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            if start_date:
                df = df[df['timestamp'] >= pd.Timestamp(start_date)]
            if end_date:
                df = df[df['timestamp'] <= pd.Timestamp(end_date)]
            df['timestamp'] = df['timestamp'].astype('int64') // 10**6
            start = int(offset) if offset else 0
            end = start + int(limit) if limit else None
            return df.iloc[start:end].to_dict(orient='records')
        except Exception as e:
            self.processing_log = f"Error fetching data for {data_type}: {str(e)}"
            self.save()
            return []
        
    def count_rows(self, data_type, start_date=None, end_date=None):
        if data_type not in self.get_data_types():
            return 0
        csv_path = self._csv_path(data_type)
        if not os.path.exists(csv_path):
            return 0
        try:
            tmp = crypto.decrypt_file_to_temp(csv_path)
            try:
                df = pd.read_csv(tmp, parse_dates=['timestamp'])
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            if start_date:
                df = df[df['timestamp'] >= pd.Timestamp(start_date)]
            if end_date:
                df = df[df['timestamp'] <= pd.Timestamp(end_date)]
            return len(df)
        except Exception as e:
            self.processing_log = f"Error counting rows for {data_type}: {str(e)}"
            self.save()
            return 0
        
    def extract_and_process(self):
        if self.processing_status in ['processing', 'error']:
            return
        
        try:
            if not os.path.exists('data'):
                os.makedirs('data')
            
            file_status = self.file_status or {}
            data_type_status = self.data_type_status or {}

            for filepath in self.uploaded_files:
                if file_status.get(filepath, {}).get('processed'):
                    continue
                if not os.path.exists(filepath):
                    self.processing_log = f"File not found: {filepath}"
                    file_status[filepath] = {'processed': True, 'skipped': True}
                    continue

                try:
                    tmp_fp = crypto.decrypt_file_to_temp(filepath)
                except Exception as e:
                    self.processing_log = f"Failed to decrypt {filepath}: {e}\n"
                    continue
            
                try:
                    for data_type, reader in self.DATA_TYPE_READERS.items():
                        if data_type not in self.requested_data_types:
                            continue
                        if data_type_status.get(data_type, {}).get('received'):
                            continue
                        try:
                            df = reader(tmp_fp)
                            if df is None or df.empty:
                                continue
                            df = df.reset_index()
                            df["device_id"] = self.pk
                            csv_path = self._csv_path(data_type)
                            existing_df = pd.DataFrame()
                            if os.path.exists(csv_path):
                                try:
                                    tmp_csv = crypto.decrypt_file_to_temp(csv_path)
                                    try:
                                        existing_df = pd.read_csv(tmp_csv)
                                    finally:
                                        try:
                                            os.remove(tmp_csv)
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            combined = pd.concat([existing_df, df], ignore_index=True)
                            crypto.write_encrypted_bytes(
                                csv_path, combined.to_csv(index=False).encode()
                            )
                            data_type_status[data_type] = {
                                'received': True,
                                'received_at': timezone.now().isoformat(),
                            }
                            self.processing_log = f"Received {data_type} from {filepath}\n"
                        except NotImplementedError:
                            pass
                        except Exception as e:
                            self.processing_log = f"Failed to read {data_type} from {filepath}: {e}\n"
                finally:
                    try:
                        os.remove(tmp_fp)
                    except Exception:
                        pass
            
                file_status[filepath] = {
                    'processed': True,
                    'processed_at': timezone.now().isoformat(),
                }
                self.file_status = file_status
                self.data_type_status = data_type_status
                self.save()

            all_files_done = all(
                filepath in file_status for filepath in self.uploaded_files
            )
            if all_files_done:
                expected = [
                    df for dt in self.requested_data_types
                    if dt in self.DATA_TYPE_READERS
                ]
                missing = [
                    dt for dt in expected
                    if not data_type_status.get(dt, {}).get('received')
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
        filename = file.name
        unique_suffix = secrets.token_hex(8)
        stored_filename = f"{self.pk}_{unique_suffix}_{filename}"
        stored_path = os.path.join('data', stored_filename)
        if not os.path.exists('data'):
            os.makedirs('data')
        with open(stored_path, 'wb') as f:
            for chunk in file.chunks():
                f.write(chunk)
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




