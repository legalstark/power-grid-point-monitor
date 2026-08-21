"""核心单元与轻量集成测试，使用 Python 内置 unittest。"""

from __future__ import annotations

import random
import sys
import time
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from common import QUALITY_SB, quality_details  # noqa: E402
from monitor_app import MonitorState  # noqa: E402
from simulator_app import PointSimulator  # noqa: E402
from storage import HistoryStore  # noqa: E402


class FakeClock:
    """可控单调时钟，测试无需实际等待十秒。"""

    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += seconds
        return self.value


class SimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.simulator = PointSimulator(rng=random.Random(20260821), monotonic=self.clock)

    def test_creates_exact_monitoring_scope(self) -> None:
        points = self.simulator.snapshot()
        self.assertEqual(120, len(points))
        self.assertEqual(100, sum(p["pointType"] == "YX" for p in points))
        self.assertEqual(20, sum(p["pointType"] == "YC" for p in points))
        self.assertIsNotNone(self.simulator.snapshot("YX00"))
        self.assertIsNotNone(self.simulator.snapshot("YX99"))
        self.assertIsNotNone(self.simulator.snapshot("YC01"))
        self.assertIsNotNone(self.simulator.snapshot("YC20"))

    def test_snapshot_has_required_contract_fields(self) -> None:
        required = {
            "identifier", "stationId", "pointId", "pointType", "value", "unit",
            "qualityCode", "qualityFlags", "qualityText", "refreshedAt", "manualOverride",
        }
        self.assertTrue(required.issubset(self.simulator.snapshot("YC01")))
        self.assertEqual("A1.YC01", self.simulator.snapshot("YC01")["identifier"])

    def test_all_telemetry_changes_on_one_second_tick(self) -> None:
        before = {p["pointId"]: p["value"] for p in self.simulator.snapshot() if p["pointType"] == "YC"}
        summary = self.simulator.tick(self.clock.advance(1.01))
        after = {p["pointId"]: p["value"] for p in self.simulator.snapshot() if p["pointType"] == "YC"}
        self.assertTrue(summary["ycUpdated"])
        self.assertEqual(20, len(after))
        self.assertTrue(all(before[key] != after[key] for key in before))

    def test_one_random_signal_toggles_at_ten_seconds(self) -> None:
        before = {p["pointId"]: p["value"] for p in self.simulator.snapshot() if p["pointType"] == "YX"}
        summary = self.simulator.tick(self.clock.advance(10.01))
        after = {p["pointId"]: p["value"] for p in self.simulator.snapshot() if p["pointType"] == "YX"}
        changed = [key for key in before if before[key] != after[key]]
        self.assertEqual(1, len(changed))
        self.assertEqual(changed[0], summary["yxChanged"])

    def test_manual_telemetry_is_validated_and_held(self) -> None:
        manual = self.simulator.set_manual("YC01", 222.22)
        self.assertEqual(222.22, manual["value"])
        self.assertEqual(QUALITY_SB, manual["qualityCode"])
        self.simulator.tick(self.clock.advance(2.0))
        self.assertEqual(222.22, self.simulator.snapshot("YC01")["value"])
        with self.assertRaises(ValueError):
            self.simulator.set_manual("YC01", 9999)

    def test_manual_signal_accepts_only_binary_value(self) -> None:
        point = self.simulator.set_manual("YX00", 1)
        self.assertTrue(point["value"])
        with self.assertRaises(ValueError):
            self.simulator.set_manual("YX00", 2)

    def test_clear_manual_restores_good_quality(self) -> None:
        self.simulator.set_manual("YC02", 220)
        point = self.simulator.clear_manual("YC02")
        self.assertFalse(point["manualOverride"])
        self.assertEqual(0, point["qualityCode"])

    def test_quality_bit_description(self) -> None:
        flags, text = quality_details(0x20 | 0x40)
        self.assertEqual(["SB", "NT"], flags)
        self.assertIn("人工替代", text)
        self.assertIn("非当前值", text)

    def test_unknown_point_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            self.simulator.set_manual("YX100", 1)


class StorageAndMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_root = SOURCE_ROOT / "tests" / "runtime"
        self.db_path = runtime_root / "unit_history.db"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        self.store = HistoryStore(self.db_path, retention_seconds=60)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def test_storage_persists_and_queries_history(self) -> None:
        point = PointSimulator(rng=random.Random(1)).snapshot("YC01")
        self.store.insert_snapshots([point], collected_at=1000)
        items = self.store.query("YC01", 1, now=1010)
        self.assertEqual(1, len(items))
        self.assertEqual("YC01", items[0]["point_id"])

    def test_cleanup_removes_only_expired_rows(self) -> None:
        point = PointSimulator(rng=random.Random(1)).snapshot("YC01")
        self.store.insert_snapshots([point], collected_at=900)
        self.store.insert_snapshots([point], collected_at=990)
        self.assertEqual(1, self.store.cleanup(now=1000))
        self.assertEqual(1, self.store.count())

    def test_pagination_is_twenty_rows_and_six_pages(self) -> None:
        monitor = MonitorState("http://invalid", self.store)
        points = PointSimulator(rng=random.Random(1)).snapshot()
        with monitor.lock:
            monitor.cache = {point["pointId"]: point for point in points}
            monitor.connected = True
        result = monitor.list_points("", "", 1, 20)
        self.assertEqual(20, len(result["items"]))
        self.assertEqual(120, result["pagination"]["total"])
        self.assertEqual(6, result["pagination"]["totalPages"])

    def test_filter_and_keyword_search(self) -> None:
        monitor = MonitorState("http://invalid", self.store)
        points = PointSimulator(rng=random.Random(1)).snapshot()
        with monitor.lock:
            monitor.cache = {point["pointId"]: point for point in points}
        yc = monitor.list_points("YC", "YC0", 1, 20)
        self.assertEqual(9, yc["pagination"]["total"])
        self.assertTrue(all(item["pointType"] == "YC" for item in yc["items"]))

    def test_status_marks_disconnected_data_stale(self) -> None:
        monitor = MonitorState("http://invalid", self.store)
        self.assertTrue(monitor.status()["stale"])
        self.assertFalse(monitor.status()["connected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
