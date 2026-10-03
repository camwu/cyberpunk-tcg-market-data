"""Tests for centralized environment, filesystem, and transport constants."""

from pathlib import Path
import unittest

from tracker.constants import (
    REPO_ROOT,
    REPO_PRICES_DIR,
    REPO_CARDS_FILE,
    USER_AGENT,
    CACHE_TTL_24H_SECONDS,
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


if __name__ == "__main__":
    unittest.main()
