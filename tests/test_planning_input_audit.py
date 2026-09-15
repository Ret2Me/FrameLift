import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from telemetry_yield.planning.input_audit import audit_partial_input_metadata


def captured_metadata(*, gain: bool = True) -> str:
    parameters = {
        "soapy-rx-device": "driver=rtlsdr,serial=private-value",
    }
    if gain:
        parameters["gain"] = "28.0"
    return json.dumps(
        {
            "latitude": 52.2,
            "longitude": 21.0,
            "elevation": 100,
            "radio": {"name": "RTL-SDR", "parameters": parameters},
        }
    )


class InputMetadataAuditTests(unittest.TestCase):
    def fixture(self, root: Path, rows: list[dict[str, object]]) -> tuple[Path, Path]:
        raw = root / "raw"
        raw.mkdir()
        response = raw / "page.json"
        response.write_text(json.dumps(rows))
        snapshot = root / "snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "complete": False,
                    "total_rows_before_deduplication": len(rows),
                    "responses": [
                        {
                            "relative_path": response.name,
                            "byte_length": response.stat().st_size,
                            "sha256": hashlib.sha256(response.read_bytes()).hexdigest(),
                            "row_count": len(rows),
                        }
                    ],
                }
            )
        )
        return snapshot, raw

    def test_passes_without_accessing_outcomes(self):
        rows = [
            {
                "id": index + 1,
                "client_metadata": captured_metadata(),
                "waterfall_status": "with-signal" if index % 2 else "unknown",
                "demoddata": [{"payload": "must-not-be-inspected"}],
            }
            for index in range(10)
        ]
        with tempfile.TemporaryDirectory() as directory:
            snapshot, raw = self.fixture(Path(directory), rows)
            report = audit_partial_input_metadata(snapshot, raw)
        self.assertTrue(report["pass"])
        self.assertTrue(report["outcome_blind"])
        self.assertEqual(report["outcome_fields_accessed"], [])
        self.assertEqual(report["inspected_fields"], ["client_metadata", "id"])

    def test_fails_closed_on_low_coverage_and_tampered_raw_page(self):
        rows = [
            {"id": index + 1, "client_metadata": captured_metadata(gain=index < 5)}
            for index in range(8)
        ]
        rows.extend(
            {"id": index + 9, "client_metadata": None}
            for index in range(2)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot, raw = self.fixture(root, rows)
            report = audit_partial_input_metadata(snapshot, raw)
            self.assertFalse(report["pass"])
            self.assertFalse(report["coverage_checks"]["capture_time_location"])
            self.assertFalse(
                report["coverage_checks"]["capture_time_receiver_rf_gain"]
            )

            (raw / "page.json").write_text("[]")
            tampered = audit_partial_input_metadata(snapshot, raw)
        self.assertFalse(tampered["integrity_pass"])
        self.assertTrue(
            any("byte length mismatch" in item for item in tampered["integrity_errors"])
        )


if __name__ == "__main__":
    unittest.main()
