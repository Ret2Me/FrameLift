from __future__ import annotations

import unittest
from pathlib import Path

from telemetry_yield.sweep import (
    PARTIAL_SIGNAL_METRICS,
    AttemptCost,
    ProtocolSweep,
    SweepBudget,
    SweepBudgetExceeded,
    SweepPlanError,
    cartesian_count,
    estimate_l0_l1,
    iter_bounded_l0_l1,
    l0_l1_attempt_count,
    load_protocol_sweep,
)


FIXTURE = {
    "protocol_id": "fixture",
    "nominal": {"baud": 1200},
    "sweep": {
        "decoder": ["alpha", "beta"],
        "unused_axis": ["kept", "also_kept"],
        "cfo_hz": {"range": [-10, 10], "coarse_step": 10, "fine_step": 5},
        "bit_polarity": ["normal", "inverted"],
        "baud_rel_error": [-0.01, 0, 0.01],
    },
}


class SweepDefinitionTests(unittest.TestCase):
    def test_preserves_all_axes_and_required_partial_metrics(self) -> None:
        protocol = ProtocolSweep.from_mapping(FIXTURE)
        self.assertEqual(
            {axis.name for axis in protocol.axes},
            set(FIXTURE["sweep"]),
        )
        self.assertEqual(protocol.axis("unused_axis").cardinality(), 2)
        self.assertEqual(
            PARTIAL_SIGNAL_METRICS,
            (
                "n_syncwords_detected",
                "longest_clean_run_bits",
                "timing_err_variance",
                "preamble_corr_peak",
            ),
        )

    def test_counts_ranges_without_materializing_cartesian_product(self) -> None:
        protocol = ProtocolSweep.from_mapping(FIXTURE)
        self.assertEqual(protocol.axis("cfo_hz").cardinality(), 3)
        self.assertEqual(
            list(protocol.axis("cfo_hz").iter_values()),
            [-10, 0, 10],
        )
        self.assertEqual(protocol.axis("cfo_hz").cardinality(resolution="fine"), 5)
        self.assertEqual(l0_l1_attempt_count(protocol), 7)
        self.assertEqual(cartesian_count(protocol), 72)

    def test_invalid_unaligned_range_is_rejected(self) -> None:
        invalid = dict(FIXTURE)
        invalid["sweep"] = {"cfo_hz": {"range": [0, 10], "coarse_step": 3}}
        with self.assertRaisesRegex(SweepPlanError, "divisible"):
            ProtocolSweep.from_mapping(invalid)


class LazyPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = ProtocolSweep.from_mapping(FIXTURE)
        self.live = {
            "baud_rel_error": 0,
            "cfo_hz": 0,
            "bit_polarity": "normal",
            "decoder": "alpha",
            "unused_axis": "kept",
        }
        self.versions = {"alpha": "1.0", "beta": "2.0"}
        self.cost = AttemptCost(cpu_seconds=2.5, disk_bytes=100)
        self.budget = SweepBudget(
            max_attempts=7,
            max_cpu_seconds=17.5,
            max_disk_bytes=700,
        )

    def plan(self, **changes):
        arguments = {
            "protocol": self.protocol,
            "recording_id": "recording-1",
            "live_config": self.live,
            "decoder_versions": self.versions,
            "seed": 7,
            "max_level": 1,
            "budget": self.budget,
            "attempt_cost": self.cost,
        }
        arguments.update(changes)
        return iter_bounded_l0_l1(**arguments)

    def test_order_and_hashes_are_stable_across_mapping_order(self) -> None:
        first = list(self.plan())
        reordered = {
            "decoder": "alpha",
            "bit_polarity": "normal",
            "cfo_hz": 0,
            "baud_rel_error": 0,
            "unused_axis": "kept",
        }
        second = list(self.plan(live_config=reordered))
        first_keys = [
            (attempt.cascade_level, attempt.varied_axis, attempt.config_hash)
            for attempt in first
        ]
        second_keys = [
            (attempt.cascade_level, attempt.varied_axis, attempt.config_hash)
            for attempt in second
        ]
        self.assertEqual(first_keys, second_keys)
        self.assertEqual(len(first), 7)
        self.assertEqual(
            [attempt.varied_axis for attempt in first],
            [
                None,
                "baud_rel_error",
                "baud_rel_error",
                "cfo_hz",
                "cfo_hz",
                "bit_polarity",
                "decoder",
            ],
        )
        self.assertEqual(len({attempt.config_hash for attempt in first}), 7)

    def test_each_budget_dimension_rejects_before_first_attempt(self) -> None:
        budgets = (
            SweepBudget(6, 100, 10_000),
            SweepBudget(100, 17.4, 10_000),
            SweepBudget(100, 100, 699),
        )
        expected = ("attempts", "cpu_seconds", "disk_bytes")
        for budget, violation in zip(budgets, expected, strict=True):
            with self.subTest(violation=violation):
                iterator = self.plan(budget=budget)
                with self.assertRaises(SweepBudgetExceeded) as caught:
                    next(iterator)
                self.assertEqual(caught.exception.violations, (violation,))

    def test_l2_l3_materialization_is_rejected(self) -> None:
        for level in (2, 3):
            with self.subTest(level=level):
                with self.assertRaisesRegex(SweepPlanError, "only explicit L0/L1"):
                    next(self.plan(max_level=level))

    def test_missing_live_axis_is_rejected(self) -> None:
        incomplete = dict(self.live)
        del incomplete["cfo_hz"]
        with self.assertRaisesRegex(SweepPlanError, "missing configured axis"):
            next(self.plan(live_config=incomplete))


class CurrentProtocolTests(unittest.TestCase):
    def test_current_protocol_l0_l1_counts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        protocols = [
            load_protocol_sweep(path)
            for path in sorted((root / "configs/protocols").glob("*.yaml"))
        ]
        counts = {
            protocol.protocol_id: l0_l1_attempt_count(protocol)
            for protocol in protocols
        }
        afsk = next(
            protocol
            for protocol in protocols
            if protocol.protocol_id == "afsk1200_ax25"
        )
        self.assertEqual(next(afsk.axis("agc").iter_values()), "off")
        self.assertEqual(list(afsk.axis("tone_swap").iter_values()), [False, True])
        self.assertEqual(
            counts,
            {
                "afsk1200_ax25": 17,
                "bpsk1200": 209,
                "fsk9600_g3ruh": 94,
            },
        )
        estimate = estimate_l0_l1(
            protocols,
            recording_count=1,
            attempt_cost=AttemptCost(cpu_seconds=60, disk_bytes=2 * 1024**2),
        )
        self.assertEqual(estimate.attempts, 320)
        estimate.require_fits(SweepBudget(400, 24_000, 1024**3))


if __name__ == "__main__":
    unittest.main()
