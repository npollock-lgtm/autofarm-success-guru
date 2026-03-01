"""
Token refresher for AutoFarm Zero — Success Guru Network v6.0.

Handles automatic OAuth token refresh for all platform accounts.
Runs as a scheduled job (cron) to prevent token expiry that would
block publishing.

Refresh strategy per platform:
- YouTube: OAuth2 refresh_token grant (long-lived refresh token)
- Instagram/Facebook: Exchange short-lived for long-lived token
- TikTok: Refresh token exchange
- Snapchat: OAuth2 refresh_token grant

Timing:
- Runs every 6 hours via cron
- Refreshes tokens expiring within 24 hours
- Handles rate limits with exponential backoff
"""

import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import structlog

from database.db import Database
from database.credential_manager import CredentialManager
from account_manager.manager import AccountManager
from modules.infrastructure.retry_handler import retry_with_backoff

logger = structlog.get_logger(__name__)


class TokenRefresher:
    """
    Automatic OAuth token refresh for all platform accounts.

    Monitors token expiry and proactively refreshes before expiry.
    Each platform has a different refresh mechanism handled by
    platform-specific methods.

    Attributes:
        REFRESH_WINDOW_HOURS: Refresh tokens expiring within this window.
        MAX_RETRIES: Maximum refresh attempts before marking account.
    """

    REFRESH_WINDOW_HOURS: int = 24
    MAX_RETRIES: int = 3

    def __init__(self) -> None:
        """
        Initializes the TokenRefresher.

        Side effects:
            Creates Database, CredentialManager, and AccountManager instances.
        """
        self.db = Database()
        self.credential_manager = CredentialManager()
        self.account_manager = AccountManager()
        self._refresh_count: int = 0
        self._error_count: int = 0

    def refresh_all_expiring(self) -> dict:
        """
        Refreshes all tokens that are expiring soon.

        Returns:
            Dict with refreshed_count, error_count, skipped_count,
            and per-account details.

        Side effects:
            Makes API calls to platform token endpoints.
            Updates encrypted credentials in the database.
            May update account status if refresh fails.
        """
        accounts = self.account_manager.get_accounts_needing_refresh(
            hours_before_expiry=self.REFRESH_WINDOW_HOURS
        )

        results = {
            'refreshed_count': 0,
            'error_count': 0,
            'skipped_count': 0,
            'details': [],
        }

        if not accounts:
            logger.info("no_tokens_to_refresh")
            return results

        logger.info("token_refresh_starting",
                      accounts_count=len(accounts))

        for account in accounts:
            brand_id = account['brand_id']
            platform = account['platform']

            try:
                success = self.refresh_token(brand_id, platform)
                if success:
                    results['refreshed_count'] += 1
                    results['details'].append({
                        'brand_id': brand_id,
                        'platform': platform,
                        'status': 'refreshed',
                    })
                else:
                    results['error_count'] += 1
                    results['details'].append({
                        'brand_id': brand_id,
                        'platform': platform,
                        'status': 'failed',
                    })

            except Exception as e:
                results['error_count'] += 1
                results['details'].append({
                    'brand_id': brand_id,
                    'platform': platform,
                    'status': 'error',
                    'error': str(e),
                })
                logger.error("token_refresh_error",
                              brand_id=brand_id, platform=platform,
                              error=str(e))

            # Brief delay between refreshes to avoid rate limits
            time.sleep(2)

        logger.info("token_refresh_complete",
                      refreshed=results['refreshed_count'],
                      errors=results['error_count'])

        return results

    def refresh_token(self, brand_id: str, platform: str) -> bool:
        """
        Refreshes the token for a specific brand+platform account.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            True if token was successfully refreshed, False otherwise.

        Side effects:
            Makes API calls to platform OAuth endpoints.
            Updates encrypted credentials in the database.
            Updates account status on failure.
        """
        # Get current credentials
        row = self.db.fetch_one(
            "SELECT credentials_encrypted FROM accounts "
            "WHERE brand_id=? AND platform=? AND status IN ('active', 'token_expired')",
            (brand_id, platform)
        )

        if not row or not row['credentials_encrypted']:
            logger.warning("no_credentials_to_refresh",
                            brand_id=brand_id, platform=platform)
            return False

        try:
            credentials = self.credential_manager.decrypt(
                row['credentials_encrypted']
            )
        except Exception as e:
            logger.error("credential_decrypt_failed",
                          brand_id=brand_id, platform=platform,
                          error=str(e))
            return False

        # Route to platform-specific refresh
        refresh_methods = {
            'youtube': self._refresh_youtube,
            'instagram': self._refresh_instagram,
            'facebook': self._refresh_facebook,
            'tiktok': self._refresh_tiktok,
            'snapchat': self._refresh_snapchat,
        }

        refresh_method = refresh_methods.get(platform)
        if not refresh_method:
            logger.error("unsupported_platform_refresh",
                          platform=platform)
            return False

        try:
            new_credentials, expires_at = refresh_method(
                brand_id, credentials
            )

            if new_credentials:
                # Update stored credentials
                self.account_manager.update_credentials(
                    brand_id, platform, new_credentials,
                    token_expires_at=expires_at
                )

                # Ensure status is active
                self.account_manager.update_status(
                    brand_id, platform, 'active'
                )

                self._refresh_count += 1
                logger.info("token_refreshed",
                              brand_id=brand_id, platform=platform,
                              expires_at=expires_at)
                return True
            else:
                self._error_count += 1
                return False

        except Exception as e:
            self._error_count += 1
            logger.error("token_refresh_failed",
                          brand_id=brand_id, platform=platform,
                          error=str(e))

            # Mark account as token_expired after repeated failures
            self.account_manager.update_status(
                brand_id, platform, 'token_expired'
            )
            return False

    @retry_with_backoff(max_retries=3, base_delay=2.0,
                        retry_on=(requests.RequestException,))
    def _refresh_youtube(self, brand_id: str,
                         credentials: dict) -> tuple[Optional[dict], Optional[str]]:
        """
        Refreshes YouTube OAuth2 token using refresh_token grant.

        Parameters:
            brand_id: Brand identifier.
            credentials: Current credentials with refresh_token.

        Returns:
            Tuple of (new_credentials dict, expires_at ISO string)
            or (None, None) on failure.

        Side effects:
            Makes HTTP request to Google OAuth2 token endpoint.
        """
        refresh_token = credentials.get('refresh_token')
        client_id = credentials.get('client_id', os.getenv('YOUTUBE_CLIENT_ID', ''))
        client_secret = credentials.get('client_secret', os.getenv('YOUTUBE_CLIENT_SECRET', ''))

        if not refresh_token:
            logger.error("youtube_no_refresh_token", brand_id=brand_id)
            return None, None

        response = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error("youtube_token_refresh_failed",
                          brand_id=brand_id,
                          status=response.status_code,
                          response=response.text[:200])
            return None, None

        data = response.json()
        expires_in = data.get('expires_in', 3600)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        new_credentials = {**credentials}
        new_credentials['access_token'] = data['access_token']
        if 'refresh_token' in data:
            new_credentials['refresh_token'] = data['refresh_token']

        return new_credentials, expires_at

    @retry_with_backoff(max_retries=3, base_delay=2.0,
                        retry_on=(requests.RequestException,))
    def _refresh_instagram(self, brand_id: str,
                           credentials: dict) -> tuple[Optional[dict], Optional[str]]:
        """
        Refreshes Instagram long-lived token.

        Instagram long-lived tokens are valid for 60 days and can be
        refreshed as long as they haven't expired.

        Parameters:
            brand_id: Brand identifier.
            credentials: Current credentials with access_token.

        Returns:
            Tuple of (new_credentials dict, expires_at ISO string)
            or (None, None) on failure.

        Side effects:
            Makes HTTP request to Instagram Graph API.
        """
        access_token = credentials.get('access_token')
        if not access_token:
            return None, None

        response = requests.get(
            'https://graph.instagram.com/refresh_access_token',
            params={
                'grant_type': 'ig_refresh_token',
                'access_token': access_token,
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error("instagram_token_refresh_failed",
                          brand_id=brand_id,
                          status=response.status_code)
            return None, None

        data = response.json()
        expires_in = data.get('expires_in', 5184000)  # 60 days default
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        new_credentials = {**credentials}
        new_credentials['access_token'] = data['access_token']

        return new_credentials, expires_at

    @retry_with_backoff(max_retries=3, base_delay=2.0,
                        retry_on=(requests.RequestException,))
    def _refresh_facebook(self, brand_id: str,
                          credentials: dict) -> tuple[Optional[dict], Optional[str]]:
        """
        Refreshes Facebook long-lived page access token.

        Facebook page access tokens obtained from long-lived user tokens
        are permanent and don't need refreshing. But user tokens do expire.

        Parameters:
            brand_id: Brand identifier.
            credentials: Current credentials with access_token.

        Returns:
            Tuple of (new_credentials dict, expires_at ISO string)
            or (None, None) on failure.

        Side effects:
            Makes HTTP request to Facebook Graph API.
        """
        access_token = credentials.get('access_token')
        app_id = credentials.get('app_id', os.getenv('FACEBOOK_APP_ID', ''))
        app_secret = credentials.get('app_secret', os.getenv('FACEBOOK_APP_SECRET', ''))

        if not access_token:
            return None, None

        # Exchange short-lived for long-lived token
        response = requests.get(
            'https://graph.facebook.com/v19.0/oauth/access_token',
            params={
                'grant_type': 'fb_exchange_token',
                'client_id': app_id,
                'client_secret': app_secret,
                'fb_exchange_token': access_token,
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error("facebook_token_refresh_failed",
                          brand_id=brand_id,
                          status=response.status_code)
            return None, None

        data = response.json()
        expires_in = data.get('expires_in', 5184000)  # 60 days
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        new_credentials = {**credentials}
        new_credentials['access_token'] = data['access_token']

        # Also refresh page token if page_id is available
        page_id = credentials.get('page_id')
        if page_id:
            page_response = requests.get(
                f'https://graph.facebook.com/v19.0/{page_id}',
                params={
                    'fields': 'access_token',
                    'access_token': data['access_token'],
                },
                timeout=30
            )
            if page_response.status_code == 200:
                page_data = page_response.json()
                new_credentials['page_access_token'] = page_data.get(
                    'access_token', ''
                )

        return new_credentials, expires_at

    @retry_with_backoff(max_retries=3, base_delay=2.0,
                        retry_on=(requests.RequestException,))
    def _refresh_tiktok(self, brand_id: str,
                        credentials: dict) -> tuple[Optional[dict], Optional[str]]:
        """
        Refreshes TikTok access token using refresh token.

        Parameters:
            brand_id: Brand identifier.
            credentials: Current credentials with refresh_token.

        Returns:
            Tuple of (new_credentials dict, expires_at ISO string)
            or (None, None) on failure.

        Side effects:
            Makes HTTP request to TikTok OAuth endpoint.
        """
        refresh_token = credentials.get('refresh_token')
        client_key = credentials.get('client_key',
                                     os.getenv('TIKTOK_CLIENT_KEY', ''))
        client_secret = credentials.get('client_secret',
                                        os.getenv('TIKTOK_CLIENT_SECRET', ''))

        if not refresh_token:
            logger.warning("tiktok_no_refresh_token", brand_id=brand_id)
            return None, None

        response = requests.post(
            'https://open.tiktokapis.com/v2/oauth/token/',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_key': client_key,
                'client_secret': client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error("tiktok_token_refresh_failed",
                          brand_id=brand_id,
                          status=response.status_code)
            return None, None

        data = response.json()
        if data.get('error', '') != '':
            logger.error("tiktok_token_refresh_error",
                          brand_id=brand_id,
                          error=data.get('error_description', ''))
            return None, None

        expires_in = data.get('expires_in', 86400)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        new_credentials = {**credentials}
        new_credentials['access_token'] = data.get('access_token', '')
        new_credentials['open_id'] = data.get('open_id',
                                              credentials.get('open_id', ''))
        if data.get('refresh_token'):
            new_credentials['refresh_token'] = data['refresh_token']
        if data.get('refresh_expires_in'):
            new_credentials['refresh_expires_in'] = data['refresh_expires_in']

        return new_credentials, expires_at

    @retry_with_backoff(max_retries=3, base_delay=2.0,
                        retry_on=(requests.RequestException,))
    def _refresh_snapchat(self, brand_id: str,
                          credentials: dict) -> tuple[Optional[dict], Optional[str]]:
        """
        Refreshes Snapchat access token using refresh token.

        Parameters:
            brand_id: Brand identifier.
            credentials: Current credentials with refresh_token.

        Returns:
            Tuple of (new_credentials dict, expires_at ISO string)
            or (None, None) on failure.

        Side effects:
            Makes HTTP request to Snapchat accounts API.
        """
        refresh_token = credentials.get('refresh_token')
        client_id = credentials.get('client_id',
                                    os.getenv('SNAPCHAT_CLIENT_ID', ''))
        client_secret = credentials.get('client_secret',
                                        os.getenv('SNAPCHAT_CLIENT_SECRET', ''))

        if not refresh_token:
            logger.warning("snapchat_no_refresh_token", brand_id=brand_id)
            return None, None

        response = requests.post(
            'https://accounts.snapchat.com/login/oauth2/access_token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error("snapchat_token_refresh_failed",
                          brand_id=brand_id,
                          status=response.status_code)
            return None, None

        data = response.json()
        expires_in = data.get('expires_in', 1800)  # 30 min default
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        new_credentials = {**credentials}
        new_credentials['access_token'] = data.get('access_token', '')
        if data.get('refresh_token'):
            new_credentials['refresh_token'] = data['refresh_token']

        return new_credentials, expires_at

    def get_refresh_status(self) -> dict:
        """
        Returns token refresh statistics.

        Returns:
            Dict with refresh_count, error_count, and accounts
            needing refresh.

        Side effects:
            Queries accounts table.
        """
        expiring = self.account_manager.get_accounts_needing_refresh(
            hours_before_expiry=self.REFRESH_WINDOW_HOURS
        )

        expired = self.account_manager.list_accounts(
            status='token_expired'
        )

        return {
            'total_refreshed': self._refresh_count,
            'total_errors': self._error_count,
            'expiring_soon': len(expiring),
            'already_expired': len(expired),
            'expiring_accounts': [
                {'brand_id': a['brand_id'],
                 'platform': a['platform'],
                 'expires_at': a.get('token_expires_at')}
                for a in expiring
            ],
        }
