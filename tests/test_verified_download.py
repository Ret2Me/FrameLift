from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from telemetry_yield.verified_download import (
    ArtifactSpec,
    DownloadManifest,
    download_artifact,
)


PAYLOAD = bytes(range(251)) * 64


class RangeHandler(BaseHTTPRequestHandler):
    requests: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802
        range_header = self.headers.get("Range")
        type(self).requests.append(range_header)
        if range_header:
            start = int(range_header.removeprefix("bytes=").removesuffix("-"))
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        else:
            body = PAYLOAD
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class VerifiedDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        RangeHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.spec = ArtifactSpec(
            "dataset.zip",
            f"http://127.0.0.1:{self.server.server_port}/dataset.zip",
            len(PAYLOAD),
            hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest(),
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def test_resumes_and_records_both_hashes(self) -> None:
        destination = self.root / "data"
        destination.mkdir()
        (destination / "dataset.zip.part").write_bytes(PAYLOAD[:1234])
        manifest = DownloadManifest(self.root / "manifest.json", (self.spec,))
        result = download_artifact(
            self.spec,
            destination,
            manifest=manifest,
            retries=0,
            checkpoint_bytes=1000,
        )
        self.assertEqual(result.status, "downloaded_verified")
        self.assertEqual(result.resumed_from_bytes, 1234)
        self.assertEqual(RangeHandler.requests, ["bytes=1234-"])
        self.assertEqual(result.md5, self.spec.expected_md5)
        self.assertEqual(result.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        snapshot = json.loads((self.root / "manifest.json").read_text())
        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["verified_count"], 1)

    def test_existing_file_is_reverified_without_network(self) -> None:
        destination = self.root / "data"
        destination.mkdir()
        (destination / "dataset.zip").write_bytes(PAYLOAD)
        result = download_artifact(self.spec, destination, retries=0)
        self.assertEqual(result.status, "skipped_verified")
        self.assertEqual(RangeHandler.requests, [])


if __name__ == "__main__":
    unittest.main()
