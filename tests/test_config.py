"""
Automated unit tests for configuration loading and path resolution.
"""

import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
