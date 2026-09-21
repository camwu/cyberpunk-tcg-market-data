"""
Automated unit tests for configuration loading and path resolution.
"""

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tracker.config import load_config, resolve_collection_file, TrackerConfig


class TestTrackerConfig(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolve_collection_file_excludes_sealed_and_backup(self):
        backup_csv = self.test_dir / "active_collection_backup.csv"
        backup_csv.write_text("dummy", encoding="utf-8")

        sealed_csv = self.test_dir / "sealed_inventory.csv"
        sealed_csv.write_text("dummy", encoding="utf-8")

        collection_csv = self.test_dir / "active_collection.csv"
        collection_csv.write_text("dummy", encoding="utf-8")

        # Set older mtime on collection to ensure globbing doesn't just pick newest if sealed is newer
        os.utime(collection_csv, (1000, 1000))
        os.utime(sealed_csv, (2000, 2000))
        os.utime(backup_csv, (3000, 3000))

        resolved = resolve_collection_file(str(self.test_dir))
        self.assertEqual(Path(resolved).resolve(), collection_csv.resolve())

    def test_load_config_auto_discovers_adjacent_sealed_csv(self):
        empty_cfg = self.test_dir / "empty_cfg.json"
        empty_cfg.write_text("{}", encoding="utf-8")

        collection_file = self.test_dir / "my_cards.csv"
        collection_file.write_text("dummy", encoding="utf-8")

        sealed_file = self.test_dir / "sealed_inventory.csv"
        sealed_file.write_text("dummy", encoding="utf-8")

        cfg = load_config(config_path=str(empty_cfg), collection_csv=str(collection_file))
        self.assertIsNotNone(cfg.sealed_csv)
        self.assertEqual(Path(cfg.sealed_csv).resolve(), sealed_file.resolve())

    def test_load_config_cli_override_sealed_csv(self):
        empty_cfg = self.test_dir / "empty_cfg.json"
        empty_cfg.write_text("{}", encoding="utf-8")

        custom_sealed = self.test_dir / "custom_sealed.csv"
        custom_sealed.write_text("dummy", encoding="utf-8")

        cfg = load_config(config_path=str(empty_cfg), sealed_csv=str(custom_sealed))
        self.assertEqual(Path(cfg.sealed_csv).resolve(), custom_sealed.resolve())

    def test_load_config_default_price_cache_dir_anchors_to_repo_root(self):
        empty_cfg = self.test_dir / "non_existent_config.json"
        cfg = load_config(config_path=str(empty_cfg))
        repo_root = Path(__file__).resolve().parent.parent
        expected = str((repo_root / "prices").resolve())
        self.assertEqual(Path(cfg.price_cache_dir).resolve(), Path(expected).resolve())

    def test_load_config_resolves_cardnexus_api_key(self):
        # Environment variable resolution
        with patch.dict(os.environ, {"CARDNEXUS_API_KEY": "env_key_789"}):
            cfg = load_config(config_path=str(self.test_dir / "non_existent.json"))
            self.assertEqual(cfg.cardnexus_api_key, "env_key_789")

            # CLI override takes precedence over environment variable
            cfg_override = load_config(cardnexus_api_key="cli_override_key")
            self.assertEqual(cfg_override.cardnexus_api_key, "cli_override_key")

        # JSON file does not populate API key
        cfg_file = self.test_dir / "custom_key.json"
        cfg_file.write_text('{"cardnexus_api_key": "json_key_should_be_ignored"}', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            with patch("winreg.OpenKey", side_effect=FileNotFoundError):
                cfg_no_env = load_config(config_path=str(cfg_file))
                self.assertIsNone(cfg_no_env.cardnexus_api_key)


if __name__ == "__main__":
    unittest.main()
