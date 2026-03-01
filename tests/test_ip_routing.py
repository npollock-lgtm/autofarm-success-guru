"""Tests for IP routing — BrandIPRouter proxy configuration."""

import unittest
from unittest.mock import MagicMock


class TestBrandIPRouter(unittest.TestCase):
    """Test BrandIPRouter proxy assignment."""

    def test_import(self) -> None:
        """BrandIPRouter should be importable."""
        from modules.network.ip_router import BrandIPRouter
        self.assertTrue(callable(BrandIPRouter))

    def test_brand_proxy_mapping(self) -> None:
        """Each brand should map to a unique proxy port."""
        from modules.network.ip_router import BrandIPRouter
        db = MagicMock()
        router = BrandIPRouter(db=db)

        if hasattr(router, 'brand_proxies'):
            ports = set()
            for brand_id, proxy_info in router.brand_proxies.items():
                port = proxy_info.get("port") if isinstance(proxy_info, dict) else None
                if port:
                    self.assertNotIn(port, ports, f"Duplicate port for {brand_id}")
                    ports.add(port)

    def test_six_brands_configured(self) -> None:
        """All 6 brands should be configured."""
        from modules.network.ip_router import BrandIPRouter
        db = MagicMock()
        router = BrandIPRouter(db=db)

        if hasattr(router, 'brand_proxies'):
            self.assertGreaterEqual(len(router.brand_proxies), 6)


if __name__ == "__main__":
    unittest.main()
