"""
Account setup module for AutoFarm Zero — Success Guru Network v6.0.

Handles initial account creation and configuration for all brands
across all platforms. Pre-populates the accounts table with pending
entries for each brand-platform combination, and provides methods
for registering credentials when accounts are created externally.

Account creation flow:
1. Pre-populate all 30 accounts as pending_setup
2. User creates platform accounts externally
3. User runs add_account.py to register credentials
4. AccountSetup validates and encrypts credentials
5. Account becomes active for publishing
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import structlog

from database.db import Database
from database.credential_manager import CredentialManager
from account_manager.manager import AccountManager

logger = structlog.get_logger(__name__)


class AccountSetup:
    """
    Handles initial account setup and credential registration.

    Manages the lifecycle of account creation from pre-population
    through credential registration and validation. Platform-specific
    credential requirements are enforced during setup.

    Attributes:
        CREDENTIAL_REQUIREMENTS: Per-platform required credential fields.
    """

    CREDENTIAL_REQUIREMENTS: dict = {
        'tiktok': {
            'required': ['access_token', 'open_id'],
            'optional': ['refresh_token', 'refresh_expires_in'],
            'description': 'TikTok Content Posting API credentials',
        },
        'instagram': {
            'required': ['access_token', 'instagram_account_id'],
            'optional': ['page_id', 'app_id', 'app_secret'],
            'description': 'Instagram Graph API credentials (via Facebook)',
        },
        'facebook': {
            'required': ['access_token', 'page_id'],
            'optional': ['app_id', 'app_secret', 'user_id'],
            'description': 'Facebook Page API credentials',
        },
        'youtube': {
            'required': ['client_id', 'client_secret', 'refresh_token'],
            'optional': ['access_token', 'channel_id', 'project_id'],
            'description': 'YouTube Data API v3 OAuth 2.0 credentials',
        },
        'snapchat': {
            'required': ['access_token', 'organization_id'],
            'optional': ['refresh_token', 'ad_account_id'],
            'description': 'Snapchat Marketing API credentials',
        },
    }

    def __init__(self) -> None:
        """
        Initializes AccountSetup with database and credential manager.

        Side effects:
            Creates Database, CredentialManager, and AccountManager instances.
        """
        self.db = Database()
        self.credential_manager = CredentialManager()
        self.account_manager = AccountManager()

    def initialize_all_accounts(self) -> int:
        """
        Pre-populates accounts table with all brand-platform combinations.

        Creates an account row for each of the 6 brands across 5 platforms
        (30 total), all with status 'pending_setup'.

        Returns:
            Number of accounts created (skips existing).

        Side effects:
            Inserts up to 30 rows into the accounts table.
        """
        from config.settings import load_brands_config

        brands = load_brands_config()
        platforms = AccountManager.SUPPORTED_PLATFORMS
        created_count = 0

        for brand_id in brands:
            for platform in platforms:
                existing = self.account_manager.get_account(
                    brand_id, platform
                )
                if not existing:
                    self.account_manager.create_account(
                        brand_id=brand_id,
                        platform=platform,
                        username='',
                        account_id='',
                        credentials=None
                    )
                    created_count += 1
                    logger.info("account_pre_populated",
                                  brand_id=brand_id,
                                  platform=platform)

        logger.info("accounts_initialized",
                      created=created_count,
                      total_brands=len(brands),
                      total_platforms=len(platforms))

        return created_count

    def register_account(self, brand_id: str, platform: str,
                         username: str, account_id: str,
                         credentials: dict,
                         token_expires_at: Optional[str] = None) -> bool:
        """
        Registers credentials for an existing account.

        Parameters:
            brand_id: Brand identifier.
            platform: Platform name.
            username: Platform username or handle.
            account_id: Platform-specific account ID.
            credentials: Dict of credential key-value pairs.
            token_expires_at: Optional ISO timestamp for token expiry.

        Returns:
            True if registration succeeded, False on validation failure.

        Side effects:
            Validates credentials against platform requirements.
            Encrypts and stores credentials in the database.
            Updates account status to 'active'.

        Raises:
            ValueError: If credentials are missing required fields.
        """
        # Validate platform
        if platform not in self.CREDENTIAL_REQUIREMENTS:
            logger.error("unsupported_platform",
                          platform=platform)
            return False

        # Validate required credentials
        validation = self.validate_credentials(platform, credentials)
        if not validation['valid']:
            logger.error("credential_validation_failed",
                          brand_id=brand_id, platform=platform,
                          errors=validation['errors'])
            return False

        # Log warnings for missing optional fields
        for warning in validation.get('warnings', []):
            logger.warning("credential_warning",
                            brand_id=brand_id, platform=platform,
                            warning=warning)

        # Ensure account row exists
        existing = self.account_manager.get_account(brand_id, platform)
        if not existing:
            self.account_manager.create_account(
                brand_id=brand_id,
                platform=platform,
                username=username,
                account_id=account_id,
                credentials=credentials
            )
        else:
            # Update existing account
            self.account_manager.update_credentials(
                brand_id, platform, credentials, token_expires_at
            )

            # Update username and account_id
            now = datetime.now(timezone.utc).isoformat()
            self.db.execute_write(
                "UPDATE accounts SET username=?, account_id=?, "
                "status='active', updated_at=? "
                "WHERE brand_id=? AND platform=?",
                (username, account_id, now, brand_id, platform)
            )

        logger.info("account_registered",
                      brand_id=brand_id, platform=platform,
                      username=username)
        return True

    def validate_credentials(self, platform: str,
                              credentials: dict) -> dict:
        """
        Validates credentials against platform requirements.

        Parameters:
            platform: Platform name.
            credentials: Dict of credential key-value pairs.

        Returns:
            Dict with keys:
                valid (bool): True if all required fields present.
                errors (list[str]): Missing required fields.
                warnings (list[str]): Missing optional fields.
        """
        if platform not in self.CREDENTIAL_REQUIREMENTS:
            return {
                'valid': False,
                'errors': [f'Unknown platform: {platform}'],
                'warnings': [],
            }

        requirements = self.CREDENTIAL_REQUIREMENTS[platform]
        errors = []
        warnings = []

        for field in requirements['required']:
            if field not in credentials or not credentials[field]:
                errors.append(
                    f"Missing required credential: {field}"
                )

        for field in requirements.get('optional', []):
            if field not in credentials:
                warnings.append(
                    f"Missing optional credential: {field}"
                )

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
        }

    def get_setup_status(self) -> dict:
        """
        Returns setup status for all accounts.

        Returns:
            Dict with per-brand, per-platform setup status,
            total counts, and completion percentage.

        Side effects:
            Reads from the database.
        """
        from config.settings import load_brands_config

        brands = load_brands_config()
        platforms = AccountManager.SUPPORTED_PLATFORMS

        status = {
            'brands': {},
            'total_expected': len(brands) * len(platforms),
            'total_created': 0,
            'total_active': 0,
            'total_pending': 0,
        }

        for brand_id in brands:
            status['brands'][brand_id] = {}
            for platform in platforms:
                account = self.account_manager.get_account(
                    brand_id, platform
                )
                if account:
                    status['brands'][brand_id][platform] = {
                        'status': account['status'],
                        'username': account.get('username', ''),
                        'has_credentials': bool(
                            account.get('credentials_encrypted')
                            or (self.db.fetch_one(
                                "SELECT credentials_encrypted "
                                "FROM accounts WHERE id=?",
                                (account['id'],)
                            ) or {}).get('credentials_encrypted')
                        ),
                    }
                    status['total_created'] += 1
                    if account['status'] == 'active':
                        status['total_active'] += 1
                    elif account['status'] == 'pending_setup':
                        status['total_pending'] += 1
                else:
                    status['brands'][brand_id][platform] = {
                        'status': 'not_created',
                        'username': '',
                        'has_credentials': False,
                    }

        if status['total_expected'] > 0:
            status['completion_percent'] = round(
                (status['total_active'] / status['total_expected']) * 100, 1
            )
        else:
            status['completion_percent'] = 0.0

        return status

    def import_credentials_from_file(self, filepath: str) -> dict:
        """
        Imports credentials for multiple accounts from a JSON file.

        Parameters:
            filepath: Path to JSON file with credentials.
                Format: [{"brand_id": "...", "platform": "...",
                          "username": "...", "account_id": "...",
                          "credentials": {...}}]

        Returns:
            Dict with success_count, error_count, and errors list.

        Side effects:
            Registers credentials for each account in the file.
        """
        results = {
            'success_count': 0,
            'error_count': 0,
            'errors': [],
        }

        try:
            with open(filepath, 'r') as f:
                accounts_data = json.load(f)

            if not isinstance(accounts_data, list):
                results['errors'].append(
                    "File must contain a JSON array of account objects"
                )
                results['error_count'] = 1
                return results

            for entry in accounts_data:
                try:
                    success = self.register_account(
                        brand_id=entry['brand_id'],
                        platform=entry['platform'],
                        username=entry.get('username', ''),
                        account_id=entry.get('account_id', ''),
                        credentials=entry['credentials'],
                        token_expires_at=entry.get('token_expires_at'),
                    )
                    if success:
                        results['success_count'] += 1
                    else:
                        results['error_count'] += 1
                        results['errors'].append(
                            f"Failed to register {entry.get('brand_id')}/"
                            f"{entry.get('platform')}"
                        )
                except KeyError as e:
                    results['error_count'] += 1
                    results['errors'].append(f"Missing key: {e}")
                except Exception as e:
                    results['error_count'] += 1
                    results['errors'].append(str(e))

        except FileNotFoundError:
            results['errors'].append(f"File not found: {filepath}")
            results['error_count'] = 1
        except json.JSONDecodeError as e:
            results['errors'].append(f"Invalid JSON: {e}")
            results['error_count'] = 1

        logger.info("credentials_import_complete",
                      filepath=filepath,
                      success=results['success_count'],
                      errors=results['error_count'])

        return results

    def get_credential_requirements(self,
                                     platform: str) -> Optional[dict]:
        """
        Returns credential requirements for a platform.

        Parameters:
            platform: Platform name.

        Returns:
            Dict with required, optional fields and description,
            or None if platform is unknown.
        """
        return self.CREDENTIAL_REQUIREMENTS.get(platform)
