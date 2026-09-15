import random
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from telemetry_yield.planning.live_history import HistoryCursor, HistoryEvent, LiveHistoryStore, keys

T = datetime(2026, 1, 1, tzinfo=UTC)
TASK = "signal_present"


def event(oid=1, end=0, available=1, outcome=1, **kwargs):
    return HistoryEvent(f"satnogs:{oid}", TASK, T + timedelta(hours=end),
                        T + timedelta(hours=available), 100, 200, "tx", 45, 145e6,
                        outcome, **kwargs)


def target(**kwargs):
    return SimpleNamespace(**({"norad_id": 100, "station_id": 200, "transmitter_uuid": "tx",
                              "max_elevation_deg": 45, "frequency_hz": 145e6} | kwargs))


def snapshot(cursor, hour, row=None):
    now = T + timedelta(hours=hour)
    return cursor.snapshot(row or target(), as_of=now, pass_start=now + timedelta(days=10))


class LiveHistoryTests(unittest.TestCase):
    def cursor(self, events):
        return HistoryCursor(events, task=TASK, availability_kind="captured")

    def test_availability_not_end_controls_visibility(self):
        cursor = self.cursor([event(end=0, available=50)])
        self.assertEqual(snapshot(cursor, 49)["pair_count"], 0)
        self.assertEqual(snapshot(cursor, 50)["pair_count"], 1)

    def test_monthly_prediction_cannot_see_intervening_future_history(self):
        cursor = self.cursor([event(1, 0, 1), event(2, 24, 25)])
        result = snapshot(cursor, 2)
        self.assertEqual(result["global_count"], 1)
        self.assertEqual(result["prediction_lead_days"], 10)
        self.assertEqual(snapshot(cursor, 26)["global_count"], 2)

    def test_unknown_and_late_corrections_and_withdrawals(self):
        original = event(outcome=None)
        positive = replace(original, available_at=T + timedelta(hours=5), outcome=1)
        negative = replace(original, available_at=T + timedelta(hours=10), outcome=0)
        withdrawn = replace(original, available_at=T + timedelta(hours=15))
        cursor = self.cursor([original, positive, negative, withdrawn])
        self.assertEqual(snapshot(cursor, 2)["pair_count"], 0)
        self.assertEqual(snapshot(cursor, 6)["pair_rate"], 2 / 3)
        self.assertEqual(snapshot(cursor, 11)["pair_rate"], 1 / 3)
        self.assertEqual(snapshot(cursor, 16)["pair_count"], 0)

    def test_duplicate_retrieval_does_not_double_count(self):
        first = event()
        repeat = replace(first, available_at=T + timedelta(hours=2))
        cursor = self.cursor([first, first, repeat])
        self.assertEqual(snapshot(cursor, 3)["global_count"], 1)
        self.assertEqual(snapshot(cursor, 3)["global_last10_count"], 1)

    def test_conflicting_revision_is_rejected(self):
        with self.assertRaises(ValueError):
            self.cursor([event(), event(outcome=0)])

    def test_recent_windows_expire_but_cumulative_counts_survive(self):
        cursor = self.cursor([event()])
        self.assertEqual(snapshot(cursor, 24)["pair_1d_count"], 1)
        self.assertEqual(snapshot(cursor, 25)["pair_1d_count"], 0)
        self.assertEqual(snapshot(cursor, 25)["pair_count"], 1)

    def test_backfilled_old_events_do_not_appear_recent(self):
        cursor = self.cursor([event(available=1000)])
        value = snapshot(cursor, 1001)
        self.assertEqual(value["pair_30d_count"], 0)
        self.assertEqual(value["pair_count"], 1)
        self.assertGreater(value["pair_days_since_last"], 40)

    def test_other_station_and_other_satellite_are_disjoint(self):
        events = [event(), replace(event(2), station_id=201), replace(event(3), norad_id=101, outcome=0)]
        value = snapshot(self.cursor(events), 2)
        self.assertEqual(value["sat_other_station_7d_count"], 1)
        self.assertEqual(value["station_other_sat_7d_count"], 1)
        self.assertEqual(value["station_other_sat_7d_rate"], 1 / 3)
        self.assertEqual(value["pair_count"], 1)

    def test_missing_transmitter_does_not_pool_unrelated_unknowns(self):
        events = [replace(event(), transmitter_uuid=None)]
        value = snapshot(self.cursor(events), 2, target(transmitter_uuid=None))
        self.assertEqual(value["transmitter_count"], 0)
        self.assertEqual(value["link_count"], 0)
        self.assertEqual(value["pair_count"], 1)

    def test_no_backward_or_post_pass_query_and_no_naive_times(self):
        cursor = self.cursor([])
        snapshot(cursor, 2)
        with self.assertRaises(ValueError):
            snapshot(cursor, 1)
        with self.assertRaises(ValueError):
            cursor.snapshot(target(), as_of=T, pass_start=T)
        with self.assertRaises(ValueError):
            replace(event(), available_at=T.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            event(end=2, available=1)

    def test_assumed_labels_are_not_live_data(self):
        assumed = event(availability_kind="assumed_delay")
        with self.assertRaises(ValueError):
            self.cursor([assumed])
        with tempfile.TemporaryDirectory() as directory:
            store = LiveHistoryStore(Path(directory) / "history.sqlite")
            with self.assertRaises(ValueError):
                store.append([event(), assumed])
            self.assertEqual(store.events(TASK, T + timedelta(days=1)), ())
            store.close()

    def test_store_is_durable_idempotent_and_versioned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            store = LiveHistoryStore(path)
            self.assertEqual(store.append([event()]), 1)
            self.assertEqual(store.append([event()]), 0)
            store.append([replace(event(outcome=0), available_at=T + timedelta(hours=5))])
            store.close()
            store = LiveHistoryStore(path)
            self.assertEqual(len(store.events(TASK, T + timedelta(hours=2))), 1)
            self.assertEqual(len(store.events(TASK, T + timedelta(hours=6))), 2)
            store.db.execute("UPDATE events SET payload='{}'")
            with self.assertRaises(ValueError):
                store.events(TASK, T + timedelta(hours=6))
            store.close()

    def test_incremental_replay_matches_independent_brute_force(self):
        rng = random.Random(7538)
        events = []
        for oid in range(1, 101):
            end = rng.randint(0, 24 * 200)
            item = replace(event(oid, end, end + rng.randint(1, 500), rng.choice([0, 1, None])),
                           norad_id=rng.choice([100, 101]), station_id=rng.choice([200, 201]))
            events.append(item)
            if oid % 5 == 0:
                events.append(replace(item, available_at=item.available_at + timedelta(hours=100), outcome=rng.choice([0, 1, None])))
        cursor = self.cursor(events)
        for hour in range(0, 24 * 230, 61):
            now = T + timedelta(hours=hour)
            known = {}
            for item in sorted(events, key=lambda e: e.available_at):
                if item.available_at <= now:
                    known[item.observation_key] = item
            actual = snapshot(cursor, hour)
            for scope, key in keys(target()).items():
                selected = [e for e in known.values() if e.outcome is not None and key is not None and keys(e)[scope] == key]
                self.assertEqual(actual[f"{scope}_count"], len(selected))
                for days in (1, 7, 30, 90, 180):
                    recent = [e for e in selected if e.ended_at >= now - timedelta(days=days)]
                    self.assertEqual(actual[f"{scope}_{days}d_count"], len(recent))
                    self.assertEqual(actual[f"{scope}_{days}d_rate"], (sum(e.outcome for e in recent) + 1) / (len(recent) + 2))
