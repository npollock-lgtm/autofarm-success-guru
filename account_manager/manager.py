"""
Account manager for AutoFarm Zero — Success Guru Network v6.0.

Central management of all platform accounts across all brands.
Handles account listing, status tracking, credential management,
and account health monitoring. Each brand has one dedicated account
per platform (6 brands × 5 platforms = up to 30 accounts).

Credential lifecycle:
1. Account created with pending_setup status
2. Credentials added via add_account.py script
3. Token refresher keeps OAuth tokens valid
4. Status monitored for health/bans/issues
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from database.db import Database
from database.credential_manager import CredentialManager

logger = structlog.get_logger(__name__)


class AccountManager:
    """
    Central management for all platform accounts across all brands.

    Provides methods for listing accounts, checking status, managing
    credentials, and monitoring account health. Works with the
    CredentialManager for encrypted credential storage.

    Attributes:
        SUPPORTED_PLATFORMS: List of supported platform identifiers.
        STATUS_VALUES: Valid account status values.
    """

    SUPPORTED_PLATFORMS: list[str] = [
        'tiktok', 'instagram', 'facebook', 'youtube', 'snapchat'
    ]

    STATUS_VALUES: list[str] = [
        'pending_setup',    # Account not yet created/configured
        'active',           # Account operational
        'token_expired',    # OAuth token needs refresh
        'suspended',        # Platform suspended the account
        'banned',           # Account permanently banned
        'disabled',         # Manually disabled by operator
        'rate_limited',     # Temporarily rate limited
    ]

    def __init__(self) -> None:
        """
        Initializes the AccountManager.

        Side effects:
            Creates Database and CredentialManager instances.
        """
        self.db = Database()
        self.credential_manager = CredentialManager()

    def list_accounts(self, brand_id: Optional[str] = None,
                      platform: Optional[str] = None,
                      status: Optional[str] = None) -> list[dict]:
        """
        Lists accounts with optional filtering.

        Parameters:
            brand_id: Filter by brand. None for all brands.
            platform: Filter by platform. None for all platforms.
            status: Filter by status. None for all statuses.

        Returns:
            List of account dicts with keys: id, brand_id, platform,
            username, account_id, status, follower_count, created_at,
            token_expires_at, last_token_refresh.

        Side effects:
            Reads from the database. Credentials are NOT included.
        """
        query = "SELECT id, brand_id, platform, username, account_id, " \
                "status, follower_count, created_at, updated_at, " \
                "token_expires_at, last_token_refresh FROM accounts WHERE 1=1"
        params = []

        if brand_id:
            query += " AND brand_id=?"
            params.append(brand_id)
        if platform:
            query += " AND platform=?"
            params.append(platform)
        if status:
            query += " AND status=?"
            params.append(status)

        query += " ORDER BY brand_id, platform"

        rows = self.db.fetch_all(query, tuple(params))
        return [dict(row) for row in rows]

    def get_account(self, brand_id: str,
                    platform: str) -> Optional[dict]:
        """
        Gets a single account by brand and platform.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Account dict or None if not found.

        Side effects:
            Reads from the database.
        """
        row = self.db.fetch_one(
            "SELECT id, brand_id, platform, username, account_id, "
            "status, follower_count, created_at, updated_at, "
            "token_expires_at, last_token_refresh "
            "FROM accounts WHERE brand_id=? AND platform=?",
            (brand_id, platform)
        )
        return dict(row) if row else None

    def get_active_account(self, brand_id: str,
                           platform: str) -> Optional[dict]:
        """
        Gets an active account with valid credentials.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            Account dict with decrypted credentials, or None if
            account is not active or credentials are invalid.

        Side effects:
            Reads and decrypts credentials from the database.
        """
        row = self.db.fetch_one(
            "SELECT * FROM accounts WHERE brand_id=? AND platform=? "
            "AND status='active'",
            (brand_id, platform)
        )

        if not row:
            logger.debug("no_active_account",
                          brand_id=brand_id, platform=platform)
            return None

        account = dict(row)

        # Decrypt credentials
        if account.get('credentials_encrypted'):
            try:
                account['credentials'] = self.credential_manager.decrypt(
                    account['credentials_encrypted']
                )
            except Exception as e:
                logger.error("credential_decrypt_failed",
                              brand_id=brand_id, platform=platform,
                              error=str(e))
                return None
        else:
            account['credentials'] = {}

        # Check if token is expired
        if account.get('token_expires_at'):
            expires = datetime.fromisoformat(account['token_expires_at'])
            if expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                logger.warning("token_expired",
                                brand_id=brand_id, platform=platform)
                self.update_status(brand_id, platform, 'token_expired')
                return None

        return account

    def create_account(self, brand_id: str, platform: str,
                       username: str = "",
                       account_id: str = "",
                       credentials: Optional[dict] = None) -> int:
        """
        Creates a new account record.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            username: Platform username.
            account_id: Platform-specific account ID.
            credentials: Optional dict of credentials to encrypt and store.

        Returns:
            Account ID of the created record.

        Side effects:
            Inserts a row into the accounts table.
            Encrypts and stores credentials if provided.

        Raises:
            ValueError: If brand_id or platform is invalid.
        """
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")

        # Check if account already exists
        existing = self.get_account(brand_id, platform)
        if existing:
            logger.warning("account_already_exists",
                            brand_id=brand_id, platform=platform,
                            existing_id=existing['id'])
            return existing['id']

        encrypted_creds = None
        status = 'pending_setup'
        if credentials:
            encrypted_creds = self.credential_manager.encrypt(credentials)
            status = 'active'

        self.db.execute_write(
            "INSERT INTO accounts "
            "(brand_id, platform, username, account_id, status, "
            "credentials_encrypted) VALUES (?, ?, ?, ?, ?, ?)",
            (brand_id, platform, username, account_id,
             status, encrypted_creds)
        )

        account = self.get_account(brand_id, platform)
        account_id_db = account['id'] if account else 0

        logger.info("account_created",
                      brand_id=brand_id, platform=platform,
                      username=username, status=status,
                      account_id=account_id_db)

        return account_id_db

    def update_credentials(self, brand_id: str, platform: str,
                           credentials: dict,
                           token_expires_at: Optional[str] = None) -> bool:
        """
        Updates encrypted credentials for an account.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            credentials: New credentials dict to encrypt and store.
            token_expires_at: Optional ISO timestamp for token expiry.

        Returns:
            True if update succeeded, False if account not found.

        Side effects:
            Encrypts and updates credentials in the database.
            Updates token_expires_at and last_token_refresh timestamps.
        """
        account = self.get_account(brand_id, platform)
        if not account:
            logger.error("account_not_found",
                          brand_id=brand_id, platform=platform)
            return False

        encrypted = self.credential_manager.encrypt(credentials)
        now = datetime.now(timezone.utc).isoformat()

        update_sql = (
            "UPDATE accounts SET credentials_encrypted=?, "
            "last_token_refresh=?, updated_at=?"
        )
        params = [encrypted, now, now]

        if token_expires_at:
            update_sql += ", token_expires_at=?"
            params.append(token_expires_at)

        update_sql += " WHERE brand_id=? AND platform=?"
        params.extend([brand_id, platform])

        self.db.execute_write(update_sql, tuple(params))

        logger.info("credentials_updated",
                      brand_id=brand_id, platform=platform,
                      token_expires=token_expires_at)
        return True

    def update_status(self, brand_id: str, platform: str,
                      status: str) -> bool:
        """
        Updates account status.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            status: New status value (must be in STATUS_VALUES).

        Returns:
            True if update succeeded, False if account not found.

        Side effects:
            Updates the accounts table.

        Raises:
            ValueError: If status is not a valid value.
        """
        if status not in self.STATUS_VALUES:
            raise ValueError(
                f"Invalid status: {status}. "
                f"Must be one of: {self.STATUS_VALUES}"
            )

        account = self.get_account(brand_id, platform)
        if not account:
            return False

        now = datetime.now(timezone.utc).isoformat()
        self.db.execute_write(
            "UPDATE accounts SET status=?, updated_at=? "
            "WHERE brand_id=? AND platform=?",
            (status, now, brand_id, platform)
        )

        logger.info("account_status_updated",
                      brand_id=brand_id, platform=platform,
                      old_status=account['status'],
                      new_status=status)
        return True

    def update_follower_count(self, brand_id: str, platform: str,
                               count: int) -> bool:
        """
        Updates the follower count for an account.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            count: New follower count.

        Returns:
            True if update succeeded, False if account not found.

        Side effects:
            Updates follower_count in the accounts table.
        """
        self.db.execute_write(
            "UPDATE accounts SET follower_count=?, updated_at=? "
            "WHERE brand_id=? AND platform=?",
            (count, datetime.now(timezone.utc).isoformat(),
             brand_id, platform)
        )
        return True

    def get_accounts_needing_refresh(self,
                                      hours_before_expiry: int = 24
                                      ) -> list[dict]:
        """
        Gets accounts whose tokens expire within the specified window.

        Parameters:
            hours_before_expiry: Refresh tokens expiring within this many hours.

        Returns:
            List of account dicts that need token refresh.

        Side effects:
            Reads from the database.
        """
        cutoff = (
            datetime.now(timezone.utc) + timedelta(hours=hours_before_expiry)
        ).isoformat()

        rows = self.db.fetch_all(
            "SELECT id, brand_id, platform, username, token_expires_at "
            "FROM accounts WHERE status='active' "
            "AND token_expires_at IS NOT NULL "
            "AND token_expires_at < ? "
            "ORDER BY token_expires_at ASC",
            (cutoff,)
        )
        return [dict(row) for row in rows]

    def get_network_summary(self) -> dict:
        """
        Returns a summary of all accounts across the network.

        Returns:
            Dict with total_accounts, active_accounts, by_status,
            by_platform, by_brand, total_followers.

        Side effects:
            Multiple database queries.
        """
        all_accounts = self.list_accounts()

        by_status = {}
        by_platform = {}
        by_brand = {}
        total_followers = 0

        for account in all_accounts:
            status = account.get('status', 'unknown')
            platform = account.get('platform', 'unknown')
            brand = account.get('brand_id', 'unknown')
            followers = account.get('follower_count', 0) or 0

            by_status[status] = by_status.get(status, 0) + 1
            by_platform[platform] = by_platform.get(platform, 0) + 1
            by_brand[brand] = by_brand.get(brand, 0) + 1
            total_followers += followers

        return {
            'total_accounts': len(all_accounts),
            'active_accounts': by_status.get('active', 0),
            'pending_setup': by_status.get('pending_setup', 0),
            'by_status': by_status,
            'by_platform': by_platform,
            'by_brand': by_brand,
            'total_followers': total_followers,
        }

    def disable_account(self, brand_id: str, platform: str,
                        reason: str = "") -> bool:
        """
        Disables an account (manual override).

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            reason: Reason for disabling.

        Returns:
            True if disabled, False if account not found.

        Side effects:
            Updates account status to 'disabled'.
            Logs the disabling action.
        """
        success = self.update_status(brand_id, platform, 'disabled')
        if success:
            logger.warning("account_disabled",
                            brand_id=brand_id, platform=platform,
                            reason=reason)
        return success

    def enable_account(self, brand_id: str, platform: str) -> bool:
        """
        Re-enables a disabled account.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.

        Returns:
            True if re-enabled, False if account not found or
            has no credentials.

        Side effects:
            Updates account status to 'active' if credentials exist.
        """
        account = self.get_account(brand_id, platform)
        if not account:
            return False

        # Check if account has credentials
        row = self.db.fetch_one(
            "SELECT credentials_encrypted FROM accounts "
            "WHERE brand_id=? AND platform=?",
            (brand_id, platform)
        )

        if row and row['credentials_encrypted']:
            return self.update_status(brand_id, platform, 'active')
        else:
            self._warnings_log(
                "Cannot enable account without credentials",
                brand_id, platform
            )
            return self.update_status(brand_id, platform, 'pending_setup')

    def _warnings_log(self, message: str, brand_id: str,
                      platform: str) -> None:
        """
        Logs a warning about an account.

        Parameters:
            message: Warning message.
            brand_id: Brand identifier.
            platform: Platform name.

        Side effects:
            Writes to the structured log.
        """
        logger.warning("account_warning",
                         message=message,
                         brand_id=brand_id,
                         platform=platform)
