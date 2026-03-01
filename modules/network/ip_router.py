"""
Brand IP router for AutoFarm Zero — Success Guru Network v6.0.

Routes all outbound publishing API calls through per-brand Squid proxy
instances on the proxy-vm. Each brand publishes from a distinct source IP
to prevent platform cross-referencing of coordinated accounts.

RULE: Every outbound API call in publishing MUST use
BrandIPRouter.get_session(brand_id). Raw requests.get() is forbidden
in publishing modules.
"""

import os
import logging

import requests

from modules.network.ua_generator import UserAgentGenerator

logger = logging.getLogger(__name__)


class BrandIPRouter:
    """
    Routes outbound API calls through brand-specific Squid proxy instances.

    Each brand has a dedicated Squid proxy port on the proxy-vm, bound to
    a specific network interface IP. This ensures each brand publishes from
    a distinct public IP address, preventing platforms from detecting
    coordinated inauthentic behaviour.

    IP Groups:
        A (human + wealth):        Primary VNIC -> Public IP A
        B (zen + social):          VNIC B -> Public IP B
        C (habits + relationships): VNIC C -> Public IP C
    """

    # Brand to proxy port mapping
    BRAND_PROXY_MAP = {
        'human_success_guru': 3128,
        'wealth_success_guru': 3129,
        'zen_success_guru': 3130,
        'social_success_guru': 3131,
        'habits_success_guru': 3132,
        'relationships_success_guru': 3133,
    }

    # Brand to IP group mapping
    BRAND_IP_GROUPS = {
        'human_success_guru': 'A',
        'wealth_success_guru': 'A',
        'zen_success_guru': 'B',
        'social_success_guru': 'B',
        'habits_success_guru': 'C',
        'relationships_success_guru': 'C',
    }

    def __init__(self):
        """
        Initializes the BrandIPRouter.

        Side effects:
            Loads proxy VM IP from environment variables.
            Initializes the UserAgentGenerator.
            Loads per-brand proxy ports from env (with fallback to defaults).
        """
        self.proxy_vm_ip = os.getenv('PROXY_VM_INTERNAL_IP', '10.0.2.2')
        self.ua_generator = UserAgentGenerator()

        # Allow env overrides for proxy ports
        self.brand_ports = {}
        for brand_id, default_port in self.BRAND_PROXY_MAP.items():
            env_key = f"PROXY_PORT_{brand_id.upper()}"
            self.brand_ports[brand_id] = int(os.getenv(env_key, str(default_port)))

    def get_session(self, brand_id: str) -> requests.Session:
        """
        Returns a requests.Session configured to route through
        the brand's dedicated Squid proxy with the brand's user agent.

        Parameters:
            brand_id: The brand identifier (e.g. 'human_success_guru').

        Returns:
            requests.Session with proxy and user agent configured.

        Raises:
            ValueError: If brand_id is not recognized.

        Side effects:
            Creates a new Session with proxy and headers configured.
        """
        if brand_id not in self.brand_ports:
            raise ValueError(f"Unknown brand_id: {brand_id}")

        port = self.brand_ports[brand_id]
        proxy_url = f"http://{self.proxy_vm_ip}:{port}"

        session = requests.Session()
        session.proxies = {
            'http': proxy_url,
            'https': proxy_url,
        }

        # Set brand-specific user agent
        ua = self.ua_generator.get_ua(brand_id)
        session.headers.update({
            'User-Agent': ua,
        })

        logger.debug(
            "Created branded session",
            extra={
                'brand_id': brand_id,
                'proxy': proxy_url,
                'ip_group': self.BRAND_IP_GROUPS.get(brand_id),
            }
        )

        return session

    def get_proxy_url(self, brand_id: str) -> str:
        """
        Returns the proxy URL for a brand.

        Parameters:
            brand_id: The brand identifier.

        Returns:
            Proxy URL string (e.g. 'http://10.0.2.2:3128').
        """
        if brand_id not in self.brand_ports:
            raise ValueError(f"Unknown brand_id: {brand_id}")

        port = self.brand_ports[brand_id]
        return f"http://{self.proxy_vm_ip}:{port}"

    def get_ip_group(self, brand_id: str) -> str:
        """
        Returns the IP group letter for a brand.

        Parameters:
            brand_id: The brand identifier.

        Returns:
            IP group letter ('A', 'B', or 'C').
        """
        return self.BRAND_IP_GROUPS.get(brand_id, 'unknown')

    def verify_brand(self, brand_id: str) -> dict:
        """
        Verifies that a brand's proxy is working by making a test request.

        Parameters:
            brand_id: The brand identifier to verify.

        Returns:
            Dictionary with keys: brand_id, verified (bool), actual_source_ip,
            proxy_url, ip_group, error (if failed).
        """
        result = {
            'brand_id': brand_id,
            'verified': False,
            'proxy_url': self.get_proxy_url(brand_id),
            'ip_group': self.get_ip_group(brand_id),
        }

        try:
            session = self.get_session(brand_id)
            response = session.get(
                'https://api.ipify.org?format=json',
                timeout=15
            )
            response.raise_for_status()
            ip_data = response.json()
            result['actual_source_ip'] = ip_data.get('ip', 'unknown')
            result['verified'] = True

            logger.info(
                "Proxy verified",
                extra={
                    'brand_id': brand_id,
                    'source_ip': result['actual_source_ip'],
                    'ip_group': result['ip_group'],
                }
            )
        except Exception as e:
            result['error'] = str(e)
            logger.error(
                f"Proxy verification failed for {brand_id}: {e}",
                extra={'brand_id': brand_id}
            )

        return result

    def verify_all_brands(self) -> list[dict]:
        """
        Verifies all brand proxies and checks IP group separation.

        Returns:
            List of verification result dicts, one per brand.

        Side effects:
            Makes one HTTP request per brand through its proxy.
        """
        results = []
        for brand_id in self.brand_ports:
            result = self.verify_brand(brand_id)
            results.append(result)

        # Check IP group separation
        ip_groups: dict[str, set] = {}
        for r in results:
            if r['verified']:
                group = r['ip_group']
                if group not in ip_groups:
                    ip_groups[group] = set()
                ip_groups[group].add(r['actual_source_ip'])

        # Verify different groups have different IPs
        all_ips = set()
        for group, ips in ip_groups.items():
            if ips & all_ips:
                logger.warning(
                    f"IP overlap detected! Group {group} shares IPs with another group"
                )
            all_ips.update(ips)

        return results

    def get_status(self) -> dict:
        """
        Returns a summary of the IP routing configuration.

        Returns:
            Dictionary with brand_ports, ip_groups, and proxy_vm_ip.
        """
        return {
            'proxy_vm_ip': self.proxy_vm_ip,
            'brand_ports': dict(self.brand_ports),
            'ip_groups': dict(self.BRAND_IP_GROUPS),
        }
