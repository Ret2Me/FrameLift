import json
import unittest
from datetime import UTC, datetime

from telemetry_yield.planning.satnogs_adapter import SatnogsScheduleExport
from telemetry_yield.planning.satnogs_submit import (
    SatnogsSubmissionClient,
    SatnogsSubmissionAmbiguous,
    SatnogsSubmissionError,
    satnogs_export_from_dict,
    satnogs_export_sha256,
)


def export_fixture():
    return SatnogsScheduleExport(
        schema_version="satnogs-network-schedule-export-v1",
        api_path="observations/",
        api_payload=(
            {
                "start": "2026-09-03 10:00:00",
                "end": "2026-09-03 10:05:00",
                "ground_station": 12,
                "transmitter_uuid": "A" * 22,
            },
        ),
        audit=(
            {
                "opportunity_id": "pass-1",
                "norad_id": 25544,
                "tle_fingerprint": "f" * 64,
                "p_success": 0.8,
                "expected_unique_samples": 80.0,
                "plan_id": "plan-1",
                "plan_revision": 1,
                "plan_canonical_fingerprint": "e" * 64,
            },
        ),
    )


class FakeResponse:
    status = 201

    def __init__(self, payload=None):
        self.closed = False
        self.payload = [{"id": 1234}] if payload is None else payload

    def geturl(self):
        return "https://network.satnogs.org/api/observations/"

    def read(self, limit):
        return json.dumps(self.payload).encode("utf-8")

    def close(self):
        self.closed = True


class SatnogsSubmissionTests(unittest.TestCase):
    def test_submission_rejects_non_official_origin_and_past_jobs_before_transport(self):
        with self.assertRaisesRegex(ValueError, "official API origin"):
            SatnogsSubmissionClient(
                api_token="secret-token",
                user_agent="study/contact@example.org",
                base_url="https://example.org/api/",
            )
        calls = []

        def transport(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        export = export_fixture()
        client = SatnogsSubmissionClient(
            api_token="secret-token",
            user_agent="study/contact@example.org",
            transport=transport,
            now=lambda: datetime(2026, 9, 4, tzinfo=UTC),
        )
        with self.assertRaisesRegex(SatnogsSubmissionError, "future"):
            client.submit(
                export,
                confirmation_sha256=satnogs_export_sha256(export),
            )
        self.assertEqual(calls, [])

    def test_submission_is_bound_to_reviewed_digest_and_receipt_has_no_token(self):
        export = export_fixture()
        calls = []
        response = FakeResponse()

        def transport(request, timeout):
            calls.append((request, timeout))
            return response

        client = SatnogsSubmissionClient(
            api_token="secret-token",
            user_agent="study/contact@example.org",
            transport=transport,
            now=lambda: datetime(2026, 9, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(SatnogsSubmissionError, "digest mismatch"):
            client.submit(export, confirmation_sha256="0" * 64)
        self.assertEqual(calls, [])
        receipt = client.submit(
            export, confirmation_sha256=satnogs_export_sha256(export)
        )
        request = calls[0][0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), list(export.api_payload))
        self.assertEqual(receipt.response_observation_ids, (1234,))
        self.assertNotIn("secret-token", json.dumps(receipt.as_dict()))
        self.assertTrue(response.closed)

    def test_export_parser_rejects_mutable_or_empty_documents(self):
        document = export_fixture().as_dict()
        parsed = satnogs_export_from_dict(document)
        self.assertEqual(parsed, export_fixture())
        document["mode"] = "submit"
        with self.assertRaisesRegex(SatnogsSubmissionError, "dry-run"):
            satnogs_export_from_dict(document)
        document = export_fixture().as_dict()
        document["api_payload"][0]["unexpected"] = True
        with self.assertRaisesRegex(SatnogsSubmissionError, "unsupported"):
            satnogs_export_from_dict(document)
        document = export_fixture().as_dict()
        document["api_payload"][0]["ground_station"] = True
        with self.assertRaisesRegex(SatnogsSubmissionError, "invalid type"):
            satnogs_export_from_dict(document)
        document = export_fixture().as_dict()
        document["audit"][0]["p_success"] = "0.8"
        with self.assertRaisesRegex(SatnogsSubmissionError, "values are invalid"):
            satnogs_export_from_dict(document)
        document = export_fixture().as_dict()
        document["api_payload"].append(
            {
                **document["api_payload"][0],
                "start": "2026-09-03 10:04:00",
                "end": "2026-09-03 10:06:00",
            }
        )
        document["audit"].append(
            {
                **document["audit"][0],
                "opportunity_id": "pass-2",
            }
        )
        with self.assertRaisesRegex(SatnogsSubmissionError, "overlapping"):
            satnogs_export_from_dict(document)

    def test_submission_treats_boolean_response_id_as_ambiguous(self):
        export = export_fixture()

        def transport(request, timeout):
            return FakeResponse([{"id": True}])

        client = SatnogsSubmissionClient(
            api_token="secret-token",
            user_agent="study/contact@example.org",
            transport=transport,
            now=lambda: datetime(2026, 9, 2, tzinfo=UTC),
        )
        with self.assertRaisesRegex(SatnogsSubmissionAmbiguous, "identify"):
            client.submit(
                export,
                confirmation_sha256=satnogs_export_sha256(export),
            )


if __name__ == "__main__":
    unittest.main()
