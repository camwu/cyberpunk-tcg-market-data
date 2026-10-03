"""
Automated unit tests for scrape.py skip behavior, force override, and CLI options.
"""

import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch, MagicMock
from urllib.error import HTTPError, URLError

from scrape import (
    extract_date_from_build,
    fetch_json,
    fetch_text,
    is_valid_snapshot,
    run_scraper,
    MAX_RETRIES,
    RATE_LIMIT_DELAY,
)
import scrape


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

        with patch("scrape.fetch_json", side_effect=fake_fetch), \
             patch("scrape.fetch_text", return_value="2026-09-15T20:00:00+0000"):
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

        with patch("scrape.fetch_json", side_effect=fake_fetch), \
             patch("scrape.fetch_text", return_value="2026-09-16T20:00:00+0000"):
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

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)), \
             patch("scrape.fetch_text", return_value="2026-09-15T20:00:00+0000"):
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

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)), \
             patch("scrape.fetch_text", return_value="2026-09-15T20:00:00+0000"):
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

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)), \
             patch("scrape.fetch_text", return_value="2026-09-15T20:00:00+0000"):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 30.0)

    def test_aborts_when_no_prices_fetched(self):
        target_date = "2026-09-18"
        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One"}]},
            "92/100/prices": {"results": []},
        }
        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)), \
             patch("scrape.fetch_text", return_value="2026-09-18T20:00:00+0000"):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertFalse(success)

        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        self.assertFalse(os.path.exists(dated_file))

    def test_skips_product_fetch_when_group_modified_on_matches(self):
        target_date = "2026-09-17"
        cards_file = os.path.join(self.temp_path, "cards.json")
        initial_cards = {
            "_groups": {"100": {"name": "Set 1", "modifiedOn": "2026-09-01T12:00:00"}},
            "101": {"productId": 101, "name": "Card One", "groupId": 100}
        }
        with open(cards_file, "w", encoding="utf-8") as f:
            json.dump(initial_cards, f)

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1", "modifiedOn": "2026-09-01T12:00:00"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 42.0}]},
        }

        requested_endpoints = []

        def tracking_fetch(endpoint):
            requested_endpoints.append(endpoint)
            return mock_responses.get(endpoint)

        with patch("scrape.fetch_json", side_effect=tracking_fetch), \
             patch("scrape.fetch_text", return_value="2026-09-17T20:00:00+0000"):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        self.assertIn("92/groups", requested_endpoints)
        self.assertIn("92/100/prices", requested_endpoints)
        self.assertNotIn("92/100/products", requested_endpoints)

        dated_file = os.path.join(self.price_dir, f"{target_date}.json")
        with open(dated_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["tcgcsvBuild"], "2026-09-17T20:00:00+0000")
        self.assertEqual(saved_data["prices"]["101"]["Normal"]["marketPrice"], 42.0)

    def test_fetches_products_when_group_modified_on_differs(self):
        target_date = "2026-09-17"
        cards_file = os.path.join(self.temp_path, "cards.json")
        initial_cards = {
            "_groups": {"100": {"name": "Set 1", "modifiedOn": "2026-09-01T12:00:00"}},
            "101": {"productId": 101, "name": "Card One", "groupId": 100}
        }
        with open(cards_file, "w", encoding="utf-8") as f:
            json.dump(initial_cards, f)

        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1", "modifiedOn": "2026-09-02T15:00:00"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One Updated", "cleanName": "Card One Updated"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 45.0}]},
        }

        requested_endpoints = []

        def tracking_fetch(endpoint):
            requested_endpoints.append(endpoint)
            return mock_responses.get(endpoint)

        with patch("scrape.fetch_json", side_effect=tracking_fetch), \
             patch("scrape.fetch_text", return_value="2026-09-17T20:00:00+0000"):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=target_date)
            self.assertTrue(success)

        self.assertIn("92/groups", requested_endpoints)
        self.assertIn("92/100/prices", requested_endpoints)
        self.assertIn("92/100/products", requested_endpoints)

        with open(cards_file, "r", encoding="utf-8") as f:
            updated_cards = json.load(f)
        self.assertEqual(updated_cards["_groups"]["100"]["modifiedOn"], "2026-09-02T15:00:00")
        self.assertEqual(updated_cards["101"]["name"], "Card One Updated")

    def test_extracts_color_from_extended_data(self):
        target_date = "2026-09-17"
        cards_file = os.path.join(self.temp_path, "cards.json")
        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1", "modifiedOn": "2026-09-17T12:00:00"}]},
            "92/100/products": {
                "results": [
                    {
                        "productId": 101,
                        "name": "Card One",
                        "cleanName": "Card One",
                        "extendedData": [
                            {"name": "Number", "value": "B057"},
                            {"name": "Rarity", "value": "Epic"},
                            {"name": "Color", "value": "Yellow"},
                            {"name": "CardType", "value": "Legend"},
                        ],
                    }
                ]
            },
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 10.0}]},
        }

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)), \
             patch("scrape.fetch_text", return_value="2026-09-17T20:00:00+0000"):
            success = run_scraper(output_dir=self.price_dir, force=True, target_date=target_date)
            self.assertTrue(success)

        with open(cards_file, "r", encoding="utf-8") as f:
            saved_cards = json.load(f)
        self.assertEqual(saved_cards["101"]["color"], "Yellow")
        self.assertEqual(saved_cards["101"]["printNumber"], "B057")
        self.assertEqual(saved_cards["101"]["rarity"], "Epic")
        self.assertEqual(saved_cards["101"]["cardType"], "Legend")


class TestScraperDateDerivation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.price_dir = str(self.temp_path / "prices")
        os.makedirs(self.price_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_date_from_build_valid_formats(self):
        self.assertEqual(extract_date_from_build("2026-09-28T20:05:54+0000"), "2026-09-28")
        self.assertEqual(extract_date_from_build("2026-09-29T00:20:33Z"), "2026-09-29")
        self.assertEqual(extract_date_from_build("2026-09-30"), "2026-09-30")

    def test_extract_date_from_build_invalid_formats(self):
        self.assertIsNone(extract_date_from_build(None))
        self.assertIsNone(extract_date_from_build(""))
        self.assertIsNone(extract_date_from_build("invalid-date"))
        self.assertIsNone(extract_date_from_build("2026-99-99"))

    def test_run_scraper_derives_date_from_upstream_build_when_target_date_omitted(self):
        mock_responses = {
            "92/groups": {"results": [{"groupId": 100, "name": "Set 1"}]},
            "92/100/products": {"results": [{"productId": 101, "name": "Card One", "cleanName": "Card One"}]},
            "92/100/prices": {"results": [{"productId": 101, "subTypeName": "Normal", "marketPrice": 15.0}]},
        }

        with patch("scrape.fetch_json", side_effect=lambda ep: mock_responses.get(ep)), \
             patch("scrape.fetch_text", return_value="2026-09-28T20:05:54+0000"):
            success = run_scraper(output_dir=self.price_dir, force=False, target_date=None)
            self.assertTrue(success)

        expected_file = os.path.join(self.price_dir, "2026-09-28.json")
        self.assertTrue(os.path.isfile(expected_file))
        with open(expected_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["date"], "2026-09-28")
        self.assertEqual(data["tcgcsvBuild"], "2026-09-28T20:05:54+0000")


class TestScraperRetryLogic(unittest.TestCase):

    def _make_mock_response(self, content_bytes: bytes):
        mock_resp = MagicMock()
        mock_resp.read.return_value = content_bytes
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    @patch("scrape.time.sleep")
    @patch("scrape.urllib.request.urlopen")
    def test_fetch_text_recovers_on_transient_503(self, mock_urlopen, mock_sleep):
        err_503 = HTTPError(url="https://tcgcsv.com/test", code=503, msg="Service Unavailable", hdrs={}, fp=io.BytesIO(b""))
        mock_resp = self._make_mock_response(b"recovered text payload")
        mock_urlopen.side_effect = [err_503, mock_resp]

        result = fetch_text("https://tcgcsv.com/test")

        self.assertEqual(result, "recovered text payload")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0)])

    @patch("scrape.time.sleep")
    @patch("scrape.urllib.request.urlopen")
    def test_fetch_json_recovers_on_transient_429(self, mock_urlopen, mock_sleep):
        err_429 = HTTPError(url="https://tcgcsv.com/test", code=429, msg="Too Many Requests", hdrs={}, fp=io.BytesIO(b""))
        mock_resp = self._make_mock_response(b'{"results": [{"id": 1}]}')
        mock_urlopen.side_effect = [err_429, mock_resp]

        result = fetch_json("https://tcgcsv.com/test")

        self.assertEqual(result, {"results": [{"id": 1}]})
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0)])

    @patch("scrape.time.sleep")
    @patch("scrape.urllib.request.urlopen")
    def test_fetch_json_exhausts_retries_without_final_sleep(self, mock_urlopen, mock_sleep):
        err_500 = HTTPError(url="https://tcgcsv.com/test", code=500, msg="Internal Server Error", hdrs={}, fp=io.BytesIO(b""))
        mock_urlopen.side_effect = [err_500, err_500, err_500]

        result = fetch_json("https://tcgcsv.com/test")

        self.assertIsNone(result)
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0), call(2.0)])

    @patch("scrape.time.sleep")
    @patch("scrape.urllib.request.urlopen")
    def test_fetch_text_fast_fails_on_404_without_sleeping(self, mock_urlopen, mock_sleep):
        err_404 = HTTPError(url="https://tcgcsv.com/test", code=404, msg="Not Found", hdrs={}, fp=io.BytesIO(b""))
        mock_urlopen.side_effect = err_404

        result = fetch_text("https://tcgcsv.com/test")

        self.assertIsNone(result)
        self.assertEqual(mock_urlopen.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("scrape.time.sleep")
    @patch("scrape.urllib.request.urlopen")
    def test_fetch_text_recovers_on_urlerror_network_dropout(self, mock_urlopen, mock_sleep):
        url_err = URLError("Connection reset by peer")
        mock_resp = self._make_mock_response(b"socket recovered")
        mock_urlopen.side_effect = [url_err, mock_resp]

        result = fetch_text("https://tcgcsv.com/test")

        self.assertEqual(result, "socket recovered")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0)])

    @patch("scrape.time.sleep")
    @patch("scrape.urllib.request.urlopen")
    def test_fetch_text_exhausts_urlerror_without_final_sleep(self, mock_urlopen, mock_sleep):
        url_err = URLError("Network is unreachable")
        mock_urlopen.side_effect = [url_err, url_err, url_err]

        result = fetch_text("https://tcgcsv.com/test")

        self.assertIsNone(result)
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0), call(2.0)])


class TestScraperResilienceAndExceptions(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_valid_snapshot_corrupted_json_and_non_dict(self):
        corrupt_file = self.temp_path / "corrupt.json"
        corrupt_file.write_text("{malformed_json: true", encoding="utf-8")
        self.assertFalse(is_valid_snapshot(str(corrupt_file)))

        empty_file = self.temp_path / "empty.json"
        empty_file.write_text("", encoding="utf-8")
        self.assertFalse(is_valid_snapshot(str(empty_file)))

        array_file = self.temp_path / "array.json"
        array_file.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertFalse(is_valid_snapshot(str(array_file)))

        no_prices_file = self.temp_path / "no_prices.json"
        no_prices_file.write_text(json.dumps({"category": "Cyberpunk TCG"}), encoding="utf-8")
        self.assertFalse(is_valid_snapshot(str(no_prices_file)))

    def test_is_valid_snapshot_unexpected_error_bubbles(self):
        valid_file = self.temp_path / "test.json"
        valid_file.write_text(json.dumps({"prices": {"1": {}}}), encoding="utf-8")

        with patch("builtins.open", side_effect=RuntimeError("Disk hardware failure")):
            with self.assertRaises(RuntimeError):
                is_valid_snapshot(str(valid_file))

    def test_rate_limit_env_override(self):
        try:
            with patch.dict(os.environ, {"TCGCSV_RATE_LIMIT_DELAY": "0.75"}):
                reloaded = importlib.reload(scrape)
                self.assertEqual(reloaded.RATE_LIMIT_DELAY, 0.75)
        finally:
            importlib.reload(scrape)

    @patch("scrape.urllib.request.urlopen")
    def test_ssl_context_passed_to_urlopen(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"prices": {}}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        fetch_text("https://tcgcsv.com/test")
        self.assertIn("context", mock_urlopen.call_args.kwargs)
        self.assertIs(mock_urlopen.call_args.kwargs["context"], scrape._SSL_CONTEXT)

        fetch_json("https://tcgcsv.com/test")
        self.assertIn("context", mock_urlopen.call_args.kwargs)
        self.assertIs(mock_urlopen.call_args.kwargs["context"], scrape._SSL_CONTEXT)


if __name__ == "__main__":
    unittest.main()
