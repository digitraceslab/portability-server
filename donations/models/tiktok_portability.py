"""TikTok Portability data source model and OAuth flow."""
import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from donations.models import Donation
from donations.utils import crypto


class TikTokDonation(Donation):
    DEFAULT_REQUEST_TIMEOUT = 10

    PROCESSING_STATUS_CHOICES = (
        ('authorized', 'Authorized, waiting for data'),
        ('data_requested', 'Data portability request submitted'),
        ('processing', 'Processing'),
        ('processed', 'Processed successfully'),
        ('error', 'Error during processing'),
    )

    access_token = models.CharField(max_length=500, blank=True, null=True)
    refresh_token = models.CharField(max_length=500, blank=True, null=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    tiktok_user_id = models.CharField(max_length=255, blank=True, unique=True, null=True)
    code_verifier = models.CharField(max_length=200, blank=True)
    oauth_state = models.CharField(max_length=100, blank=True, null=True)

    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='authorized',
    )

    def save(self, *args, **kwargs):
        if not self.source_type:
            self.source_type = 'tiktok_portability'
        super().save(*args, **kwargs)

    def get_data_types(self):
        return ['tiktok_portability']

    def fetch_data(self, data_type, limit=1000, start_date=None, end_date=None, offset=0):
        if data_type != 'tiktok_portability':
            return []
        if self.processing_status != 'processed':
            return []
        return [{
            'data_type': 'tiktok_portability',
            'data': {'message': 'TikTok portability data fetched successfully.'},
            'fetched_at': timezone.now().isoformat(),
        }]

    def count_rows(self, data_type, start_date=None, end_date=None):
        if data_type != 'tiktok_portability':
            return 0
        if self.processing_status != 'processed':
            return 0
        return 1

    @staticmethod
    def generate_pkce_pair():
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
        code_verifier = code_verifier.replace('=', '')
        code_sha = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(code_sha).decode('utf-8').replace('=', '')
        return code_verifier, code_challenge

    def _store_token_info(self, token_info):
        data = token_info.get('data', {})

        try:
            access_plain = data['access_token']
        except KeyError:
            raise KeyError("access_token missing from token_info")

        try:
            self.access_token = crypto.encrypt_text(access_plain)
        except (ValueError, TypeError) as e:
            self.access_token = None
            self.processing_log += f"Failed to encrypt access_token: {e}\n"

        if data.get('refresh_token'):
            try:
                self.refresh_token = crypto.encrypt_text(data['refresh_token'])
            except (ValueError, TypeError) as e:
                self.refresh_token = None
                self.processing_log += f"Failed to encrypt refresh_token: {e}\n"
        else:
            self.refresh_token = ''

        expires_in = data.get('expires_in')
        if expires_in is not None:
            try:
                self.token_expiry = timezone.now() + timedelta(seconds=int(expires_in))
            except (TypeError, ValueError):
                self.token_expiry = None
                self.processing_log += "Invalid expires_in value; token_expiry not set.\n"

        if data.get('open_id'):
            self.tiktok_user_id = data['open_id']

        self.processing_status = 'authorized'
        self.code_verifier = ''

    def get_auth_url(self, request):
        code_verifier, code_challenge = self.generate_pkce_pair()
        self.code_verifier = code_verifier
        self.oauth_state = secrets.token_urlsafe(16)
        self.save()

        redirect_url = request.build_absolute_uri(
            reverse('tiktok-auth-callback')
        )

        params = {
            'client_key': settings.TIKTOK_CLIENT_KEY,
            'response_type': 'code',
            'scope': 'user.info.basic',
            'redirect_uri': redirect_url,
            'state': self.oauth_state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }

        return f"https://www.tiktok.com/v2/auth/authorize?{urlencode(params)}"

    def handle_auth_callback(self, request):
        code = request.GET.get('code')
        if not code:
            return False, "Authorization code not provided."

        if not self.code_verifier:
            return False, "Missing code verifier. Authorization may have expired."

        token_url = 'https://open.tiktokapis.com/v2/oauth/token/'
        token_data = {
            'code': code,
            'client_key': settings.TIKTOK_CLIENT_KEY,
            'client_secret': settings.TIKTOK_CLIENT_SECRET,
            'redirect_uri': request.build_absolute_uri(reverse('tiktok-auth-callback')),
            'grant_type': 'authorization_code',
            'code_verifier': self.code_verifier,
        }

        try:
            response = requests.post(token_url, data=token_data, timeout=self.DEFAULT_REQUEST_TIMEOUT)
            response.raise_for_status()
            token_info = response.json()
            try:
                self._store_token_info(token_info)
            except KeyError:
                return False, "Invalid response from TikTok during token exchange."

            self.status = 'processing'
            self.save()
            return True, "Authorization successful."

        except requests.RequestException as e:
            return False, f"Error during token exchange: {e}"
        except KeyError:
            return False, "Invalid response from TikTok during token exchange."

    def refresh_access_token(self):
        if not self.refresh_token:
            return False, "No refresh token available."

        token_url = 'https://open.tiktokapis.com/v2/oauth/token/'
        try:
            refresh_token_plain = crypto.decrypt_text(self.refresh_token)
        except (ValueError, TypeError) as e:
            self.processing_log += f"Failed to decrypt refresh_token: {e}\n"
            return False, "Failed to decrypt refresh token."
        token_data = {
            'client_key': settings.TIKTOK_CLIENT_KEY,
            'client_secret': settings.TIKTOK_CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token_plain,
        }

        try:
            response = requests.post(token_url, data=token_data, timeout=self.DEFAULT_REQUEST_TIMEOUT)
            response.raise_for_status()
            token_info = response.json()

            try:
                self._store_token_info(token_info)
            except KeyError:
                return False, "Invalid response from TikTok during token refresh."

            self.save()
            return True, "Access token refreshed successfully."

        except requests.RequestException as e:
            return False, f"Error during token refresh: {e}"
        except KeyError:
            return False, "Invalid response from TikTok during token refresh."

    def _process_data(self):
        pass
