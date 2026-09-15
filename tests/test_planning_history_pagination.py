import json
import unittest

from telemetry_yield.planning.history import SatnogsHistorySource
from telemetry_yield.satnogs import SatNOGSResponse


class PagedHistoryClient:
    def __init__(self):
        self.calls = []

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        if len(self.calls) == 1:
            headers = {
                "Link": '<https://network.satnogs.org/api/observations/?cursor=x>; rel="next"'
            }
            identifier = 2
            waterfall = "without-signal"
        else:
            headers = {}
            identifier = 1
            waterfall = "with-signal"
        body = json.dumps(
            [
                {
                    "id": identifier,
                    "norad_cat_id": 25544,
                    "ground_station": 12,
                    "waterfall_status": waterfall,
                    "demoddata": [],
                }
            ]
        ).encode()
        return SatNOGSResponse(
            url="https://network.satnogs.org/api/observations/",
            body=body,
            status=200,
            headers=headers,
        )


class HistoryPaginationTests(unittest.TestCase):
    def test_history_follows_every_cursor_page_and_sorts_deduplicated_ids(self):
        client = PagedHistoryClient()
        evidence = SatnogsHistorySource(
            client, maximum_pages=2  # type: ignore[arg-type]
        ).load(25544)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual([item.signal_present for item in evidence], [True, False])

    def test_history_page_bound_fails_instead_of_silently_truncating(self):
        client = PagedHistoryClient()
        with self.assertRaisesRegex(ValueError, "page bound"):
            SatnogsHistorySource(
                client, maximum_pages=1  # type: ignore[arg-type]
            ).load(25544)


if __name__ == "__main__":
    unittest.main()
