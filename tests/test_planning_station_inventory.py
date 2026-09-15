import unittest
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from telemetry_yield.planning.station_inventory import (
    SatnogsStationInventoryProvider,
    StationInventoryError,
    station_from_satnogs,
)
from telemetry_yield.satnogs import SATNOGS_NETWORK_API


def station_payload():
    return {
        "id": 12,
        "name": "fixture",
        "lat": 52.2,
        "lng": 21.0,
        "altitude": 100,
        "min_horizon": 7,
        "is_connected": True,
        "is_available": True,
        "testing": False,
        "antenna": [
            {
                "frequency": 144_000_000,
                "frequency_max": 146_000_000,
                "antenna_type": "cross-yagi",
                "antenna_type_name": "Cross Yagi",
            },
            {
                "frequency": 430_000_000,
                "frequency_max": 440_000_000,
                "antenna_type": "cross-yagi",
                "antenna_type_name": "Cross Yagi",
            },
        ],
    }


class FakeClient:
    base_url = SATNOGS_NETWORK_API

    def get(self, path):
        self.path = path
        body = json.dumps(station_payload(), sort_keys=True).encode()
        return SimpleNamespace(body=body)


class StationInventoryTests(unittest.TestCase):
    def test_current_antenna_ranges_become_one_capacity_receiver(self):
        station = station_from_satnogs(station_payload())
        self.assertEqual(station.satnogs_station_id, 12)
        self.assertEqual(station.minimum_elevation_deg, 7)
        self.assertEqual(station.resources[0].capacity, 1)
        self.assertTrue(station.resources[0].supports(145_000_000, "GFSK"))
        self.assertFalse(station.resources[0].supports(300_000_000, "GFSK"))
        self.assertIsNone(station.resources[0].antenna.gain_dbi)

    def test_provider_retrieves_exact_detail_and_marks_configuration_current(self):
        client = FakeClient()
        snapshot = SatnogsStationInventoryProvider(
            client,  # type: ignore[arg-type]
            now=lambda: datetime(2026, 9, 2, tzinfo=UTC),
        ).fetch([12])
        self.assertEqual(client.path, "stations/12/")
        self.assertTrue(snapshot.current_configuration_only)
        self.assertEqual(snapshot.stations[0].satnogs_station_id, 12)
        self.assertEqual(snapshot.evidence_sha256_by_station[0][0], 12)
        self.assertEqual(len(snapshot.evidence_sha256_by_station[0][1]), 64)

    def test_testing_or_malformed_station_fails_closed(self):
        payload = station_payload()
        payload["testing"] = True
        with self.assertRaisesRegex(StationInventoryError, "testing"):
            station_from_satnogs(payload)
        payload = station_payload()
        payload["antenna"][0]["frequency_max"] = 1
        with self.assertRaisesRegex(StationInventoryError, "range"):
            station_from_satnogs(payload)
        payload = station_payload()
        payload["altitude"] = float("nan")
        with self.assertRaisesRegex(StationInventoryError, "finite"):
            station_from_satnogs(payload)
        payload = station_payload()
        payload["id"] = True
        with self.assertRaisesRegex(StationInventoryError, "positive integer"):
            station_from_satnogs(payload)
        payload = station_payload()
        payload["is_available"] = None
        with self.assertRaisesRegex(StationInventoryError, "unavailable"):
            station_from_satnogs(payload)
        payload = station_payload()
        payload["min_horizon"] = True
        with self.assertRaisesRegex(StationInventoryError, "numeric"):
            station_from_satnogs(payload)

    def test_provider_rejects_naive_inventory_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            SatnogsStationInventoryProvider(
                FakeClient(),  # type: ignore[arg-type]
                now=lambda: datetime(2026, 9, 2),
            ).fetch([12])

    def test_provider_can_exclude_transiently_unavailable_station(self):
        class ChangingClient:
            base_url = SATNOGS_NETWORK_API

            def get(self, path):
                payload = station_payload()
                payload["id"] = int(path.split("/")[1])
                if payload["id"] == 13:
                    payload["is_connected"] = False
                return SimpleNamespace(body=json.dumps(payload).encode())

        provider = SatnogsStationInventoryProvider(
            ChangingClient(),  # type: ignore[arg-type]
            now=lambda: datetime(2026, 9, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(StationInventoryError, "not connected"):
            provider.fetch([12, 13])
        snapshot = provider.fetch([12, 13], skip_unavailable=True)
        self.assertEqual(
            [station.satnogs_station_id for station in snapshot.stations],
            [12],
        )
        self.assertEqual(snapshot.excluded_station_ids, (13,))
        self.assertEqual(
            [station_id for station_id, _ in snapshot.evidence_sha256_by_station],
            [12],
        )


if __name__ == "__main__":
    unittest.main()
