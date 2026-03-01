"""
Setup Google Drive Auth — interactive OAuth flow for Google Drive
access (optional — only needed if ``GDRIVE_ENABLED=true``).

Creates and stores OAuth credentials for uploading review videos to
Google Drive as a fallback when Telegram file size limits are exceeded.

Usage::

    python scripts/setup_gdrive_auth.py
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    """Run the Google Drive OAuth authorization flow.

    Side Effects
    ------------
    Creates ``data/gdrive_token.json`` with OAuth credentials.
    """
    if os.getenv("GDRIVE_ENABLED", "").lower() != "true":
        print("  Google Drive is not enabled (GDRIVE_ENABLED != true)")
        print("  Set GDRIVE_ENABLED=true in .env to use Google Drive")
        confirm = input("  Continue anyway? (y/N): ").strip().lower()
        if confirm != "y":
            return

    print("\n  GOOGLE DRIVE AUTH SETUP")
    print("=" * 50)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("  ERROR: Google auth libraries not installed")
        print("  Run: pip install google-auth google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    TOKEN_PATH = Path("data/gdrive_token.json")
    CREDENTIALS_PATH = Path("data/gdrive_credentials.json")

    # Check for existing token
    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            if creds and creds.valid:
                print("  PASS  Existing token is valid")
                return
            elif creds and creds.expired and creds.refresh_token:
                print("  Refreshing expired token...")
                creds.refresh(Request())
                TOKEN_PATH.write_text(creds.to_json())
                print("  PASS  Token refreshed")
                return
        except Exception as exc:
            print(f"  WARN  Failed to load existing token: {exc}")

    # Need new auth flow
    if not CREDENTIALS_PATH.exists():
        print(f"  ERROR: {CREDENTIALS_PATH} not found")
        print("  Download OAuth client credentials from Google Cloud Console:")
        print("  1. Go to https://console.cloud.google.com/apis/credentials")
        print("  2. Create OAuth 2.0 Client ID (Desktop application)")
        print("  3. Download JSON and save as data/gdrive_credentials.json")
        sys.exit(1)

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
        print("  PASS  Google Drive authorized successfully")
        print(f"  Token saved to: {TOKEN_PATH}")
    except Exception as exc:
        print(f"  ERROR: Auth flow failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
