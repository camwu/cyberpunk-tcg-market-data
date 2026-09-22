"""
Unit tests for CardNexus API client, rate limiting, and collection synchronization.
"""

import gzip
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

from tracker.cardnexus import (
    CardNexusClient,
    CardNexusAPIError,
    sync_cardnexus_collection,
    STEADY_STATE_SLEEP_SECONDS,
)
from tracker.validation import REQUIRED_COLUMNS


def make_mock_response(status=200, headers=None, body=b""):
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.read.return_value = body if isinstance(body, bytes) else body.encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


class TestCardNexusClient(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_reads_explicit_or_env_key(self):
        with patch.dict(os.environ, {"CARDNEXUS_API_KEY": "env_key_123"}):
            client = CardNexusClient(cache_dir=str(self.cache_dir))
            self.assertEqual(client.api_key, "env_key_123")

        client_explicit = CardNexusClient(api_key="explicit_key_456", cache_dir=str(self.cache_dir))
        self.assertEqual(client_explicit.api_key, "explicit_key_456")

    def test_missing_key_raises_error_on_fetch(self):
        client = CardNexusClient(api_key=None, cache_dir=str(self.cache_dir))
        with patch.dict(os.environ, {}, clear=True):
            client.api_key = None
            with self.assertRaises(CardNexusAPIError):
                client.fetch_inventory()

    @patch("time.sleep", return_value=None)
    @patch("urllib.request.urlopen")
    def test_fetch_inventory_pagination_and_pacing(self, mock_urlopen, mock_sleep):
        page_1_data = {
            "data": [{"id": "item1", "productId": 101, "quantity": 1}],
            "pagination": {"nextCursor": "cursor_page_2"},
        }
        page_2_data = {
            "data": [{"id": "item2", "productId": 102, "quantity": 2}],
            "pagination": {"nextCursor": None},
        }

        resp_1 = make_mock_response(status=200, body=json.dumps(page_1_data))
        resp_2 = make_mock_response(status=200, body=json.dumps(page_2_data))

        mock_urlopen.side_effect = [resp_1, resp_2]

        client = CardNexusClient(api_key="test_key", cache_dir=str(self.cache_dir))
        items = client.fetch_inventory(include_marketplace=True)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["productId"], 101)
        self.assertEqual(items[1]["productId"], 102)
        mock_sleep.assert_called_with(STEADY_STATE_SLEEP_SECONDS)

        # Verify page 2 request contains quoted cursor
        req_2 = mock_urlopen.call_args_list[1][0][0]
        self.assertIn("cursor=cursor_page_2", req_2.full_url)

    @patch("time.sleep", return_value=None)
    @patch("urllib.request.urlopen")
    def test_fetch_inventory_for_sale_query_param(self, mock_urlopen, mock_sleep):
        mock_resp = make_mock_response(status=200, body=json.dumps({"data": [], "pagination": {}}))
        mock_urlopen.return_value = mock_resp

        client = CardNexusClient(api_key="test_key", cache_dir=str(self.cache_dir))

        # include_marketplace = True -> omit forSale
        client.fetch_inventory(include_marketplace=True)
        req_1 = mock_urlopen.call_args_list[0][0][0]
        self.assertNotIn("forSale=false", req_1.full_url)

        # include_marketplace = False -> pass forSale=false
        client.fetch_inventory(include_marketplace=False)
        req_2 = mock_urlopen.call_args_list[1][0][0]
        self.assertIn("forSale=false", req_2.full_url)

    @patch("time.sleep", return_value=None)
    @patch("urllib.request.urlopen")
    def test_rate_limit_429_retry_after(self, mock_urlopen, mock_sleep):
        headers_429 = {"retry-after": "3.5"}
        err_429 = urllib.error.HTTPError(
            url="https://api.test",
            code=429,
            msg="Too Many Requests",
            hdrs=headers_429,
            fp=io.BytesIO(b'{"code":"TOO_MANY_REQUESTS","message":"Rate limit exceeded"}')
        )

        mock_resp = make_mock_response(status=200, body=json.dumps({"data": [], "pagination": {}}))
        mock_urlopen.side_effect = [err_429, mock_resp]

        client = CardNexusClient(api_key="test_key", cache_dir=str(self.cache_dir))
        items = client.fetch_inventory()

        self.assertEqual(items, [])
        mock_sleep.assert_any_call(3.5)

    @patch("urllib.request.urlopen")
    def test_fetch_catalog_decompression_and_caching(self, mock_urlopen):
        exp_info = {"data": [{"id": 2675, "name": "Welcome to Night City - Beta", "slug": "welcome-to-night-city-beta"}]}
        resp_exp = make_mock_response(status=200, body=json.dumps(exp_info))

        feed_info = {"feedType": "catalog", "url": "https://s3.download/catalog.ndjson.gz"}
        resp_feed = make_mock_response(status=200, body=json.dumps(feed_info))

        ndjson_content = (
            json.dumps({"id": 501, "name": "Judy Alvarez", "expansionId": 2675, "printNumber": "001", "productType": "card"}) + "\n"
            + json.dumps({"id": 502, "name": "Welcome to Night City Booster Box", "expansionSlug": "welcome-to-night-city-beta", "printNumber": None, "productType": "sealed"}) + "\n"
        )
        compressed = gzip.compress(ndjson_content.encode("utf-8"))
        resp_s3 = make_mock_response(status=200, body=compressed)

        mock_urlopen.side_effect = [resp_exp, resp_feed, resp_s3]

        client = CardNexusClient(api_key="test_key", cache_dir=str(self.cache_dir))
        catalog = client.fetch_catalog(refresh=True)

        self.assertIn(501, catalog)
        self.assertEqual(catalog[501]["name"], "Judy Alvarez")
        self.assertEqual(catalog[501]["printNumber"], "001")
        self.assertEqual(catalog[502]["productType"], "sealed")

        cache_file = self.cache_dir / "cardnexus_catalog_cyberpunk.json"
        self.assertTrue(cache_file.is_file())

    def test_product_id_firewall_and_sealed_emission(self):
        client = CardNexusClient(api_key="test_key", cache_dir=str(self.cache_dir))
        raw_inventory = [
            {
                "id": "line1",
                "productId": 501,
                "quantity": 3,
                "finish": "Standard",
                "notes": "2026-09-02: 3",
            },
            {
                "id": "line2",
                "productId": 502,
                "quantity": 1,
                "finish": "Standard",
                "notes": "2026-09-05: 1",
            },
        ]
        catalog_map = {
            501: {
                "name": "Judy Alvarez",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "001",
                "productType": "card",
            },
            502: {
                "name": "Welcome to Night City Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "",
                "productType": "sealed",
            },
        }

        rows = client.transform_inventory_rows(raw_inventory, catalog_map)

        self.assertEqual(len(rows), 2)
        # Verify strict productId firewall: productId must NOT exist in output dict
        for row in rows:
            self.assertNotIn("productId", row)
            self.assertNotIn("id", row)
            self.assertTrue(REQUIRED_COLUMNS.issubset(set(row.keys())))

        # Card row verification
        self.assertEqual(rows[0]["name"], "Judy Alvarez")
        self.assertEqual(rows[0]["printNumber"], "001")
        self.assertEqual(rows[0]["totalQtyOwned"], "3")

        # Sealed row verification: printNumber is empty string
        self.assertEqual(rows[1]["name"], "Welcome to Night City Booster Box")
        self.assertEqual(rows[1]["printNumber"], "")
        self.assertEqual(rows[1]["totalQtyOwned"], "1")


class TestSyncCardNexusCollection(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.target_csv = str(self.test_dir / "active_collection.csv")
        self.backup_dir = str(self.test_dir / "backups")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch.object(CardNexusClient, "fetch_catalog")
    @patch.object(CardNexusClient, "fetch_inventory")
    def test_validation_failure_prevents_promotion(self, mock_fetch_inv, mock_fetch_cat):
        # Create invalid inventory: lot quantity mismatch (notes sum 5 != quantity 2)
        mock_fetch_inv.return_value = [
            {
                "id": "line_err",
                "productId": 999,
                "quantity": 2,
                "finish": "Standard",
                "notes": "2026-09-02: 5",
            }
        ]
        mock_fetch_cat.return_value = {
            999: {
                "name": "Mismatched Card",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "999",
                "productType": "card",
            }
        }

        # Create prior active collection to verify it remains untouched
        with open(self.target_csv, "w", encoding="utf-8") as f:
            f.write("name,expansion,printNumber,finish,totalQtyOwned,notes\nPrior,Welcome to Night City - Beta,001,Standard,1,2026-09-01\n")

        success, snapshot_path, total_units = sync_cardnexus_collection(
            target_csv=self.target_csv,
            backup_dir=self.backup_dir,
            api_key="test_key",
        )

        self.assertFalse(success)
        self.assertEqual(total_units, 0)
        # Prior active_collection.csv must not be touched
        with open(self.target_csv, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Prior", content)
        # Failed validation retains staging snapshot for inspection
        self.assertTrue(os.path.exists(snapshot_path))

    @patch.object(CardNexusClient, "fetch_catalog")
    @patch.object(CardNexusClient, "fetch_inventory")
    def test_successful_sync_promotes_and_backs_up(self, mock_fetch_inv, mock_fetch_cat):
        mock_fetch_inv.return_value = [
            {
                "id": "line_ok",
                "productId": 101,
                "quantity": 4,
                "finish": "Standard",
                "notes": "2026-09-02: 4",
            }
        ]
        mock_fetch_cat.return_value = {
            101: {
                "name": "Panam Palmer",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "010",
                "productType": "card",
            }
        }

        # Existing file before sync
        with open(self.target_csv, "w", encoding="utf-8") as f:
            f.write("name,expansion,printNumber,finish,totalQtyOwned,notes\nOld,Exp,001,Standard,1,2026-09-01\n")

        success, snapshot_path, total_units = sync_cardnexus_collection(
            target_csv=self.target_csv,
            backup_dir=self.backup_dir,
            api_key="test_key",
        )

        self.assertTrue(success)
        self.assertEqual(total_units, 4)
        # Staging snapshot must be removed after successful promotion
        self.assertFalse(os.path.exists(snapshot_path))

        # Verify active_collection.csv was promoted
        with open(self.target_csv, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Panam Palmer", content)
            self.assertNotIn("productId", content)

        # Verify backup was created
        backups = list(Path(self.backup_dir).glob("*.csv"))
        self.assertEqual(len(backups), 1)
        with open(backups[0], "r", encoding="utf-8") as f:
            backup_content = f.read()
            self.assertIn("Old", backup_content)


if __name__ == "__main__":
    unittest.main()
