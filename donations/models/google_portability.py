"""Google Portability data source model and processing logic."""
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import pandas as pd
import requests
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from niimpy.reading.google_portability import (
    discover as np_read_discover,
    google_lens_history as np_read_google_lens,
    google_play_games_history as np_read_google_play_games,
    google_play_store_history as np_read_google_play_store,
    image_search_history as np_read_image_search,
    search_history as np_read_search,
    video_search_history as np_read_video_search,
    youtube_history as np_youtube_history,
)

from donations.models import Donation
import donations.utils.crypto as crypto


class GoogleDonation(Donation):
    PROCESSING_STATUS_CHOICES = (
        ('authorized', 'Authorized, waiting for download'),
        ('processing', 'Processing'),
        ('processed', 'Processed successfully'),
        ('error', 'Error during processing'),
    )

    access_token = models.CharField(max_length=500, blank=True, null=True)
    refresh_token = models.CharField(max_length=500, blank=True, null=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    google_user_id = models.CharField(max_length=255, blank=True, unique=True, null=True)
    oauth_state = models.CharField(max_length=100, blank=True, null=True)
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='authorized',
    )

    downloaded_files = models.JSONField(default=list, blank=True)
    data_job_ids = models.JSONField(default=dict, blank=True)
    job_status = models.JSONField(
        default=dict, blank=True,
        help_text="Maps job_id to {'completed': bool, 'downloaded_at': timestamp, 'state': job_state}",
    )
    file_status = models.JSONField(
        default=dict, blank=True,
        help_text="Maps filepath to {'processed': bool, 'processed_at': timestamp}",
    )
    data_type_status = models.JSONField(
        default=dict, blank=True,
        help_text="Maps data_type to {'received': bool, 'received_at': timestamp}",
    )

    EXPECTED_DATA_TYPES = [
        'youtube_history',
        'discover',
        'google_lens',
        'google_play_games',
        'google_play_store',
        'image_search',
        'search',
        'video_search',
    ]

    DATA_TYPE_READERS = {
        'youtube_history': np_youtube_history,
        'discover': np_read_discover,
        'google_lens': np_read_google_lens,
        'google_play_games': np_read_google_play_games,
        'google_play_store': np_read_google_play_store,
        'image_search': np_read_image_search,
        'search': np_read_search,
        'video_search': np_read_video_search,
    }

    DATA_TYPE_SCOPE_MAP = {
        'discover': {
            'scopes': ['discover.likes', 'discover.follows', 'discover.not_interested'],
            'resources': ['discover.likes', 'discover.follows', 'discover.not_interested'],
        },
        'youtube_history': {
            'scopes': ['myactivity.youtube'],
            'resources': ['myactivity.youtube'],
        },
        'search': {
            'scopes': ['myactivity.search'],
            'resources': ['myactivity.search'],
        },
        'google_play_games': {
            'scopes': ['myactivity.play'],
            'resources': ['myactivity.play'],
        },
        'google_play_store': {
            'scopes': ['myactivity.play'],
            'resources': ['myactivity.play'],
        },
        'image_search': {
            'scopes': ['myactivity.search'],
            'resources': ['myactivity.search'],
        },
        'video_search': {
            'scopes': ['myactivity.search'],
            'resources': ['myactivity.search'],
        },
        'google_lens': {
            'scopes': ['chrome.history'],
            'resources': ['chrome.history'],
        },
    }

    def _get_scopes_and_resources(self):
        """Return (scopes, resources) filtered by requested_data_types. Empty means all."""
        scope_prefix = 'https://www.googleapis.com/auth/dataportability.'
        if not self.requested_data_types:
            types = self.EXPECTED_DATA_TYPES
        else:
            types = self.requested_data_types
        scopes = []
        resources = []
        for dt in types:
            mapping = self.DATA_TYPE_SCOPE_MAP.get(dt)
            if mapping:
                for s in mapping['scopes']:
                    if s not in scopes:
                        scopes.append(s)
                for r in mapping['resources']:
                    if r not in resources:
                        resources.append(r)
        return (
            [scope_prefix + s for s in scopes],
            resources,
        )

    def save(self, *args, **kwargs):
        if not self.source_type:
            self.source_type = 'google_portability'
        super().save(*args, **kwargs)

    def get_data_types(self):
        if self.processing_status not in ('processed', 'processing', 'error'):
            return []
        return [
            dt for dt in self.EXPECTED_DATA_TYPES
            if self.data_type_status.get(dt, {}).get('received')
        ]

    def _csv_path(self, data_type):
        return f'data/{self.pk}/google_portability/{data_type}_processed.csv'

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
            end = start + int(limit) if limit is not None else None
            return df.iloc[start:end].to_dict('records')
        except Exception as e:
            self.processing_log += f"Error fetching {data_type} data: {e}\n"
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
            self.processing_log += f"Error counting {data_type} data: {e}\n"
            self.save()
            return 0

    def get_auth_url(self, request):
        state_token = secrets.token_urlsafe(16)
        self.oauth_state = state_token
        self.save()

        redirect_url = request.build_absolute_uri(
            reverse('google-auth-callback')
        )

        scopes, _ = self._get_scopes_and_resources()
        params = {
            'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
            'redirect_uri': redirect_url,
            'response_type': 'code',
            'scope': ' '.join(scopes),
            'access_type': 'offline',
            'state': state_token,
            'prompt': 'consent',
        }
        return f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"

    def create_archive_job(self):
        api_url = 'https://dataportability.googleapis.com/v1/portabilityArchive:initiate'
        access_token = None
        if self.access_token:
            access_token = crypto.decrypt_text(self.access_token)
        headers = {'Authorization': f"Bearer {access_token}"}
        _, resources = self._get_scopes_and_resources()
        body = {
            'resources': resources
        }
        api_response = requests.post(api_url, headers=headers, json=body)

        if api_response.ok:
            response_data = api_response.json()
            job_id = response_data.get('archiveJobId')
            if job_id:
                job_list = self.data_job_ids or []
                job_list.append(job_id)
                self.data_job_ids = job_list

                job_status = self.job_status or {}
                job_status[job_id] = {'completed': False, 'downloaded_at': None, 'state': None}
                self.job_status = job_status
                self.save()
                return True, "Data export initiated successfully."
            else:
                return False, "No archiveJobId returned in response."
        else:
            self.processing_log += f"Failed to initiate data export: {api_response.text}\n"
            self.save()
            return False, f"Failed to initiate data export: {api_response.text}"

    def handle_auth_callback(self, request):
        code = request.GET.get('code')
        if not code:
            return False, "Google authorization failed: No code returned."

        token_url = 'https://oauth2.googleapis.com/token'
        token_data = {
            'code': code,
            'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
            'client_secret': settings.GOOGLE_OAUTH_CLIENT_SECRET,
            'redirect_uri': request.build_absolute_uri(reverse('google-auth-callback')),
            'grant_type': 'authorization_code',
        }

        try:
            response = requests.post(token_url, data=token_data)
            response.raise_for_status()
            tokens = response.json()

            try:
                self.access_token = crypto.encrypt_text(tokens['access_token'])
            except Exception as e:
                self.access_token = None
                self.processing_log += f"Failed to encrypt access_token: {e}\n"
            try:
                if tokens.get('refresh_token'):
                    self.refresh_token = crypto.encrypt_text(tokens['refresh_token'])
                else:
                    self.refresh_token = ''
            except Exception as e:
                self.refresh_token = None
                self.processing_log += f"Failed to encrypt refresh_token: {e}\n"
            expires_in = tokens.get('expires_in')
            self.token_expiry = timezone.now() + timedelta(seconds=expires_in)
            self.processing_status = 'authorized'
            self.status = 'authorized'
            self.save()

        except requests.RequestException as e:
            return False, f"Token request failed: {e}"
        except KeyError as e:
            return False, f"Error parsing token response: Missing key {e}"

        try:
            success, message = self.create_archive_job()
        except Exception as e:
            return False, f"Error creating archive job: {e}"
        if success:
            self.status = 'processing'
            self.save()
            return True, "Authorization successful."
        else:
            return False, message

    def refresh_access_token(self):
        if not self.refresh_token:
            return False, "No refresh token available."

        token_url = 'https://oauth2.googleapis.com/token'
        refresh_token_plain = crypto.decrypt_text(self.refresh_token)
        token_data = {
            'refresh_token': refresh_token_plain,
            'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
            'client_secret': settings.GOOGLE_OAUTH_CLIENT_SECRET,
            'grant_type': 'refresh_token',
        }

        try:
            response = requests.post(token_url, data=token_data)
            response.raise_for_status()
            tokens = response.json()

            try:
                self.access_token = crypto.encrypt_text(tokens['access_token'])
            except Exception as e:
                self.access_token = None
                self.processing_log += f"Failed to encrypt refreshed access_token: {e}\n"
            expires_in = tokens.get('expires_in')
            self.token_expiry = timezone.now() + timedelta(seconds=expires_in)
            self.save()
            return True, "Token refreshed successfully."

        except requests.RequestException as e:
            return False, f"Token refresh failed: {e}"
        except KeyError as e:
            return False, f"Error parsing token response: Missing key {e}"

    def revoke_before_delete(self):
        success, message = self.refresh_access_token()
        if not success:
            return False, message

        if self.access_token:
            token = crypto.decrypt_text(self.access_token)
            revoke_url = 'https://dataportability.googleapis.com/v1/authorization:reset'
            headers = {
                'Authorization': f"Bearer {token}",
                'Content-Type': 'application/json',
            }
            try:
                response = requests.post(revoke_url, headers=headers)
                response.raise_for_status()
            except requests.RequestException as e:
                error_message = f"Failed to revoke Google OAuth token: {e}"
                self.processing_log += error_message + "\n"
                self.save()
                return False, error_message

        self.cleanup_files()
        return True, "Authorization revoked successfully."

    def download_data_files(self):
        self.refresh_access_token()
        if not self.access_token:
            return False, "Cannot download data: No valid access token."

        refresh_token_plain = crypto.decrypt_text(self.refresh_token)
        token_data = {
            'refresh_token': refresh_token_plain,
            'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
            'client_secret': settings.GOOGLE_OAUTH_CLIENT_SECRET,
            'grant_type': 'refresh_token',
        }

        try:
            token_response = requests.post(
                'https://oauth2.googleapis.com/token', data=token_data
            )
            token_response.raise_for_status()
            tokens = token_response.json()
            access_token = tokens['access_token']

            headers = {'Authorization': f"Bearer {access_token}"}
            job_ids = self.data_job_ids
            if not job_ids:
                return False, "No data export jobs found. Please initiate a data export first."
            for job_id in job_ids:
                job_status = self.job_status or {}
                if job_status.get(job_id, {}).get('completed'):
                    continue

                api_url = f'https://dataportability.googleapis.com/v1/archiveJobs/{job_id}/portabilityArchiveState'
                api_response = requests.get(api_url, headers=headers)
                status_data = api_response.json()
                if status_data.get('state') != 'COMPLETE':
                    return False, "Data export is still processing. Please check back later."

                download_urls = status_data.get('urls', [])
                for i, url in enumerate(download_urls):
                    file_response = requests.get(url)
                    if not os.path.exists('data'):
                        os.makedirs('data')
                    path = f'data/google_data_{job_id}_{i}.zip'
                    crypto.write_encrypted_bytes(path, file_response.content)
                    self.downloaded_files.append(path)
                self.processing_status = 'processing'
                self.status = 'processing'

                job_status[job_id] = {
                    'completed': True,
                    'downloaded_at': timezone.now().isoformat(),
                    'state': 'COMPLETED',
                }
                self.job_status = job_status
                self.save()

        except requests.RequestException as e:
            return False, f"Error during data retrieval: {e}"
        except KeyError as e:
            return False, f"Error parsing data retrieval response: Missing key {e}"
        except Exception as e:
            return False, f"Unexpected error during data retrieval: {e}"

    def extract_and_process(self):
        if self.processing_status not in ('processing', 'error'):
            return

        try:
            if not os.path.exists('data'):
                os.makedirs('data')

            file_status = self.file_status or {}
            data_type_status = self.data_type_status or {}

            for filepath in self.downloaded_files:
                if file_status.get(filepath, {}).get('processed'):
                    continue
                if not os.path.exists(filepath):
                    self.processing_log += f"File not found: {filepath}\n"
                    file_status[filepath] = {'processed': True, 'skipped': True}
                    continue

                try:
                    tmp_fp = crypto.decrypt_file_to_temp(filepath)
                except Exception as e:
                    self.processing_log += f"Failed to decrypt {filepath}: {e}\n"
                    continue

                try:
                    for data_type, reader in self.DATA_TYPE_READERS.items():
                        if self.requested_data_types and data_type not in self.requested_data_types:
                            continue
                        if data_type_status.get(data_type, {}).get('received'):
                            continue
                        try:
                            df = reader(tmp_fp)
                            if df is None or df.empty:
                                continue
                            df = df.reset_index()
                            df["device_id"] = str(self.pk)
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
                            self.processing_log += f"Received {data_type} from {filepath}\n"
                        except NotImplementedError:
                            pass
                        except Exception as e:
                            self.processing_log += f"Failed to read {data_type} from {filepath}: {e}\n"
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
                filepath in file_status for filepath in self.downloaded_files
            )
            if all_files_done:
                expected = self.requested_data_types or self.EXPECTED_DATA_TYPES
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
            self.processing_log += f"Unexpected error during processing: {e}\n"
            self.processing_status = 'error'
            self.save()

    def _process_data(self):
        self.download_data_files()
        if self.downloaded_files:
            self.extract_and_process()

    def cleanup_files(self):
        for filepath in self.downloaded_files:
            if os.path.exists(filepath):
                os.remove(filepath)
        self.downloaded_files = []
        self.save()
