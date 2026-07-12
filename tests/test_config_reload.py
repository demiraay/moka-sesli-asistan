import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.admin_store import AdminStore
from core.config import Config

PROJECT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class TestConfigCacheRefresh(unittest.TestCase):
    """update_listing/delete_listing sonrasi Config onbelleginin tazelendigini dogrular."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        shutil.copytree(PROJECT_DATA_DIR, self.base_dir / "data")

        self.config = Config()
        self._original_data_dir = self.config.data_dir
        self.config.data_dir = str(self.base_dir / "data")
        self.config.load_data()

        self.store = AdminStore(
            base_dir=str(self.base_dir),
            db_path=str(self.base_dir / "admin_test.sqlite3"),
        )

    def tearDown(self):
        self.config.data_dir = self._original_data_dir
        self.config.load_data()
        self.temp_dir.cleanup()

    def test_update_listing_refreshes_config_cache(self):
        form_data = {
            "block_id": "A",
            "floor": 3,
            "door_number": "999",
            "flat_type_id": "FT-2P1",
            "status": "sold",
            "direction": "south",
            "list_price_try": 9_999_999,
            "sun_exposure": "high",
            "sun_hours_per_day": 8,
            "description": "test",
        }

        self.store.update_listing("INV-0001", form_data)

        cached = next(item for item in self.config.inventory if item["inventory_id"] == "INV-0001")
        self.assertEqual(cached["status"], "sold")
        cached_price = next(item for item in self.config.prices if item["inventory_id"] == "INV-0001")
        self.assertEqual(cached_price["list_price_try"], 9_999_999)

    def test_delete_listing_refreshes_config_cache(self):
        self.assertTrue(any(item["inventory_id"] == "INV-0002" for item in self.config.inventory))

        self.store.delete_listing("INV-0002")

        self.assertFalse(any(item["inventory_id"] == "INV-0002" for item in self.config.inventory))
        self.assertFalse(any(item["inventory_id"] == "INV-0002" for item in self.config.prices))

    def test_other_data_dir_leaves_config_cache_alone(self):
        with tempfile.TemporaryDirectory() as other:
            other_base = Path(other)
            shutil.copytree(PROJECT_DATA_DIR, other_base / "data")
            other_store = AdminStore(
                base_dir=str(other_base),
                db_path=str(other_base / "other.sqlite3"),
            )

            other_store.delete_listing("INV-0003")

            self.assertTrue(any(item["inventory_id"] == "INV-0003" for item in self.config.inventory))


if __name__ == "__main__":
    unittest.main()
