"""Tests for centralized environment, filesystem, and transport constants."""

from pathlib import Path
import unittest

from tracker.constants import (
    REPO_ROOT,
    REPO_PRICES_DIR,
    REPO_CARDS_FILE,
    REPO_DATA_DIR,
    USER_AGENT,
    CACHE_TTL_24H_SECONDS,
    CANONICAL_RARITIES,
    RARITY_MAP,
    STAMP_VARIANTS,
    EXPANSION_ALIASES,
    normalize_rarity,
    normalize_stamp_variant,
    resolve_expansion_alias,
)
import tracker.config
import tracker.sync
import tracker.valuation
import tracker.cardnexus


class TestConstants(unittest.TestCase):
    def test_filesystem_constants(self):
        self.assertIsInstance(REPO_ROOT, Path)
        self.assertTrue(REPO_ROOT.is_dir())
        self.assertEqual(REPO_PRICES_DIR, REPO_ROOT / "prices")
        self.assertEqual(REPO_CARDS_FILE, REPO_ROOT / "cards.json")
        self.assertEqual(REPO_DATA_DIR, REPO_ROOT / "data")

    def test_transport_and_cache_constants(self):
        self.assertIsInstance(USER_AGENT, str)
        self.assertTrue(USER_AGENT.startswith("CyberpunkTCGMarketTracker"))
        self.assertEqual(CACHE_TTL_24H_SECONDS, 86400)

    def test_namespace_identity_in_consuming_modules(self):
        # tracker.config
        self.assertIs(tracker.config.REPO_ROOT, REPO_ROOT)
        self.assertIs(tracker.config.REPO_PRICES_DIR, REPO_PRICES_DIR)

        # tracker.sync
        self.assertIs(tracker.sync.REPO_ROOT, REPO_ROOT)
        self.assertIs(tracker.sync.REPO_PRICES_DIR, REPO_PRICES_DIR)
        self.assertIs(tracker.sync.REPO_CARDS_FILE, REPO_CARDS_FILE)
        self.assertIs(tracker.sync.USER_AGENT, USER_AGENT)

        # tracker.valuation
        self.assertIs(tracker.valuation.REPO_ROOT, REPO_ROOT)
        self.assertIs(tracker.valuation.REPO_PRICES_DIR, REPO_PRICES_DIR)
        self.assertIs(tracker.valuation.REPO_CARDS_FILE, REPO_CARDS_FILE)

        # tracker.cardnexus
        self.assertIs(tracker.cardnexus.USER_AGENT, USER_AGENT)
        self.assertIs(tracker.cardnexus.CACHE_TTL_24H_SECONDS, CACHE_TTL_24H_SECONDS)
        self.assertIs(tracker.cardnexus.REPO_DATA_DIR, REPO_DATA_DIR)

    def test_canonical_rarities_and_normalization(self):
        self.assertEqual(
            CANONICAL_RARITIES,
            ("Common", "Uncommon", "Rare", "Epic", "Secret", "Iconic", "Nova"),
        )
        # None and blank inputs return 'Unknown'
        self.assertEqual(normalize_rarity(None), "Unknown")
        self.assertEqual(normalize_rarity(""), "Unknown")
        self.assertEqual(normalize_rarity("   "), "Unknown")

        # Canonical shorthand mappings
        self.assertEqual(normalize_rarity("C"), "Common")
        self.assertEqual(normalize_rarity("c"), "Common")
        self.assertEqual(normalize_rarity("Common"), "Common")
        self.assertEqual(normalize_rarity("UC"), "Uncommon")
        self.assertEqual(normalize_rarity("uc"), "Uncommon")
        self.assertEqual(normalize_rarity("Uncommon"), "Uncommon")
        self.assertEqual(normalize_rarity("R"), "Rare")
        self.assertEqual(normalize_rarity("r"), "Rare")
        self.assertEqual(normalize_rarity("Rare"), "Rare")
        self.assertEqual(normalize_rarity("E"), "Epic")
        self.assertEqual(normalize_rarity("e"), "Epic")
        self.assertEqual(normalize_rarity("Epic"), "Epic")
        self.assertEqual(normalize_rarity("S"), "Secret")
        self.assertEqual(normalize_rarity("s"), "Secret")
        self.assertEqual(normalize_rarity("Secret"), "Secret")

        # Iconic variants
        self.assertEqual(normalize_rarity("IL"), "Iconic")
        self.assertEqual(normalize_rarity("il"), "Iconic")
        self.assertEqual(normalize_rarity("IO"), "Iconic")
        self.assertEqual(normalize_rarity("io"), "Iconic")
        self.assertEqual(normalize_rarity("IS"), "Iconic")
        self.assertEqual(normalize_rarity("is"), "Iconic")
        self.assertEqual(normalize_rarity("Iconic"), "Iconic")
        self.assertEqual(normalize_rarity("Iconic Legend"), "Iconic")
        self.assertEqual(normalize_rarity("Iconic Other"), "Iconic")
        self.assertEqual(normalize_rarity("Iconic Secret"), "Iconic")

        # Nova variants
        self.assertEqual(normalize_rarity("NR"), "Nova")
        self.assertEqual(normalize_rarity("nr"), "Nova")
        self.assertEqual(normalize_rarity("Nova"), "Nova")
        self.assertEqual(normalize_rarity("Nova Rare"), "Nova")

        # Unrecognized non-empty strings preserved as-is
        self.assertEqual(normalize_rarity("Promo"), "Promo")
        self.assertEqual(normalize_rarity("  Special Foil  "), "Special Foil")
        self.assertEqual(normalize_rarity("Sealed"), "Sealed")

    def test_stamp_variants_and_expansion_aliases(self):
        self.assertEqual(STAMP_VARIANTS, ("Brawl Champ", "Showdown Champ", "Top 4"))
        self.assertEqual(normalize_stamp_variant("Brawl Champ"), "Brawl Champ")
        self.assertEqual(normalize_stamp_variant("  brawl champ "), "Brawl Champ")
        self.assertEqual(normalize_stamp_variant("SHOWDOWN CHAMP"), "Showdown Champ")
        self.assertEqual(normalize_stamp_variant("top 4"), "Top 4")
        # Casual notes or typos return None
        self.assertIsNone(normalize_stamp_variant("one non-foil"))
        self.assertIsNone(normalize_stamp_variant("Brawl Chmap"))
        self.assertIsNone(normalize_stamp_variant(""))
        self.assertIsNone(normalize_stamp_variant(None))

        self.assertIn("night city showdown s1", EXPANSION_ALIASES)
        self.assertEqual(resolve_expansion_alias("Night City Showdown S1"), "Set 1 Promos")
        self.assertEqual(resolve_expansion_alias("  night city showdown s1 "), "Set 1 Promos")
        self.assertEqual(resolve_expansion_alias("Night City Brawl Season 1"), "Night City Brawl Season 1")


if __name__ == "__main__":
    unittest.main()
