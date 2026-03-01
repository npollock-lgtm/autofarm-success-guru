"""
Encrypted credential manager for AutoFarm Zero — Success Guru Network v6.0.

Manages encrypted storage and retrieval of platform API credentials
(OAuth tokens, API keys, secrets) per brand per platform. Uses Fernet
symmetric encryption with a key stored in the .env file.

Security model:
- Credentials are encrypted at rest in the SQLite database
- Encryption key (FERNET_KEY) lives in .env (not in the database)
- Each credential set is a JSON blob encrypted as a single unit
- Token refresh operations decrypt, update, and re-encrypt atomically
"""

import os
import json
import logging
from datetime import datetime
from cryptography.fernet import Fernet, InvalidToken

from database.db import Database

logger = logging.getLogger(__name__)


class CredentialManager:
    """
    Handles encrypted storage and retrieval of platform credentials.

    All platform API tokens, OAuth credentials, and API keys are stored
    encrypted in the accounts table. This class provides encrypt/decrypt
    operations and credential lifecycle management (save, retrieve, refresh).
    """

    def __init__(self):
        """
        Initializes the CredentialManager with Fernet encryption.

        Side effects:
            Loads FERNET_KEY from environment. Raises ValueError if missing.
        """
        self.db = Database()
        fernet_key = os.getenv('FERNET_KEY', '')
        if not fernet_key:
            logger.warning("FERNET_KEY not set — credential encryption disabled")
            self._fernet = None
        else:
            try:
                self._fernet = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
            except Exception as e:
                logger.error(f"Invalid FERNET_KEY: {e}")
                self._fernet = None

    def encrypt(self, data: dict) -> str:
        """
        Encrypts a dictionary of credentials to a string.

        Parameters:
            data: Dictionary of credential key-value pairs.

        Returns:
            Base64-encoded encrypted string.

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if self._fernet is None:
            raise RuntimeError("Encryption not configured. Set FERNET_KEY in .env")

        json_bytes = json.dumps(data).encode('utf-8')
        encrypted = self._fernet.encrypt(json_bytes)
        return encrypted.decode('utf-8')

    def decrypt(self, encrypted_str: str) -> dict:
        """
        Decrypts an encrypted credential string back to a dictionary.

        Parameters:
            encrypted_str: Base64-encoded encrypted string.

        Returns:
            Dictionary of credential key-value pairs.

        Raises:
            RuntimeError: If encryption is not configured.
            InvalidToken: If the encrypted data is corrupt or key is wrong.
        """
        if self._fernet is None:
            raise RuntimeError("Encryption not configured. Set FERNET_KEY in .env")

        decrypted = self._fernet.decrypt(encrypted_str.encode('utf-8'))
        return json.loads(decrypted.decode('utf-8'))

    def save_credentials(self, brand_id: str, platform: str,
                         credentials: dict) -> None:
        """
        Saves encrypted credentials for a brand's platform account.

        Parameters:
            brand_id: The brand identifier.
            platform: Platform name (tiktok, instagram, etc.).
            credentials: Dictionary containing API keys, tokens, secrets.

        Side effects:
            Updates the accounts table with encrypted credentials.
            Updates the account status to 'active'.
        """
        encrypted = self.encrypt(credentials)

        # Check if account exists
        account = self.db.get_account(brand_id, platform)
        if account:
            self.db.pool.write_with_lock(
                """UPDATE accounts SET credentials_encrypted = ?,
                   status = 'active', updated_at = CURRENT_TIMESTAMP
                   WHERE brand_id = ? AND platform = ?""",
                (encrypted, brand_id, platform)
            )
        else:
            self.db.insert('accounts', {
                'brand_id': brand_id,
                'platform': platform,
                'credentials_encrypted': encrypted,
                'status': 'active',
            })

        logger.info(
            "Credentials saved",
            extra={'brand_id': brand_id, 'platform': platform}
        )

    def get_credentials(self, brand_id: str, platform: str) -> dict | None:
        """
        Retrieves and decrypts credentials for a brand's platform account.

        Parameters:
            brand_id: The brand identifier.
            platform: Platform name.

        Returns:
            Dictionary of decrypted credentials, or None if not found.
        """
        account = self.db.get_account(brand_id, platform)
        if not account or not account.get('credentials_encrypted'):
            return None

        try:
            return self.decrypt(account['credentials_encrypted'])
        except InvalidToken:
            logger.error(
                "Failed to decrypt credentials — key mismatch or corruption",
                extra={'brand_id': brand_id, 'platform': platform}
            )
            return None

    def update_token(self, brand_id: str, platform: str,
                     token_key: str, new_value: str,
                     expires_at: str = None) -> bool:
        """
        Updates a specific token within the encrypted credentials.

        Used during OAuth token refresh to update access_token without
        losing other stored credentials (client_id, client_secret, etc.).

        Parameters:
            brand_id: The brand identifier.
            platform: Platform name.
            token_key: Key within credentials dict (e.g. 'access_token').
            new_value: New token value.
            expires_at: Optional ISO datetime when the token expires.

        Returns:
            True if update succeeded, False if credentials not found.

        Side effects:
            Decrypts, modifies, re-encrypts, and saves credentials atomically.
            Updates token_expires_at in the accounts table.
        """
        credentials = self.get_credentials(brand_id, platform)
        if credentials is None:
            return False

        credentials[token_key] = new_value
        if expires_at:
            credentials['token_expires_at'] = expires_at

        encrypted = self.encrypt(credentials)
        self.db.pool.write_with_lock(
            """UPDATE accounts SET credentials_encrypted = ?,
               token_expires_at = ?, last_token_refresh = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP
               WHERE brand_id = ? AND platform = ?""",
            (encrypted, expires_at, brand_id, platform)
        )

        logger.info(
            "Token updated",
            extra={'brand_id': brand_id, 'platform': platform, 'token_key': token_key}
        )
        return True

    def get_expiring_tokens(self, hours_ahead: int = 1) -> list[dict]:
        """
        Finds accounts whose tokens expire within the given timeframe.

        Parameters:
            hours_ahead: Number of hours to look ahead for expiry.

        Returns:
            List of account dicts with tokens expiring soon.
        """
        return self.db.query(
            """SELECT * FROM accounts
               WHERE status = 'active'
               AND token_expires_at IS NOT NULL
               AND token_expires_at < datetime('now', ?)""",
            (f'+{hours_ahead} hours',)
        )

    def delete_credentials(self, brand_id: str, platform: str) -> bool:
        """
        Removes credentials for a brand's platform account.

        Parameters:
            brand_id: The brand identifier.
            platform: Platform name.

        Returns:
            True if credentials were deleted.

        Side effects:
            Sets credentials_encrypted to NULL and status to 'inactive'.
        """
        result = self.db.pool.write_with_lock(
            """UPDATE accounts SET credentials_encrypted = NULL,
               status = 'inactive', updated_at = CURRENT_TIMESTAMP
               WHERE brand_id = ? AND platform = ?""",
            (brand_id, platform)
        )
        return result.rowcount > 0

    def verify_encryption(self) -> bool:
        """
        Verifies that encryption is working by performing a round-trip test.

        Returns:
            True if encrypt/decrypt round-trip succeeds.
        """
        if self._fernet is None:
            return False

        try:
            test_data = {'test': 'verification', 'timestamp': datetime.utcnow().isoformat()}
            encrypted = self.encrypt(test_data)
            decrypted = self.decrypt(encrypted)
            return decrypted == test_data
        except Exception as e:
            logger.error(f"Encryption verification failed: {e}")
            return False

    @staticmethod
    def generate_key() -> str:
        """
        Generates a new Fernet encryption key.

        Returns:
            Base64-encoded Fernet key string suitable for FERNET_KEY env var.
        """
        return Fernet.generate_key().decode('utf-8')
