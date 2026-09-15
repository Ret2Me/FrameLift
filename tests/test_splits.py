import unittest

from telemetry_yield.splits import assert_no_event_leakage


class SplitTests(unittest.TestCase):
    def test_allows_event_in_one_split(self) -> None:
        assert_no_event_leakage([("event-1", "train"), ("event-1", "train")])

    def test_rejects_leakage(self) -> None:
        with self.assertRaisesRegex(ValueError, "event-1"):
            assert_no_event_leakage([("event-1", "train"), ("event-1", "test")])


if __name__ == "__main__":
    unittest.main()
