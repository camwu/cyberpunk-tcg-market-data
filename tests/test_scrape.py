"""
Automated unit tests for scrape.py skip behavior, force override, and CLI options.
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scrape import run_scraper


class TestScraperSkipBehavior(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.price_dir = str(self.temp_path / "prices")
        os.makedirs(self.price_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_skips_when_snapshot_exists_without_force(self):
        target_date = "2026-09-15"
        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        initial_payload = {
            "date": target_date,
            "timestamp": "2026-09-15T12:00:00+00:00",
            "category": "Cyberpunk TCG",
            "categoryId": 92,
            "productCount": 10,
            "prices": {"101": {"Normal": {"marketPrice": 5.0}}},
        }
        with open(dated_file, "w", encoding="utf-8") as f:
            json.dump(initial_payload, f)

        with patch("scrape.fetch_json") as mock_fetch:
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)
            mock_fetch.assert_not_called()

        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["timestamp"], "2026-09-15T12:00:00+00:00")

    def test_overwrites_when_snapshot_exists_with_force(self):
        target_date = "2026-09-15"
        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        latest_file = os.path.join(self.price_dir, "latest.json")
        initial_payload = {
            "date": target_date,
            "timestamp": "2026-09-15T12:00:00+00:00",
            "category": "Cyberpunk TCG",
            "categoryId": 92,
            "productCount": 10,
            "prices": {"101": {"Normal": {"marketPrice": 5.0}}},
        }
        with open(dated_file, "w", encoding="utf-8") as f:
            json.dump(initial_payload, f)

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One", "cleanName": "Card One"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 12.0}]},
        }

        def fake_fetch(endpoint):
            return mock_responses.get(endpoint)

        with patch("scrape.fetch_json", side_effect=fake_fetch):
            success = run_scraper(output_dir=self.price_dir, force=True, target_date=target_date)
            self.assertTrue(success)

        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 12.0)
        self.assertNotEqual(saved_data["timestamp"], "2026-09-15T12:00:00+00:00")

        self.assertTrue(os.path.isfile(latest_file))
        with open(latest_file, "r", encoding="utf-8") as f:
            latest_data = json.load(f)
        self.assertEqual(latest_data["prices"]["101"]["Normal"]["marketPrice"], 12.0)

    def test_executes_normally_when_snapshot_does_not_exist(self):
        target_date = "2026-09-16"
        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        latest_file = os.path.join(self.price_dir, "latest.json")

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One", "cleanName": "Card One"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 8.5}]},
        }

        def fake_fetch(endpoint):
            return mock_responses.get(endpoint)

        with patch("scrape.fetch_json", side_effect=fake_fetch):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        self.assertTrue(os.path.isfile(dated_file))
        self.assertTrue(os.path.isfile(latest_file))
        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 8.5)

    def test_aborted_zero_byte_snapshot_triggers_scrape(self):
        target_date = "2026-09-15"
        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        # Create a 0-byte file
        with open(dated_file, "w", encoding="utf-8") as f:
            pass

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One", "cleanName": "Card One"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 15.0}]},
        }

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 15.0)

    def test_corrupted_json_snapshot_triggers_scrape(self):
        target_date = "2026-09-15"
        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        with open(dated_file, "w", encoding="utf-8") as f:
            f.write("{corrupted json: [")

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One", "cleanName": "Card One"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 20.0}]},
        }

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 20.0)

    def test_empty_prices_snapshot_triggers_scrape(self):
        target_date = "2026-09-15"
        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        with open(dated_file, "w", encoding="utf-8") as f:
            json.dump({"date": target_date, "prices": {}}, f)

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One", "cleanName": "Card One"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 30.0}]},
        }

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 30.0)


if __name__ == "__main__":
    unittest.main()
