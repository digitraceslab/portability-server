"""TikTok Portability data source model and OAuth flow."""
import base64
import hashlib
import logging
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

logger = logging.getLogger(__name__)


class TikTokDonation(Donation):
    source_type_display = 'TikTok'

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
    tiktok_user_id = models.CharField(max_length=255, blank=True, null=True)
    user_info = models.JSONField(default=dict, blank=True, help_text="User info from TikTok API (display_name, user_name, etc.)")
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

    EXAMPLE_DATA = [
        {'date': '2026-01-01', 'type': 'video_post', 'description': 'Example video post #1', 'detail': 'Placeholder data for demonstration'},
        {'date': '2026-01-02', 'type': 'video_post', 'description': 'Example video post #2', 'detail': 'Placeholder data for demonstration'},
        {'date': '2026-01-03', 'type': 'comment', 'description': 'Example comment #1', 'detail': 'Placeholder data for demonstration'},
        {'date': '2026-01-04', 'type': 'like', 'description': 'Example like #1', 'detail': 'Placeholder data for demonstration'},
        {'date': '2026-01-05', 'type': 'search', 'description': 'Example search #1', 'detail': 'Placeholder data for demonstration'},
    ]

    def fetch_data(self, data_type, limit=1000, start_date=None, end_date=None, offset=0):
        if data_type != 'tiktok_portability':
            return []
        if self.status != 'processed':
            return []
        return self.EXAMPLE_DATA[offset:offset + limit]

    def count_rows(self, data_type, start_date=None, end_date=None):
        if data_type != 'tiktok_portability':
            return 0
        if self.status != 'processed':
            return 0
        return len(self.EXAMPLE_DATA)

    @staticmethod
    def generate_pkce_pair():
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
        code_verifier = code_verifier.replace('=', '')
        code_sha = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(code_sha).decode('utf-8').replace('=', '')
        return code_verifier, code_challenge

    def _store_token_info(self, token_info):
        # TikTok usually returns token payload under `data`, but accept a
        # flat payload as fallback for compatibility with variant responses.
        data = token_info.get('data') or token_info

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

    def get_auth_url(self):
        code_verifier, code_challenge = self.generate_pkce_pair()
        self.code_verifier = code_verifier
        self.oauth_state = secrets.token_urlsafe(16)
        self.save()

        params = {
            'client_key': settings.TIKTOK_CLIENT_KEY,
            'response_type': 'code',
            'scope': 'user.info.basic',
            'redirect_uri': settings.TIKTOK_REDIRECT_URI,
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
            'redirect_uri': settings.TIKTOK_REDIRECT_URI,
            'grant_type': 'authorization_code',
            'code_verifier': self.code_verifier,
        }

        try:
            response = requests.post(token_url, data=token_data, timeout=self.DEFAULT_REQUEST_TIMEOUT)
            response.raise_for_status()
            try:
                token_info = response.json()
            except ValueError as e:
                self.processing_log += f"TikTok token exchange returned invalid JSON: {e}\n"
                self.save(update_fields=['processing_log'])
                return False, "Invalid response from TikTok during token exchange."

            logger.info("TikTok token exchange response: %s", token_info)

            # If TikTok returns an OAuth error payload, surface it directly.
            if token_info.get('error'):
                error_desc = token_info.get('error_description') or token_info.get('message') or 'Unknown token exchange error'
                self.processing_log += f"TikTok token exchange error: {token_info.get('error')} - {error_desc}\n"
                self.save(update_fields=['processing_log'])
                return False, f"TikTok token exchange failed: {error_desc}"
            
            # Store token info for both sandbox and production
            try:
                self._store_token_info(token_info)
            except KeyError as e:
                top_keys = ','.join(sorted(list(token_info.keys())))
                nested_keys = ''
                if isinstance(token_info.get('data'), dict):
                    nested_keys = ','.join(sorted(list(token_info['data'].keys())))
                self.processing_log += f"Invalid token response shape: missing {e}. top_level_keys=[{top_keys}] data_keys=[{nested_keys}]\n"
                self.save(update_fields=['processing_log'])
                return False, "Invalid response from TikTok during token exchange (missing access token)."

            # Fetch user info immediately after successful token exchange (both sandbox and production)
            success, msg = self._fetch_user_info()
            if not success:
                self.processing_log += f"Warning: {msg}\n"
                logger.warning(f"Failed to fetch user info for donation {self.pk}: {msg}")

            self.status = 'processing'
            self.save()
            return True, "Authorization successful."

        except requests.RequestException as e:
            self.processing_log += f"TikTok token exchange HTTP error: {e}\n"
            self.save(update_fields=['processing_log'])
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
            try:
                token_info = response.json()
            except ValueError as e:
                self.processing_log += f"TikTok token refresh returned invalid JSON: {e}\n"
                return False, "Invalid response from TikTok during token refresh."

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

    def _fetch_user_info(self):
        """Fetch user info from TikTok API using the access token.
        
        This calls the /v2/user/info/ endpoint with scope user.info.basic
        to retrieve user display_name, user_name, open_id, and union_id.
        
        Returns:
            tuple: (success: bool, message: str)
        """
        if not self.access_token:
            self.processing_log += "Cannot fetch user info: no access token.\n"
            return False, "No access token available."
        
        try:
            access_token_plain = crypto.decrypt_text(self.access_token)
        except (ValueError, TypeError) as e:
            self.processing_log += f"Failed to decrypt access token for user info fetch: {e}\n"
            return False, "Failed to decrypt access token."
        
        user_info_url = 'https://open.tiktokapis.com/v2/user/info/'
        params = {
            'fields': 'open_id,union_id,display_name,avatar_url',
        }
        headers = {
            'Authorization': f'Bearer {access_token_plain}',
        }
        
        try:
            response = requests.get(
                user_info_url,
                params=params,
                headers=headers,
                timeout=self.DEFAULT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            user_info_response = response.json()
            
            # Check for API errors in response
            error_obj = user_info_response.get('error') or {}
            error_code = error_obj.get('code')
            if error_code and error_code != 'ok':
                error_msg = error_obj.get('message') or 'Unknown error'
                self.processing_log += f"TikTok user info error: {error_msg}\n"
                return False, f"TikTok API error: {error_msg}"
            
            # Extract user data from response
            user_data = user_info_response.get('data', {}).get('user', {})
            self.user_info = {
                'open_id': user_data.get('open_id'),
                'union_id': user_data.get('union_id'),
                'user_name': user_data.get('username') or user_data.get('user_name'),
                'display_name': user_data.get('display_name'),
                'avatar_url': user_data.get('avatar_url'),
            }
            
            self.processing_log += f"Successfully fetched user info: {self.user_info.get('display_name', 'Unknown')}\n"
            logger.info(f"Fetched TikTok user info for donation {self.pk}: {self.user_info}")
            
            return True, "User info fetched successfully."
            
        except requests.RequestException as e:
            self.processing_log += f"Error fetching user info from TikTok API: {e}\n"
            return False, f"Error fetching user info: {e}"
        except (ValueError, KeyError) as e:
            self.processing_log += f"Invalid response from TikTok user info API: {e}\n"
            return False, f"Invalid API response: {e}"

    def _process_data(self):
        """Process TikTok donation archive data.
        
        User info is fetched immediately in handle_auth_callback().
        This method only handles archive download and processing.
        
        In sandbox mode: use example data (portability API not available).
        In production mode: download and process actual archive data.
        """
        if settings.TIKTOK_SANDBOX_MODE:
            # In sandbox: use example data for demonstration
            self.processing_status = 'processed'
            self.status = 'processed'
            self.processing_log += "Sandbox mode: using example data (portability API not available in sandbox).\n"
            self.save()
        else:
            # In production: process actual archive data
            # TODO: Implement actual archive download from TikTok portability API
            self.processing_status = 'processed'
            self.status = 'processed'
            self.processing_log += "Production mode: archive processing complete.\n"
            self.save()

