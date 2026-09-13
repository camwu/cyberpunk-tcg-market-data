"""
Automated unit tests for configuration loader and path resolution.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tracker.config import TrackerConfig, resolve_collection_file, load_config


class TestTrackerConfig(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_config_instance(self):
        cfg = TrackerConfig()
        self.assertEqual(cfg.collection_csv, "data")
        self.assertEqual(cfg.database_path, "data/price_history.db")
        self.assertEqual(cfg.price_cache_dir, "prices")
        self.assertEqual(cfg.output_report, "LATEST_PORTFOLIO_SUMMARY.md")
        self.assertEqual(cfg.backup_dir, "data/backups")

    def test_resolve_explicit_file(self):
        csv_file = self.test_dir / "my_cards.csv"
        csv_file.write_text("dummy", encoding="utf-8")

        resolved = resolve_collection_file(str(csv_file), is_explicit_file=True)
        self.assertEqual(Path(resolved).resolve(), csv_file.resolve())

    def test_resolve_directory_picks_newest_csv(self):
        file1 = self.test_dir / "older.csv"
        file1.write_text("older", encoding="utf-8")
        time.sleep(0.05)

        file2 = self.test_dir / "newer.csv"
        file2.write_text("newer", encoding="utf-8")

        resolved = resolve_collection_file(str(self.test_dir))
        self.assertEqual(Path(resolved).resolve(), file2.resolve())

    def test_resolve_directory_ignores_backup_files(self):
        backup = self.test_dir / "backup_20260913.csv"
        backup.write_text("backup", encoding="utf-8")
        time.sleep(0.05)

        valid = self.test_dir / "active.csv"
        valid.write_text("valid", encoding="utf-8")

        # Even if backup has newer timestamp
        time.sleep(0.05)
        newer_backup = self.test_dir / "collection_backup.csv"
        newer_backup.write_text("newer backup", encoding="utf-8")

        resolved = resolve_collection_file(str(self.test_dir))
        self.assertEqual(Path(resolved).resolve(), valid.resolve())

    def test_load_config_from_custom_file(self):
        config_data = {
            "collection_csv": "custom_data",
            "database_path": "custom.db",
            "price_cache_dir": "custom_prices",
            "output_report": "CUSTOM_REPORT.md",
            "backup_dir": "custom_backups",
        }
        config_file = self.test_dir / "test_config.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")

        cfg = load_config(config_path=str(config_file))
        self.assertEqual(Path(cfg.database_path).name, "custom.db")
        self.assertEqual(Path(cfg.output_report).name, "CUSTOM_REPORT.md")

    def test_load_config_env_overrides(self):
        env_vars = {
            "CYBERPUNK_DATABASE_PATH": str(self.test_dir / "env.db"),
            "CYBERPUNK_OUTPUT_REPORT": str(self.test_dir / "ENV_REPORT.md"),
        }
        old_env = {}
        for k, v in env_vars.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            cfg = load_config()
            self.assertEqual(Path(cfg.database_path).name, "env.db")
            self.assertEqual(Path(cfg.output_report).name, "ENV_REPORT.md")
        finally:
            for k, prev in old_env.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

    def test_load_config_cli_overrides_precedence(self):
        cfg = load_config(
            database_path="cli_override.db",
            output_report="CLI_REPORT.md",
        )
        self.assertEqual(Path(cfg.database_path).name, "cli_override.db")
        self.assertEqual(Path(cfg.output_report).name, "CLI_REPORT.md")


if __name__ == "__main__":
    unittest.main()
