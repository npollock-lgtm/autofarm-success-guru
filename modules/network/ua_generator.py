"""
Dynamic user agent generator for AutoFarm Zero — Success Guru Network v6.0.

Generates realistic, current user agent strings per brand persona.
Each brand has a consistent "device persona" (e.g., brand A = Mac/Chrome,
brand B = Windows/Chrome) but version numbers stay current to avoid bot
detection from stale user agents.

Updated monthly via cron job. User agents are stored in the database
and rotated to maintain consistency within each brand's identity.
"""

import logging
import random
from datetime import datetime

from database.db import Database

logger = logging.getLogger(__name__)


class UserAgentGenerator:
    """
    Generates realistic, current user agent strings per brand persona.

    Each brand has a fixed device/browser combination that forms its
    "digital persona". Version numbers are updated monthly to match
    current browser releases, preventing detection from stale UAs.
    """

    # Each brand has a consistent device persona
    BRAND_PERSONAS = {
        'human_success_guru': {'os': 'mac', 'browser': 'chrome'},
        'wealth_success_guru': {'os': 'windows', 'browser': 'chrome'},
        'zen_success_guru': {'os': 'mac', 'browser': 'safari'},
        'social_success_guru': {'os': 'windows', 'browser': 'firefox'},
        'habits_success_guru': {'os': 'linux', 'browser': 'chrome'},
        'relationships_success_guru': {'os': 'iphone', 'browser': 'safari'},
    }

    # OS version pools for variation
    MAC_VERSIONS = [
        'Intel Mac OS X 10_15_7',
        'Intel Mac OS X 11_6_8',
        'Intel Mac OS X 12_7_4',
        'Intel Mac OS X 13_6_4',
        'Intel Mac OS X 14_3_1',
    ]

    WINDOWS_VERSIONS = [
        'Windows NT 10.0; Win64; x64',
        'Windows NT 10.0; WOW64',
    ]

    LINUX_VERSIONS = [
        'X11; Linux x86_64',
        'X11; Ubuntu; Linux x86_64',
    ]

    IPHONE_VERSIONS = [
        'iPhone; CPU iPhone OS 17_3_1 like Mac OS X',
        'iPhone; CPU iPhone OS 17_4 like Mac OS X',
        'iPhone; CPU iPhone OS 16_7_5 like Mac OS X',
    ]

    def __init__(self):
        """
        Initializes the UserAgentGenerator.

        Caches generated user agents per brand to ensure consistency
        within a session.
        """
        self._cache: dict[str, str] = {}

    def get_ua(self, brand_id: str) -> str:
        """
        Returns a current, realistic UA string for this brand's persona.

        Uses cached value if available, otherwise generates a new one.
        The generated UA matches the brand's device persona with current
        browser version numbers.

        Parameters:
            brand_id: The brand identifier.

        Returns:
            User agent string matching the brand's persona.
        """
        if brand_id in self._cache:
            return self._cache[brand_id]

        # Try to load from database first
        try:
            db = Database()
            stored = db.query_one(
                "SELECT ua_string FROM user_agents WHERE brand_id = ?",
                (brand_id,)
            )
            if stored:
                self._cache[brand_id] = stored['ua_string']
                return stored['ua_string']
        except Exception:
            pass

        # Generate fresh UA
        ua = self._generate_ua(brand_id)
        self._cache[brand_id] = ua
        return ua

    def _generate_ua(self, brand_id: str) -> str:
        """
        Generates a fresh user agent string for a brand.

        Parameters:
            brand_id: The brand identifier.

        Returns:
            Generated user agent string.
        """
        persona = self.BRAND_PERSONAS.get(brand_id)
        if not persona:
            # Fallback for unknown brands
            return self._generate_chrome_ua('mac')

        os_type = persona['os']
        browser = persona['browser']

        if browser == 'chrome':
            return self._generate_chrome_ua(os_type)
        elif browser == 'safari':
            return self._generate_safari_ua(os_type)
        elif browser == 'firefox':
            return self._generate_firefox_ua(os_type)
        else:
            return self._generate_chrome_ua(os_type)

    def _get_chrome_version(self) -> str:
        """
        Calculates a current Chrome version based on the current date.

        Chrome releases roughly every 4 weeks. Version 120 was January 2024.

        Returns:
            Chrome version string like '132.0.0.0'.
        """
        now = datetime.now()
        # Chrome 120 was released ~Jan 2024
        months_since_120 = (now.year - 2024) * 12 + now.month
        chrome_major = 120 + months_since_120
        # Cap at a realistic max
        chrome_major = min(chrome_major, 145)
        return f"{chrome_major}.0.0.0"

    def _get_firefox_version(self) -> str:
        """
        Calculates a current Firefox version based on the current date.

        Firefox releases roughly every 4 weeks. Version 122 was January 2024.

        Returns:
            Firefox version string like '134.0'.
        """
        now = datetime.now()
        months_since_122 = (now.year - 2024) * 12 + now.month
        firefox_major = 122 + months_since_122
        firefox_major = min(firefox_major, 145)
        return f"{firefox_major}.0"

    def _get_safari_version(self) -> str:
        """
        Returns a current Safari/WebKit version string.

        Returns:
            Safari version string like '605.1.15'.
        """
        return "605.1.15"

    def _generate_chrome_ua(self, os_type: str) -> str:
        """
        Generates a Chrome user agent for the given OS.

        Parameters:
            os_type: Operating system ('mac', 'windows', 'linux').

        Returns:
            Chrome user agent string.
        """
        chrome_ver = self._get_chrome_version()

        if os_type == 'mac':
            os_str = random.choice(self.MAC_VERSIONS)
            return (
                f'Mozilla/5.0 (Macintosh; {os_str}) '
                f'AppleWebKit/537.36 (KHTML, like Gecko) '
                f'Chrome/{chrome_ver} Safari/537.36'
            )
        elif os_type == 'windows':
            os_str = random.choice(self.WINDOWS_VERSIONS)
            return (
                f'Mozilla/5.0 ({os_str}) '
                f'AppleWebKit/537.36 (KHTML, like Gecko) '
                f'Chrome/{chrome_ver} Safari/537.36'
            )
        elif os_type == 'linux':
            os_str = random.choice(self.LINUX_VERSIONS)
            return (
                f'Mozilla/5.0 ({os_str}) '
                f'AppleWebKit/537.36 (KHTML, like Gecko) '
                f'Chrome/{chrome_ver} Safari/537.36'
            )
        else:
            return self._generate_chrome_ua('mac')

    def _generate_safari_ua(self, os_type: str) -> str:
        """
        Generates a Safari user agent for the given OS.

        Parameters:
            os_type: Operating system ('mac', 'iphone').

        Returns:
            Safari user agent string.
        """
        safari_ver = self._get_safari_version()

        if os_type == 'iphone':
            os_str = random.choice(self.IPHONE_VERSIONS)
            return (
                f'Mozilla/5.0 ({os_str}) '
                f'AppleWebKit/{safari_ver} (KHTML, like Gecko) '
                f'Version/17.3 Mobile/15E148 Safari/604.1'
            )
        else:
            os_str = random.choice(self.MAC_VERSIONS)
            return (
                f'Mozilla/5.0 (Macintosh; {os_str}) '
                f'AppleWebKit/{safari_ver} (KHTML, like Gecko) '
                f'Version/17.3 Safari/{safari_ver}'
            )

    def _generate_firefox_ua(self, os_type: str) -> str:
        """
        Generates a Firefox user agent for the given OS.

        Parameters:
            os_type: Operating system ('windows', 'mac', 'linux').

        Returns:
            Firefox user agent string.
        """
        ff_ver = self._get_firefox_version()

        if os_type == 'windows':
            os_str = random.choice(self.WINDOWS_VERSIONS)
        elif os_type == 'mac':
            os_str = random.choice(self.MAC_VERSIONS)
        else:
            os_str = random.choice(self.LINUX_VERSIONS)

        return (
            f'Mozilla/5.0 ({os_str}; rv:{ff_ver}) '
            f'Gecko/20100101 Firefox/{ff_ver}'
        )

    def refresh_all(self) -> dict[str, str]:
        """
        Regenerates user agents for all brands and persists to database.

        Called monthly by cron job to keep version numbers current.

        Returns:
            Dictionary of brand_id: new_ua_string.

        Side effects:
            Updates user_agents table in the database.
            Clears the in-memory cache.
        """
        self._cache.clear()
        results = {}

        try:
            db = Database()
            for brand_id, persona in self.BRAND_PERSONAS.items():
                ua = self._generate_ua(brand_id)
                results[brand_id] = ua

                db.pool.write_with_lock(
                    """INSERT INTO user_agents (brand_id, ua_string, persona_os, persona_browser, generated_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(brand_id)
                       DO UPDATE SET ua_string = ?, generated_at = CURRENT_TIMESTAMP""",
                    (brand_id, ua, persona['os'], persona['browser'], ua)
                )
                self._cache[brand_id] = ua

            logger.info(f"Refreshed user agents for {len(results)} brands")
        except Exception as e:
            logger.error(f"Failed to refresh user agents: {e}")

        return results

    def get_all_uas(self) -> dict[str, str]:
        """
        Returns current user agents for all brands.

        Returns:
            Dictionary of brand_id: ua_string.
        """
        return {brand_id: self.get_ua(brand_id) for brand_id in self.BRAND_PERSONAS}
