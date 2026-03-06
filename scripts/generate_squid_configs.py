"""
Generate Squid Configs — creates per-brand Squid proxy configurations.

Called during proxy-vm setup.  Reads network config from ``.env``.
Each brand gets its own Squid instance listening on a unique port and
bound to a specific VNIC private IP so that OCI NAT-routes traffic
through the correct public IP.

Usage::

    python scripts/generate_squid_configs.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BRAND_CONFIG = {
    "human_success_guru": {
        "port": os.getenv("PROXY_PORT_HUMAN_SUCCESS_GURU", "3128"),
        "interface_ip": os.getenv("PROXY_PRIVATE_IP_A"),
    },
    "wealth_success_guru": {
        "port": os.getenv("PROXY_PORT_WEALTH_SUCCESS_GURU", "3129"),
        "interface_ip": os.getenv("PROXY_PRIVATE_IP_A"),
    },
    "zen_success_guru": {
        "port": os.getenv("PROXY_PORT_ZEN_SUCCESS_GURU", "3130"),
        "interface_ip": os.getenv("PROXY_PRIVATE_IP_A"),
    },
    "social_success_guru": {
        "port": os.getenv("PROXY_PORT_SOCIAL_SUCCESS_GURU", "3131"),
        "interface_ip": os.getenv("PROXY_PRIVATE_IP_B"),
    },
    "habits_success_guru": {
        "port": os.getenv("PROXY_PORT_HABITS_SUCCESS_GURU", "3132"),
        "interface_ip": os.getenv("PROXY_PRIVATE_IP_B"),
    },
    "relationships_success_guru": {
        "port": os.getenv("PROXY_PORT_RELATIONSHIPS_SUCCESS_GURU", "3133"),
        "interface_ip": os.getenv("PROXY_PRIVATE_IP_B"),
    },
}

SQUID_TEMPLATE = """
http_port {port} name={brand_id}

tcp_outgoing_address {interface_ip}

acl localnet src {content_vm_ip}/32
acl SSL_ports port 443
acl Safe_ports port 80
acl Safe_ports port 443
acl CONNECT method CONNECT

http_access allow localnet
http_access deny all

cache deny all

access_log /var/log/squid/{brand_id}/access.log combined
cache_log /var/log/squid/{brand_id}/cache.log
pid_filename /var/run/squid/{brand_id}.pid

coredump_dir /var/spool/squid/{brand_id}
"""


def main() -> None:
    """Generate Squid configuration files for each brand.

    Side Effects
    ------------
    Writes squid.conf files to ``/etc/squid/{brand_id}/``.
    Creates necessary directories.
    """
    content_vm_ip = os.getenv("CONTENT_VM_PRIVATE_IP")
    if not content_vm_ip:
        print("ERROR: CONTENT_VM_PRIVATE_IP not set in .env")
        sys.exit(1)

    for brand_id, cfg in BRAND_CONFIG.items():
        if not cfg["interface_ip"]:
            print(f"WARNING: No interface IP for {brand_id} — skipping")
            continue

        config_dir = Path(f"/etc/squid/{brand_id}")
        config_dir.mkdir(parents=True, exist_ok=True)

        # Create log and spool directories
        Path(f"/var/log/squid/{brand_id}").mkdir(parents=True, exist_ok=True)
        Path(f"/var/spool/squid/{brand_id}").mkdir(parents=True, exist_ok=True)

        config_content = SQUID_TEMPLATE.format(
            port=cfg["port"],
            brand_id=brand_id,
            interface_ip=cfg["interface_ip"],
            content_vm_ip=content_vm_ip,
        ).strip()

        config_path = config_dir / "squid.conf"
        config_path.write_text(config_content)
        print(f"  Generated {config_path}")

    print("All Squid configs generated.")


if __name__ == "__main__":
    main()
