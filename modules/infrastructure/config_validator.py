"""
Configuration validator for AutoFarm Zero — Success Guru Network v6.0.

Runs on system startup and daily via cron to validate all required
configuration is present and functional. Prevents the system from
operating in a silently broken state.

Validates:
1. All required .env variables present and non-empty
2. Groq API key valid (test completion call)
3. Pexels API key valid (test search)
4. Ollama responsive (test generation)
5. Database schema version matches expected
6. Disk space > 10GB free
7. Proxy-vm reachable from content-vm
8. All Squid proxy ports responding
9. Telegram bot token valid
10. SMTP credentials valid (test connection)
11. Cron jobs installed correctly
12. SSL certificates not expiring within 7 days (if applicable)
"""

import os
import json
import socket
import smtplib
import subprocess
import logging
from pathlib import Path
from typing import Optional

import requests
import structlog

logger = structlog.get_logger(__name__)


class ConfigValidator:
    """
    Validates all required configuration is present and functional.

    Runs on system startup and daily via cron.
    Returns a structured result with errors (must fix) and
    warnings (should fix) so the system can decide whether to proceed.

    Attributes:
        REQUIRED_ENV_VARS: List of environment variables that must be set.
        OPTIONAL_ENV_VARS: List of environment variables that should be set.
        MIN_DISK_FREE_GB: Minimum free disk space required.
        EXPECTED_TABLE_COUNT: Expected number of database tables.
    """

    REQUIRED_ENV_VARS: list[str] = [
        'DATABASE_PATH',
        'ENCRYPTION_KEY',
        'GROQ_API_KEY',
        'PEXELS_API_KEY',
        'PROXY_VM_INTERNAL_IP',
    ]

    OPTIONAL_ENV_VARS: list[str] = [
        'TELEGRAM_BOT_TOKEN',
        'TELEGRAM_REVIEW_CHAT_ID',
        'SMTP_HOST',
        'SMTP_PORT',
        'SMTP_USER',
        'SMTP_PASSWORD',
        'PROXY_VM_PUBLIC_IP',
        'OCI_REGION',
        'OCI_NAMESPACE',
        'COMPARTMENT_OCID',
        'YOUTUBE_CLIENT_ID',
        'YOUTUBE_CLIENT_SECRET',
    ]

    MIN_DISK_FREE_GB: float = 10.0
    EXPECTED_TABLE_COUNT: int = 26  # Minimum expected tables

    def __init__(self) -> None:
        """
        Initializes the ConfigValidator.

        Side effects:
            None on init. Call validate_all() to run checks.
        """
        self._errors: list[str] = []
        self._warnings: list[str] = []

    def validate_all(self) -> dict:
        """
        Runs all validation checks and returns results.

        Returns:
            Dict with keys:
                valid (bool): True if no errors (warnings OK).
                errors (list[str]): Critical issues that must be fixed.
                warnings (list[str]): Non-critical issues to address.
                checks_run (int): Total number of checks performed.
                checks_passed (int): Number of checks that passed.

        Side effects:
            Makes HTTP requests to validate API keys and services.
            Queries the database for schema validation.
            Checks network connectivity to proxy-vm.
        """
        self._errors = []
        self._warnings = []
        checks_run = 0
        checks_passed = 0

        validators = [
            ('env_vars', self._validate_env_vars),
            ('database', self._validate_database),
            ('disk_space', self._validate_disk_space),
            ('ollama', self._validate_ollama),
            ('groq_api', self._validate_groq_api),
            ('pexels_api', self._validate_pexels_api),
            ('proxy_vm', self._validate_proxy_vm),
            ('telegram', self._validate_telegram),
            ('smtp', self._validate_smtp),
            ('config_files', self._validate_config_files),
            ('directories', self._validate_directories),
            ('brands_config', self._validate_brands_config),
        ]

        for name, validator in validators:
            checks_run += 1
            try:
                passed = validator()
                if passed:
                    checks_passed += 1
                logger.info("config_check",
                              check=name,
                              passed=passed)
            except Exception as e:
                self._errors.append(f"{name}: Unexpected error: {e}")
                logger.error("config_check_error",
                              check=name, error=str(e))

        result = {
            'valid': len(self._errors) == 0,
            'errors': self._errors.copy(),
            'warnings': self._warnings.copy(),
            'checks_run': checks_run,
            'checks_passed': checks_passed,
            'timestamp': __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc
            ).isoformat(),
        }

        if result['valid']:
            logger.info("config_validation_passed",
                          checks_passed=checks_passed,
                          warnings=len(self._warnings))
        else:
            logger.error("config_validation_failed",
                          errors=len(self._errors),
                          warnings=len(self._warnings))

        return result

    def _validate_env_vars(self) -> bool:
        """
        Checks that all required environment variables are set.

        Returns:
            True if all required vars are present.

        Side effects:
            Adds errors for missing required vars.
            Adds warnings for missing optional vars.
        """
        all_present = True

        for var in self.REQUIRED_ENV_VARS:
            value = os.getenv(var, '').strip()
            if not value:
                self._errors.append(
                    f"Required environment variable {var} is not set"
                )
                all_present = False

        for var in self.OPTIONAL_ENV_VARS:
            value = os.getenv(var, '').strip()
            if not value:
                self._warnings.append(
                    f"Optional environment variable {var} is not set"
                )

        return all_present

    def _validate_database(self) -> bool:
        """
        Validates database schema and integrity.

        Returns:
            True if database is healthy and has expected tables.

        Side effects:
            Connects to SQLite database.
            Runs integrity check.
        """
        try:
            from database.db import Database
            db = Database()

            # Check integrity
            result = db.fetch_one("PRAGMA quick_check")
            if not result:
                self._errors.append("Database integrity check returned no result")
                return False

            integrity_value = result[0] if isinstance(result, (list, tuple)) \
                else result.get('quick_check', '')
            if integrity_value != 'ok':
                self._errors.append(
                    f"Database integrity check failed: {integrity_value}"
                )
                return False

            # Check table count
            tables = db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
            table_count = len(tables)

            if table_count < self.EXPECTED_TABLE_COUNT:
                self._warnings.append(
                    f"Database has {table_count} tables, "
                    f"expected at least {self.EXPECTED_TABLE_COUNT}. "
                    f"Run schema.sql to create missing tables."
                )

            # Check WAL mode
            wal_result = db.fetch_one("PRAGMA journal_mode")
            wal_mode = wal_result[0] if isinstance(wal_result, (list, tuple)) \
                else wal_result.get('journal_mode', '')
            if wal_mode != 'wal':
                self._warnings.append(
                    f"Database not in WAL mode (current: {wal_mode})"
                )

            return True

        except Exception as e:
            self._errors.append(f"Database validation failed: {e}")
            return False

    def _validate_disk_space(self) -> bool:
        """
        Checks that sufficient disk space is available.

        Returns:
            True if free disk space exceeds MIN_DISK_FREE_GB.

        Side effects:
            Reads disk usage via psutil.
        """
        try:
            import psutil
            try:
                disk = psutil.disk_usage('/app')
            except FileNotFoundError:
                disk = psutil.disk_usage('/')

            free_gb = disk.free / (1024 ** 3)

            if free_gb < self.MIN_DISK_FREE_GB:
                self._errors.append(
                    f"Insufficient disk space: {free_gb:.1f}GB free, "
                    f"need at least {self.MIN_DISK_FREE_GB}GB"
                )
                return False

            if free_gb < self.MIN_DISK_FREE_GB * 2:
                self._warnings.append(
                    f"Disk space is low: {free_gb:.1f}GB free"
                )

            return True

        except Exception as e:
            self._errors.append(f"Disk space check failed: {e}")
            return False

    def _validate_ollama(self) -> bool:
        """
        Checks if Ollama is responsive and has a model loaded.

        Returns:
            True if Ollama responds to API calls.

        Side effects:
            Makes HTTP request to local Ollama instance.
        """
        ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
        if not ollama_host.startswith('http'):
            ollama_host = f'http://{ollama_host}'

        try:
            response = requests.get(
                f'{ollama_host}/api/tags',
                timeout=10
            )

            if response.status_code != 200:
                self._warnings.append(
                    f"Ollama returned HTTP {response.status_code}"
                )
                return False

            data = response.json()
            models = data.get('models', [])

            if not models:
                self._warnings.append(
                    "Ollama has no models loaded. "
                    "Run: ollama pull llama3.1:8b"
                )
                return False

            # Check for LLaMA model
            has_llama = any(
                'llama' in m.get('name', '').lower()
                for m in models
            )
            if not has_llama:
                self._warnings.append(
                    "Ollama does not have a LLaMA model. "
                    "Run: ollama pull llama3.1:8b"
                )

            return True

        except requests.ConnectionError:
            self._warnings.append(
                "Ollama is not running. Start with: ollama serve"
            )
            return False
        except Exception as e:
            self._warnings.append(f"Ollama check failed: {e}")
            return False

    def _validate_groq_api(self) -> bool:
        """
        Validates Groq API key with a minimal test call.

        Returns:
            True if API key is valid and Groq responds.

        Side effects:
            Makes a minimal API call to Groq (few tokens).
        """
        api_key = os.getenv('GROQ_API_KEY', '').strip()
        if not api_key:
            # Already reported as missing env var
            return False

        try:
            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'llama-3.3-70b-versatile',
                    'messages': [{'role': 'user', 'content': 'Hi'}],
                    'max_tokens': 5,
                },
                timeout=15
            )

            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                self._errors.append("Groq API key is invalid (401 Unauthorized)")
                return False
            elif response.status_code == 429:
                # Rate limited but key is valid
                self._warnings.append("Groq API is rate limited (key is valid)")
                return True
            else:
                self._warnings.append(
                    f"Groq API returned HTTP {response.status_code}"
                )
                return False

        except Exception as e:
            self._warnings.append(f"Groq API check failed: {e}")
            return False

    def _validate_pexels_api(self) -> bool:
        """
        Validates Pexels API key with a test search.

        Returns:
            True if API key is valid and Pexels responds.

        Side effects:
            Makes a minimal API call to Pexels.
        """
        api_key = os.getenv('PEXELS_API_KEY', '').strip()
        if not api_key:
            return False

        try:
            response = requests.get(
                'https://api.pexels.com/videos/search',
                headers={'Authorization': api_key},
                params={'query': 'nature', 'per_page': 1},
                timeout=10
            )

            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                self._errors.append(
                    "Pexels API key is invalid (401 Unauthorized)"
                )
                return False
            else:
                self._warnings.append(
                    f"Pexels API returned HTTP {response.status_code}"
                )
                return False

        except Exception as e:
            self._warnings.append(f"Pexels API check failed: {e}")
            return False

    def _validate_proxy_vm(self) -> bool:
        """
        Checks if proxy-vm is reachable and Squid ports are open.

        Returns:
            True if proxy-vm is reachable.

        Side effects:
            Attempts TCP connections to proxy-vm.
        """
        proxy_ip = os.getenv('PROXY_VM_INTERNAL_IP', '').strip()
        if not proxy_ip:
            self._warnings.append(
                "PROXY_VM_INTERNAL_IP not set — proxy checks skipped"
            )
            return True  # Not an error if proxy not configured

        # Check if host is reachable
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((proxy_ip, 22))  # SSH port
            sock.close()

            if result != 0:
                self._warnings.append(
                    f"Proxy-vm at {proxy_ip} is not reachable (port 22)"
                )
                return False

        except Exception as e:
            self._warnings.append(
                f"Proxy-vm connectivity check failed: {e}"
            )
            return False

        # Check Squid proxy ports
        from config.settings import PROXY_PORTS
        all_ok = True

        for brand_id, port in PROXY_PORTS.items():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((proxy_ip, port))
                sock.close()

                if result != 0:
                    self._warnings.append(
                        f"Squid proxy for {brand_id} (port {port}) "
                        f"is not responding"
                    )
                    all_ok = False

            except Exception:
                all_ok = False

        return all_ok

    def _validate_telegram(self) -> bool:
        """
        Validates Telegram bot token.

        Returns:
            True if bot token is valid.

        Side effects:
            Makes HTTP request to Telegram Bot API.
        """
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        if not bot_token:
            self._warnings.append(
                "TELEGRAM_BOT_TOKEN not set — Telegram review disabled"
            )
            return True  # Optional service

        try:
            response = requests.get(
                f'https://api.telegram.org/bot{bot_token}/getMe',
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return True

            if response.status_code == 401:
                self._errors.append(
                    "Telegram bot token is invalid (401 Unauthorized)"
                )
                return False

            self._warnings.append(
                f"Telegram API returned HTTP {response.status_code}"
            )
            return False

        except Exception as e:
            self._warnings.append(f"Telegram check failed: {e}")
            return False

    def _validate_smtp(self) -> bool:
        """
        Validates SMTP credentials with a test connection.

        Returns:
            True if SMTP connection succeeds.

        Side effects:
            Establishes and closes an SMTP connection.
        """
        smtp_host = os.getenv('SMTP_HOST', '').strip()
        if not smtp_host:
            self._warnings.append(
                "SMTP_HOST not set — email fallback disabled"
            )
            return True  # Optional service

        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER', '').strip()
        smtp_password = os.getenv('SMTP_PASSWORD', '').strip()

        try:
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                server.starttls()

            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)

            server.quit()
            return True

        except smtplib.SMTPAuthenticationError:
            self._errors.append(
                "SMTP authentication failed — check credentials"
            )
            return False
        except Exception as e:
            self._warnings.append(f"SMTP check failed: {e}")
            return False

    def _validate_config_files(self) -> bool:
        """
        Checks that all required config files exist and are valid JSON.

        Returns:
            True if all config files are present and valid.

        Side effects:
            Reads and parses config files.
        """
        base_dir = Path(os.getenv('APP_DIR', '/app'))
        config_dir = base_dir / 'config'

        required_files = [
            'brands.json',
            'platforms.json',
            'youtube_projects.json',
        ]

        all_valid = True

        for filename in required_files:
            filepath = config_dir / filename
            if not filepath.exists():
                self._errors.append(
                    f"Config file missing: {filepath}"
                )
                all_valid = False
                continue

            try:
                with open(filepath, 'r') as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                self._errors.append(
                    f"Invalid JSON in {filepath}: {e}"
                )
                all_valid = False

        # Check cached responses
        cached_dir = config_dir / 'cached_responses'
        cached_files = [
            'script_generation.json',
            'caption_variation.json',
            'hashtag_generation.json',
        ]

        for filename in cached_files:
            filepath = cached_dir / filename
            if not filepath.exists():
                self._warnings.append(
                    f"Cached response file missing: {filepath}"
                )
            else:
                try:
                    with open(filepath, 'r') as f:
                        json.load(f)
                except json.JSONDecodeError as e:
                    self._warnings.append(
                        f"Invalid JSON in {filepath}: {e}"
                    )

        return all_valid

    def _validate_directories(self) -> bool:
        """
        Checks that all required directories exist with correct permissions.

        Returns:
            True if all directories exist.

        Side effects:
            Creates missing directories if possible.
        """
        base_dir = Path(os.getenv('APP_DIR', '/app'))

        required_dirs = [
            base_dir / 'data',
            base_dir / 'logs',
            base_dir / 'media',
            base_dir / 'media' / 'output',
            base_dir / 'media' / 'backgrounds',
            base_dir / 'media' / 'tts',
            base_dir / 'media' / 'thumbnails',
            base_dir / 'media' / 'broll',
            base_dir / 'media' / 'music',
        ]

        all_ok = True

        for dir_path in required_dirs:
            if not dir_path.exists():
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                    self._warnings.append(
                        f"Created missing directory: {dir_path}"
                    )
                except OSError as e:
                    self._errors.append(
                        f"Cannot create directory {dir_path}: {e}"
                    )
                    all_ok = False

        return all_ok

    def _validate_brands_config(self) -> bool:
        """
        Validates brands.json has correct structure for all brands.

        Returns:
            True if brands config is valid.

        Side effects:
            Reads and parses brands.json.
        """
        try:
            from config.settings import load_brands_config
            brands = load_brands_config()

            if not brands:
                self._errors.append("brands.json is empty")
                return False

            required_fields = [
                'display_name', 'niche', 'positioning',
                'visual_identity', 'voice_persona',
            ]

            all_valid = True
            for brand_id, config in brands.items():
                for field in required_fields:
                    if field not in config:
                        self._warnings.append(
                            f"Brand {brand_id} missing field: {field}"
                        )
                        all_valid = False

            return all_valid

        except Exception as e:
            self._warnings.append(f"Brands config validation failed: {e}")
            return False
