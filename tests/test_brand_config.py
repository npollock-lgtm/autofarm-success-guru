"""Tests for brand configuration — brands.json structure validation."""

import json
import os
import unittest


BRANDS_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "brands.json",
)

EXPECTED_BRANDS = [
    "human_success_guru",
    "wealth_success_guru",
    "zen_success_guru",
    "social_success_guru",
    "habits_success_guru",
    "relationships_success_guru",
]


class TestBrandsJson(unittest.TestCase):
    """Validate config/brands.json structure and content."""

    @classmethod
    def setUpClass(cls) -> None:
        with open(BRANDS_JSON_PATH, "r", encoding="utf-8") as fh:
            cls.config = json.load(fh)

    def test_file_loads(self) -> None:
        """brands.json should parse without errors."""
        self.assertIsInstance(self.config, dict)

    def test_network_name(self) -> None:
        """Network name should be present."""
        self.assertIn("network_name", self.config)
        self.assertEqual(self.config["network_name"], "Success Guru Network")

    def test_all_six_brands_present(self) -> None:
        """All 6 brands should be configured."""
        brands = self.config.get("brands", {})
        for brand_id in EXPECTED_BRANDS:
            self.assertIn(brand_id, brands, f"Missing brand: {brand_id}")

    def test_brand_has_required_fields(self) -> None:
        """Each brand should have display_name, niche, pillars, visual_identity."""
        brands = self.config.get("brands", {})
        required_keys = ["display_name", "niche", "pillars", "visual_identity"]
        for brand_id in EXPECTED_BRANDS:
            brand = brands.get(brand_id, {})
            for key in required_keys:
                self.assertIn(
                    key, brand,
                    f"{brand_id} missing required key: {key}",
                )

    def test_pillars_count(self) -> None:
        """Each brand should have at least 3 pillars."""
        brands = self.config.get("brands", {})
        for brand_id in EXPECTED_BRANDS:
            pillars = brands.get(brand_id, {}).get("pillars", [])
            self.assertGreaterEqual(
                len(pillars), 3,
                f"{brand_id} has fewer than 3 pillars",
            )

    def test_visual_identity_colors(self) -> None:
        """Visual identity should include primary and accent colors."""
        brands = self.config.get("brands", {})
        for brand_id in EXPECTED_BRANDS:
            vi = brands.get(brand_id, {}).get("visual_identity", {})
            self.assertIn("primary_color", vi, f"{brand_id} missing primary_color")
            self.assertIn("accent_color", vi, f"{brand_id} missing accent_color")

    def test_voice_persona_exists(self) -> None:
        """Each brand should have a voice_persona section."""
        brands = self.config.get("brands", {})
        for brand_id in EXPECTED_BRANDS:
            brand = brands.get(brand_id, {})
            self.assertIn(
                "voice_persona", brand,
                f"{brand_id} missing voice_persona",
            )

    def test_forbidden_words_defined(self) -> None:
        """Each brand's voice_persona should have forbidden_words."""
        brands = self.config.get("brands", {})
        for brand_id in EXPECTED_BRANDS:
            vp = brands.get(brand_id, {}).get("voice_persona", {})
            fw = vp.get("forbidden_words", [])
            self.assertIsInstance(fw, list, f"{brand_id} forbidden_words not a list")
            self.assertGreater(
                len(fw), 0,
                f"{brand_id} has no forbidden words",
            )


if __name__ == "__main__":
    unittest.main()
