"""
Automated unit tests for market price synchronization, caching, and fallback behavior.
"""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tracker.sync import sync_market_prices


class TestSyncMarketPrices(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.price_dir = str(self.temp_path / "prices")
        self.fake_repo_prices = self.temp_path / "fake_repo_prices"
        os.makedirs(self.price_dir, exist_ok=True)
        os.makedirs(self.fake_repo_prices, exist_ok=True)

        # Populate a minimal cards.json
        with open(os.path.join(self.price_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_local_cache_hit_avoids_network(self):
        target_date = "2026-09-14"
        cache_file = os.path.join(self.price_dir, f"{target_date}.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"date": target_date, "prices": {}}, f)

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json") as mock_fetch:
            res = sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=False)
            self.assertEqual(res, cache_file)
            mock_fetch.assert_not_called()

    def test_repo_prices_sync_to_custom_dir(self):
        target_date = "2026-09-14"
        repo_file = self.fake_repo_prices / f"{target_date}.json"
        with open(repo_file, "w", encoding="utf-8") as f:
            json.dump({"date": target_date, "prices": {}}, f)

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json") as mock_fetch:
            res = sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=False)
            expected_target = os.path.join(self.price_dir, f"{target_date}.json")
            self.assertEqual(res, expected_target)
            self.assertTrue(os.path.isfile(expected_target))
            mock_fetch.assert_not_called()

    def test_remote_github_raw_download(self):
        target_date = "2026-09-14"
        remote_payload = {"date": target_date, "prices": {"100": {"Normal": {"marketPrice": 5.0}}}}

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json", return_value=remote_payload) as mock_fetch:
            res = sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=False)
            expected_file = os.path.join(self.price_dir, f"{target_date}.json")
            latest_file = os.path.join(self.price_dir, "latest.json")

            self.assertEqual(res, expected_file)
            self.assertTrue(os.path.isfile(expected_file))
            self.assertTrue(os.path.isfile(latest_file))
            mock_fetch.assert_called_once()

    def test_fallback_to_latest_json_when_unpublished_and_not_live(self):
        latest_file = os.path.join(self.price_dir, "latest.json")
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump({"date": "2026-09-14", "prices": {"101": {"Normal": {"marketPrice": 10.0}}}}, f)

        target_date = "2026-09-15"

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json", return_value=None):
            captured_out = io.StringIO()
            with patch("sys.stdout", captured_out):
                res = sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=False)

            self.assertEqual(res, latest_file)
            output = captured_out.getvalue()
            self.assertIn("Notice: Market prices for 2026-09-15 are not yet published remotely", output)
            self.assertIn("Proceeding with latest available price snapshot (2026-09-14)", output)

            today_target = os.path.join(self.price_dir, f"{target_date}.json")
            self.assertFalse(os.path.exists(today_target))

    def test_live_flag_triggers_tcgcsv_scrape_when_remote_missing(self):
        target_date = "2026-09-15"

        groups_resp = {"results": [{"groupId": 101, "name": "Set One"}]}
        prods_resp = {"results": [{"productId": 1, "name": "Card One", "cleanName": "Card One"}]}
        prices_resp = {"results": [{"productId": 1, "subTypeName": "Normal", "marketPrice": 25.0}]}

        def mock_fetch(url):
            if "groups" in url:
                return groups_resp
            if "products" in url:
                return prods_resp
            if "prices" in url:
                return prices_resp
            return None

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json", side_effect=mock_fetch), \
             patch("time.sleep"):
            res = sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=True)
            expected_file = os.path.join(self.price_dir, f"{target_date}.json")
            self.assertEqual(res, expected_file)
            self.assertTrue(os.path.isfile(expected_file))

    def test_missing_cache_and_fallback_raises_filenotfound(self):
        target_date = "2026-09-15"

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json", return_value=None):
            with self.assertRaises(FileNotFoundError) as ctx:
                sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=False)
            self.assertIn("Pass --live to scrape current prices", str(ctx.exception))

    def test_live_flag_skips_products_when_group_modified_on_matches(self):
        target_date = "2026-09-15"
        # Seed cards.json with existing group and card
        with open(os.path.join(self.price_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump({
                "_groups": {"101": {"name": "Set One", "modifiedOn": "2026-09-01T00:00:00"}},
                "1": {"productId": 1, "name": "Card One", "groupId": 101}
            }, f)

        groups_resp = {"results": [{"groupId": 101, "name": "Set One", "modifiedOn": "2026-09-01T00:00:00"}]}
        prices_resp = {"results": [{"productId": 1, "subTypeName": "Normal", "marketPrice": 30.0}]}
        requested_urls = []

        def mock_fetch(url):
            requested_urls.append(url)
            if "groups" in url:
                return groups_resp
            if "prices" in url:
                return prices_resp
            return None

        with patch("tracker.sync.REPO_PRICES_DIR", self.fake_repo_prices), \
             patch("tracker.sync.fetch_json", side_effect=mock_fetch), \
             patch("tracker.sync.fetch_text", return_value="2026-09-15T20:00:00+0000"), \
             patch("time.sleep"):
            res = sync_market_prices(price_dir=self.price_dir, target_date=target_date, force=False, live=True)
            expected_file = os.path.join(self.price_dir, f"{target_date}.json")
            self.assertEqual(res, expected_file)

        self.assertTrue(any("groups" in u for u in requested_urls))
        self.assertTrue(any("prices" in u for u in requested_urls))
        self.assertFalse(any("products" in u for u in requested_urls))

        with open(expected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["tcgcsvBuild"], "2026-09-15T20:00:00+0000")
        self.assertEqual(data["prices"]["1"]["Normal"]["marketPrice"], 30.0)


if __name__ == "__main__":
    unittest.main()
