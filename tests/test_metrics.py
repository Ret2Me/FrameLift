from __future__ import annotations

import unittest

from telemetry_yield.metrics import (
    false_accepts_per_hour,
    frames_per_cpu_second,
    unique_crc_frames,
)
from telemetry_yield.models import FrameRecord


class MetricsTests(unittest.TestCase):
    def test_unique_crc_frames_deduplicate_by_event_and_payload(self) -> None:
        frames = [
            FrameRecord("pass-1", "hash-a", True),
            FrameRecord("pass-1", "hash-a", True),
            FrameRecord("pass-1", "hash-b", True),
            FrameRecord("pass-2", "hash-a", True),
            FrameRecord("pass-2", "hash-c", False),
        ]
        self.assertEqual(unique_crc_frames(frames), 3)

    def test_false_accept_rate(self) -> None:
        self.assertEqual(false_accepts_per_hour(3, 1.5), 2.0)

    def test_false_accept_rate_rejects_zero_hours(self) -> None:
        with self.assertRaises(ValueError):
            false_accepts_per_hour(0, 0.0)

    def test_frames_per_cpu_second(self) -> None:
        self.assertEqual(frames_per_cpu_second(10, 2.5), 4.0)

    def test_frames_per_cpu_second_rejects_negative_count(self) -> None:
        with self.assertRaises(ValueError):
            frames_per_cpu_second(-1, 1.0)


if __name__ == "__main__":
    unittest.main()

