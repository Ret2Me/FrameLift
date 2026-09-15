from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from telemetry_yield.archive_inventory import (
    build_archive_inventory,
    normalize_mode,
    write_archive_inventory,
)
from telemetry_yield.canonical import canonical_json


def observation(
    observation_id: int,
    mode: str,
    *,
    iq: bool = True,
    frames: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    frames = frames or []
    return {
        "observation_id": observation_id,
        "satellite_id": f"SAT-{observation_id}",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T00:01:00Z",
        "station": {"id": 1, "name": "station"},
        "frequency_hz": 437_000_000,
        "mode": mode,
        "links": {"iq": f"https://example.test/{observation_id}.iq" if iq else None},
        "artifacts": {
            "iq": (
                {
                    "format": "iq",
                    "object_key": f"observation_{observation_id}.iq",
                    "size_bytes": 16,
                }
                if iq
                else None
            )
        },
        "frames_recovered": bool(frames),
        "frame_count": len(frames),
        "frames": frames,
    }


def catalogue(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "generated_at": "2026-01-02T00:00:00Z",
        "source": "test archive",
        "excluded_bands": ["S", "X"],
        "excluded_station_ids": [],
        "observation_count": len(items),
        "observations": items,
    }


class ArchiveInventoryTests(unittest.TestCase):
    def test_normalization_does_not_infer_ax25_from_fsk(self) -> None:
        plain = normalize_mode("  gmsk ")
        explicit = normalize_mode("FSK AX.25 G3RUH")
        self.assertEqual(plain.normalized_mode, "gmsk")
        self.assertEqual(plain.protocol_hints, ())
        self.assertEqual(explicit.protocol_hints, ("ax25", "g3ruh"))

    def test_status_precedence_and_plugin_routing(self) -> None:
        result = build_archive_inventory(
            catalogue(
                [
                    observation(3, "BPSK", iq=False),
                    observation(2, "BPSK"),
                    observation(1, "FSK"),
                ]
            )
        )
        records = result["observations"]
        self.assertEqual([item["observation_id"] for item in records], [1, 2, 3])
        self.assertEqual(records[0]["status"], "supported_now")
        self.assertEqual(records[0]["route"]["protocol_strategy"], "protocol_neutral_probe_bank")
        self.assertEqual(records[0]["protocol_hints"], [])
        self.assertEqual(records[1]["status"], "needs_plugin")
        self.assertEqual(
            records[1]["route"]["required_plugins"], ["bpsk_demodulator"]
        )
        self.assertEqual(records[2]["status"], "no_iq")
        self.assertEqual(result["summary"]["iq_downloadable_count"], 2)
        self.assertEqual(result["summary"]["iq_object_key_available_count"], 2)

    def test_reference_payload_metadata_and_absence_are_distinct(self) -> None:
        payload_frame = {
            "payload_hex": "0102",
            "storage_available": True,
            "url": "https://example.test/frame",
        }
        metadata_frame = {
            "payload_hex": None,
            "storage_available": False,
            "url": None,
        }
        result = build_archive_inventory(
            catalogue(
                [
                    observation(1, "FSK", frames=[payload_frame]),
                    observation(2, "FSK", frames=[metadata_frame]),
                    observation(3, "FSK"),
                ]
            )
        )
        records = result["observations"]
        self.assertEqual(records[0]["reference_frames"]["status"], "payload_available")
        self.assertEqual(records[1]["reference_frames"]["status"], "metadata_only")
        self.assertEqual(records[2]["reference_frames"]["status"], "none")
        summary = result["summary"]
        self.assertEqual(summary["reference_payload_observation_count"], 1)
        self.assertEqual(summary["reference_metadata_only_observation_count"], 1)
        self.assertEqual(summary["no_reference_observation_count"], 1)

    def test_plan_is_deterministic_and_sorted_independently_of_input_order(self) -> None:
        first = catalogue([observation(2, "AFSK"), observation(1, "GFSK")])
        second = catalogue(list(reversed(first["observations"])))
        self.assertEqual(
            canonical_json(build_archive_inventory(first)),
            canonical_json(build_archive_inventory(second)),
        )

    def test_path_writer_binds_source_hash_and_is_repeatable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "catalogue.json"
            output = root / "inventory.json"
            source_bytes = json.dumps(
                catalogue([observation(1, "FSK")]), sort_keys=True
            ).encode()
            source.write_bytes(source_bytes)
            first = write_archive_inventory(source, output)
            first_bytes = output.read_bytes()
            second = write_archive_inventory(source, output)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, output.read_bytes())
            self.assertEqual(
                first["source"]["sha256"], hashlib.sha256(source_bytes).hexdigest()
            )

    def test_declared_count_and_duplicate_ids_fail_closed(self) -> None:
        bad_count = catalogue([observation(1, "FSK")])
        bad_count["observation_count"] = 2
        with self.assertRaisesRegex(ValueError, "observation_count"):
            build_archive_inventory(bad_count)
        duplicates = catalogue([observation(1, "FSK"), observation(1, "GMSK")])
        with self.assertRaisesRegex(ValueError, "unique"):
            build_archive_inventory(duplicates)


if __name__ == "__main__":
    unittest.main()
