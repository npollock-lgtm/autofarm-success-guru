"""
Generate Encryption Key — creates a Fernet encryption key for
credential storage and writes it to ``.env``.

Only run once during initial setup.  The key is used by
``CredentialManager`` to encrypt/decrypt OAuth tokens stored in SQLite.

Usage::

    python scripts/generate_encryption_key.py
"""

import os
import sys
from pathlib import Path


def main() -> None:
    """Generate a new Fernet encryption key and save to .env.

    Side Effects
    ------------
    Appends ``ENCRYPTION_KEY=...`` to ``.env`` if not already set.
    """
    from cryptography.fernet import Fernet

    env_path = Path(".env")

    # Check if already set
    if os.getenv("ENCRYPTION_KEY"):
        print("  ENCRYPTION_KEY already set in environment")
        print("  To regenerate, remove it from .env first")
        return

    # Check .env file
    if env_path.exists():
        content = env_path.read_text()
        if "ENCRYPTION_KEY=" in content:
            print("  ENCRYPTION_KEY already exists in .env")
            print("  To regenerate, remove the line first")
            return

    # Generate new key
    key = Fernet.generate_key().decode()

    # Append to .env
    with open(env_path, "a") as f:
        f.write(f"\n# Generated encryption key for credential storage\n")
        f.write(f"ENCRYPTION_KEY={key}\n")

    print(f"  Encryption key generated and saved to .env")
    print(f"  IMPORTANT: Back up this key — losing it means losing all stored credentials")


if __name__ == "__main__":
    main()
