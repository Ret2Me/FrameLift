from datetime import UTC, datetime
from pathlib import Path
import unittest

from telemetry_yield.download_guard import DownloadPlan, assert_download_allowed


class DownloadGuardTests(unittest.TestCase):
    def test_large_batch_requires_plan(self):
        with self.assertRaises(PermissionError):
            assert_download_allowed(
                "source", 10_000_000_000, Path("/tmp/yield-data"),
                free_bytes=30_000_000_000,
            )

    def test_matching_approved_plan_allows_capacity_checked_batch(self):
        destination = Path("/tmp/yield-data")
        plan = DownloadPlan("test", 10_000_000_000, destination, True, datetime.now(UTC))
        assert_download_allowed(
            "test",
            10_000_000_000,
            destination,
            plan=plan,
            free_bytes=30_000_000_000,
        )

    def test_reserves_ten_gb_free(self):
        with self.assertRaises(OSError):
            assert_download_allowed(
                "source", 1_000, Path("/tmp/yield-data"),
                free_bytes=10_000_000_999,
            )

    def test_plan_is_bound_to_source_and_expiry(self):
        destination = Path("/tmp/yield-data")
        created = datetime(2026, 1, 1, tzinfo=UTC)
        plan = DownloadPlan("source-a", 10_000_000_000, destination, True, created)
        with self.assertRaisesRegex(PermissionError, "source_id"):
            assert_download_allowed(
                "source-b", 10_000_000_000, destination, plan=plan,
                free_bytes=30_000_000_000, now=created,
            )
        with self.assertRaisesRegex(PermissionError, "expired"):
            assert_download_allowed(
                "source-a", 10_000_000_000, destination, plan=plan,
                free_bytes=30_000_000_000, now=created.replace(day=9),
            )


if __name__ == "__main__":
    unittest.main()
