from datetime import UTC, datetime, timedelta
import unittest

from telemetry_yield.events import EventCandidate, same_transmission_event


def candidate(**updates):
    values = {
        "observation_id": "1",
        "norad_id": 123,
        "transmitter_uuid": "tx",
        "start_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "end_utc": datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        "station_id": "a",
    }
    values.update(updates)
    return EventCandidate(**values)


class EventTests(unittest.TestCase):
    def test_requires_timezone_aware_utc(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware UTC"):
            candidate(start_utc=datetime(2026, 1, 1))

    def test_time_overlap_alone_is_insufficient(self):
        self.assertFalse(same_transmission_event(candidate(), candidate(observation_id="2")))

    def test_shared_payload_is_sufficient_physical_evidence(self):
        left = candidate(payload_hashes=frozenset({"abc"}))
        right = candidate(observation_id="2", payload_hashes=frozenset({"abc"}))
        self.assertTrue(same_transmission_event(left, right))

    def test_doppler_consistency_is_sufficient(self):
        left = candidate()
        right = candidate(observation_id="2")
        self.assertTrue(same_transmission_event(left, right, doppler_tracks_consistent=True))

    def test_rejects_different_transmitter(self):
        left = candidate(payload_hashes=frozenset({"abc"}))
        right = candidate(transmitter_uuid="other", payload_hashes=frozenset({"abc"}))
        self.assertFalse(same_transmission_event(left, right))


if __name__ == "__main__":
    unittest.main()
