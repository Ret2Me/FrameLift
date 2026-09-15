from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from telemetry_yield.archive_download import (
    DownloadCandidate,
    download_candidate,
    load_checkpoint_events,
    run_download_campaign,
    select_catalogue_iq,
    select_positive_frame_iq,
    select_zero_frame_iq,
)


PAYLOAD = bytes(range(256)) * 8


class RangeHandler(BaseHTTPRequestHandler):
    requests: list[str | None] = []
    ignore_range = False
    response_body_limit: int | None = None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        range_header = self.headers.get("Range")
        type(self).requests.append(range_header)
        if range_header and not type(self).ignore_range:
            start = int(range_header.removeprefix("bytes=").removesuffix("-"))
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
            )
        else:
            body = PAYLOAD
            self.send_response(200)
        if type(self).response_body_limit is not None:
            body = body[: type(self).response_body_limit]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ArchiveDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        RangeHandler.requests = []
        RangeHandler.ignore_range = False
        RangeHandler.response_body_limit = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/capture.iq"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def candidate(self, observation_id: int = 1) -> DownloadCandidate:
        return DownloadCandidate(observation_id, self.url, len(PAYLOAD), "FSK", "SAT")

    def test_catalogue_selection_includes_zero_frame_iq_even_without_size(self) -> None:
        def item(identifier: int, *, frames: bool, iq: bool, size: int) -> dict[str, object]:
            return {
                "observation_id": identifier,
                "frames_recovered": frames,
                "links": {"iq": self.url if iq else None},
                "artifacts": {"iq": {"size_bytes": size}},
                "mode": "GMSK",
                "satellite_id": "SAT",
            }

        selected = select_zero_frame_iq(
            {"observations": [item(3, frames=False, iq=True, size=0), item(2, frames=True, iq=True, size=5), item(1, frames=False, iq=True, size=5), item(4, frames=False, iq=False, size=5)]}
        )
        self.assertEqual([value.observation_id for value in selected], [1, 3])
        self.assertIsNone(selected[1].expected_size_bytes)

    def test_catalogue_selection_can_select_positive_or_all_iq(self) -> None:
        def item(identifier: int, frames: bool, iq: bool = True) -> dict[str, object]:
            return {
                "observation_id": identifier,
                "frames_recovered": frames,
                "links": {"iq": self.url if iq else None},
                "artifacts": {"iq": {"size_bytes": len(PAYLOAD)}},
            }

        catalogue = {
            "observations": [
                item(3, False),
                item(2, True),
                item(1, True),
                item(4, True, iq=False),
            ]
        }
        self.assertEqual(
            [value.observation_id for value in select_positive_frame_iq(catalogue)],
            [1, 2],
        )
        self.assertEqual(
            [value.observation_id for value in select_catalogue_iq(catalogue)],
            [1, 2, 3],
        )

    def test_resumes_partial_file_with_http_range(self) -> None:
        part = self.root / "observation_1.iq.part"
        part.write_bytes(PAYLOAD[:300])
        result = download_candidate(self.candidate(), self.root, retries=0)
        self.assertEqual(result.status, "downloaded")
        self.assertEqual(result.resumed_from_bytes, 300)
        self.assertEqual(RangeHandler.requests, ["bytes=300-"])
        final = self.root / "observation_1.iq"
        self.assertEqual(final.read_bytes(), PAYLOAD)
        self.assertEqual(result.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        self.assertFalse(part.exists())

    def test_server_ignoring_range_restarts_instead_of_appending(self) -> None:
        RangeHandler.ignore_range = True
        (self.root / "observation_1.iq.part").write_bytes(PAYLOAD[:100])
        result = download_candidate(self.candidate(), self.root, retries=0)
        self.assertEqual(result.status, "downloaded")
        self.assertEqual((self.root / "observation_1.iq").read_bytes(), PAYLOAD)
        self.assertEqual(RangeHandler.requests, ["bytes=100-"])

    def test_exact_local_file_is_hashed_and_skipped_without_http(self) -> None:
        final = self.root / "observation_1.iq"
        final.write_bytes(PAYLOAD)
        result = download_candidate(self.candidate(), self.root, retries=0)
        self.assertEqual(result.status, "skipped_valid")
        self.assertEqual(result.attempts, 0)
        self.assertEqual(RangeHandler.requests, [])
        self.assertEqual(result.sha256, hashlib.sha256(PAYLOAD).hexdigest())

    def test_campaign_checkpoints_and_continues_after_missing_size(self) -> None:
        checkpoint = self.root / "checkpoint.jsonl"
        manifest = self.root / "manifest.json"
        candidates = (
            DownloadCandidate(1, self.url, None),
            self.candidate(2),
        )
        results = run_download_campaign(
            candidates,
            self.root,
            checkpoint_path=checkpoint,
            manifest_path=manifest,
            retries=0,
        )
        self.assertEqual([item.status for item in results], ["error", "downloaded"])
        events = load_checkpoint_events(checkpoint)
        self.assertEqual([item["observation_id"] for item in events], [1, 2])
        snapshot = json.loads(manifest.read_text())
        self.assertEqual(snapshot["latest_result_count"], 2)
        self.assertEqual(snapshot["status_counts"], {"downloaded": 1, "error": 1})
        self.assertTrue((self.root / "observation_2.iq").exists())

    def test_short_responses_retry_from_each_new_partial_offset(self) -> None:
        RangeHandler.response_body_limit = 1000
        result = download_candidate(
            self.candidate(),
            self.root,
            retries=2,
            retry_backoff_seconds=0,
        )
        self.assertEqual(result.status, "downloaded")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(
            RangeHandler.requests, [None, "bytes=1000-", "bytes=2000-"]
        )
        self.assertEqual((self.root / "observation_1.iq").read_bytes(), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
