import io
import json
import tempfile
import threading
import time
import unittest
import os
from pathlib import Path
from urllib.error import HTTPError

from urllib.request import Request

from telemetry_yield.satnogs import (
    SatNOGSError,
    SatNOGSClient,
    _SameOriginRedirectHandler,
    read_api_token_file,
)


class FakeResponse:
    def __init__(self, body=b"{}", *, status=200, headers=None, final_url=None):
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}
        self.closed = False
        self.final_url = final_url

    def read(self, size=-1):
        return self._stream.read(size)

    def close(self):
        self.closed = True

    def geturl(self):
        return self.final_url or "https://network.satnogs.org/api/observations/"


class SatNOGSClientTests(unittest.TestCase):
    def test_authenticated_get_sets_token_header_without_exposing_token_in_mode(self):
        requests = []

        def transport(request, timeout):
            requests.append(request)
            return FakeResponse(b"{}")

        client = SatNOGSClient(
            user_agent="telemetry-yield-test/contact@example.invalid",
            api_token="secret-token",
            transport=transport,
        )
        client.get("observations/")

        self.assertEqual(requests[0].get_header("Authorization"), "Token secret-token")
        self.assertEqual(client.authentication_mode, "satnogs-token")
        self.assertNotIn("secret-token", repr(client))

    def test_api_token_rejects_header_injection(self):
        with self.assertRaisesRegex(ValueError, "valid explicit"):
            SatNOGSClient(
                user_agent="telemetry-yield-test/contact@example.invalid",
                api_token="secret\r\nInjected: value",
            )

    def test_token_file_requires_private_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "token"
            token.write_text("secret-token\n", encoding="ascii")
            token.chmod(0o600)
            self.assertEqual(read_api_token_file(token), "secret-token")

            token.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "permissions"):
                read_api_token_file(token)

            token.chmod(0o600)
            link = root / "token-link"
            os.symlink(token, link)
            with self.assertRaisesRegex(ValueError, "regular file"):
                read_api_token_file(link)

    def test_shared_rate_file_serializes_independent_clients(self):
        clock = [100.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        def transport(_request, timeout):
            self.assertGreater(timeout, 0)
            return FakeResponse(b"{}")

        with tempfile.TemporaryDirectory() as directory:
            rate_file = Path(directory) / "observations.rate"
            clients = [
                SatNOGSClient(
                    user_agent="telemetry-yield-test/contact@example.invalid",
                    transport=transport,
                    sleep=sleep,
                    wall_time=lambda: clock[0],
                    shared_rate_limit_path=rate_file,
                    shared_minimum_interval_seconds=10,
                )
                for _ in range(2)
            ]
            clients[0].get("observations/")
            clients[1].get("observations/")
            persisted = float(rate_file.read_text().strip())

        self.assertEqual(sleeps, [10.0])
        self.assertEqual(persisted, 110.0)

    def test_shared_rate_interval_requires_state_path(self):
        with self.assertRaisesRegex(ValueError, "rate-limit path"):
            SatNOGSClient(
                user_agent="telemetry-yield-test/contact@example.invalid",
                shared_minimum_interval_seconds=1,
            )

    def test_requires_explicit_user_agent_and_bounded_concurrency(self):
        with self.assertRaises(ValueError):
            SatNOGSClient(user_agent="")
        with self.assertRaises(ValueError):
            SatNOGSClient(user_agent="project/contact", max_concurrency=3)

    def test_get_json_sets_user_agent_without_network(self):
        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(b'{"id": 42}')

        client = SatNOGSClient(
            user_agent="telemetry-yield-test/contact@example.invalid",
            transport=transport,
        )
        result = client.get_json("observations/", params={"id": 42})

        self.assertEqual(result, {"id": 42})
        self.assertEqual(
            requests[0][0].get_header("User-agent"),
            "telemetry-yield-test/contact@example.invalid",
        )
        self.assertEqual(requests[0][0].get_method(), "GET")

    def test_conditional_cache_uses_etag_and_last_modified(self):
        seen_headers = []
        responses = [
            FakeResponse(
                json.dumps({"id": 7}).encode(),
                headers={
                    "etag": '"abc"',
                    "last-modified": "Wed, 21 Oct 2015 07:28:00 GMT",
                    "content-type": "application/json",
                },
            ),
            FakeResponse(status=304),
        ]

        def transport(request, timeout):
            seen_headers.append(dict(request.header_items()))
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            client = SatNOGSClient(
                user_agent="telemetry-yield-test/contact@example.invalid",
                cache_dir=Path(directory),
                transport=transport,
            )
            first = client.get("observations/7/")
            second = client.get("observations/7/")

            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(second.body, first.body)
            self.assertEqual(seen_headers[1]["If-none-match"], '"abc"')
            self.assertEqual(
                seen_headers[1]["If-modified-since"],
                "Wed, 21 Oct 2015 07:28:00 GMT",
            )
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    def test_retries_429_and_honors_retry_after(self):
        calls = 0
        sleeps = []

        def transport(request, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "0"},
                    None,
                )
            return FakeResponse(b'{"ok": true}')

        client = SatNOGSClient(
            user_agent="telemetry-yield-test/contact@example.invalid",
            transport=transport,
            sleep=sleeps.append,
            backoff_jitter_seconds=0,
        )
        self.assertEqual(client.get_json("stations/"), {"ok": True})
        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, [0.0])

    def test_never_exceeds_two_in_flight_requests(self):
        lock = threading.Lock()
        active = 0
        maximum = 0

        def transport(request, timeout):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return FakeResponse(b"{}")

        client = SatNOGSClient(
            user_agent="telemetry-yield-test/contact@example.invalid",
            max_concurrency=2,
            transport=transport,
        )
        errors = []

        def run(index):
            try:
                client.get("observations/", params={"id": index})
            except Exception as exc:  # pragma: no cover - assertion aid
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertLessEqual(maximum, 2)
        self.assertGreaterEqual(maximum, 1)

    def test_origin_limit_is_shared_across_client_instances(self):
        lock = threading.Lock()
        active = 0
        maximum = 0

        def transport(request, timeout):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return FakeResponse()

        clients = [
            SatNOGSClient(
                user_agent=f"telemetry-yield-test-{index}/contact@example.invalid",
                max_concurrency=2,
                transport=transport,
            )
            for index in range(3)
        ]
        threads = [
            threading.Thread(target=client.get, args=("observations/",))
            for client in clients
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(maximum, 2)

    def test_rejects_cross_origin_url(self):
        client = SatNOGSClient(
            user_agent="telemetry-yield-test/contact@example.invalid",
            transport=lambda request, timeout: FakeResponse(),
        )
        with self.assertRaises(ValueError):
            client.get("https://example.org/api/observations/")

    def test_rejects_off_origin_redirect_response(self):
        client = SatNOGSClient(
            user_agent="telemetry-yield-test/contact@example.invalid",
            transport=lambda request, timeout: FakeResponse(
                final_url="https://example.org/api/observations/"
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            client.get("observations/")

    def test_default_redirect_handler_blocks_before_following(self):
        handler = _SameOriginRedirectHandler("https://network.satnogs.org/api/")
        request = Request("https://network.satnogs.org/api/observations/")
        with self.assertRaisesRegex(SatNOGSError, "origin"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://example.org/escape"
            )


if __name__ == "__main__":
    unittest.main()
