import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from telemetry_yield.planning.models import (
    AntennaSpec,
    Blocker,
    GeoBox,
    GroundStation,
    ObservationPlan,
    Opportunity,
    OrbitSample,
    PassWindow,
    ProbabilityEstimate,
    ReceiverResource,
    SatelliteTarget,
    TleSnapshot,
    TransmissionRule,
)
from telemetry_yield.planning.history import (
    evidence_from_local_rows,
    evidence_from_satnogs_observations,
)
from telemetry_yield.planning.monte_carlo import (
    CorrelatedRiskScenario,
    simulate_plan_yield,
    simulate_plan_yield_correlated,
)
from telemetry_yield.planning.engine import DynamicObservationPlanner
from telemetry_yield.planning.environment import (
    CompositeEnvironmentProvider,
    EnvironmentFeatureEnricher,
    EnvironmentSnapshot,
    RecordedEnvironmentProvider,
    RecordedEnvironmentWindow,
)
from telemetry_yield.planning.orbit import Sgp4PassPredictor
from telemetry_yield.planning.opportunities import OpportunityBuilder
from telemetry_yield.planning.probability import (
    HierarchicalBetaEstimator,
    ReceptionEvidence,
    ReceptionFeatures,
)
from telemetry_yield.planning.replanner import (
    ReplanPolicy,
    RollingPlanner,
    assess_tle_plan_impact,
)
from telemetry_yield.planning.satnogs_adapter import (
    SatnogsExportError,
    build_satnogs_schedule_export,
    reconcile_satnogs_jobs,
)
from telemetry_yield.planning.scheduler import MilpScheduler, SchedulePolicy
from telemetry_yield.planning.store import PlanningStore, plan_document, plan_from_document
from telemetry_yield.planning.tle import (
    CelesTrakTleProvider,
    TleError,
    diff_tle_snapshots,
    parse_tle_epoch,
)


TLE1 = "1 25544U 98067A   24123.50000000  .00016717  00000+0  30210-3 0  9997"
TLE1_NEW = "1 25544U 98067A   24124.50000000  .00016717  00000+0  30210-3 0  9998"
TLE2 = "2 25544  51.6400 100.0000 0005000  50.0000 310.0000 15.50000000450003"


def tle(line1=TLE1, fetched=None):
    fetched = fetched or datetime(2024, 5, 3, tzinfo=UTC)
    return TleSnapshot(
        norad_id=25544,
        name="ISS",
        line1=line1,
        line2=TLE2,
        epoch=parse_tle_epoch(line1),
        fetched_at=fetched,
        source="fixture",
    )


def probability(value=0.8):
    return ProbabilityEstimate(
        p_transmit=1.0,
        p_decode_given_transmit=value,
        p_success=value,
        standard_deviation=0.05,
        lower_90=max(0, value - 0.1),
        upper_90=min(1, value + 0.1),
        model_version="test",
    )


def opportunity(identifier, start, end, value, *, fingerprint=None, resource="rx"):
    p = probability(value)
    return Opportunity(
        opportunity_id=identifier,
        norad_id=25544,
        satellite_name="ISS",
        station_id="gs",
        resource_id=resource,
        start=start,
        end=end,
        tle_fingerprint=fingerprint or tle().fingerprint,
        probability=p,
        nominal_unique_samples=100,
        expected_unique_samples=100 * value,
        priority=1,
        max_elevation_deg=50,
        min_range_km=500,
        frequency_hz=145_800_000,
        transmitter_uuid="A" * 22,
        satnogs_station_id=12,
    )


class FakePredictor:
    def __init__(self, pass_window):
        self.pass_window = pass_window

    def predict(self, tle, station, start, end, *, carrier_frequency_hz=None):
        return (self.pass_window,)


class StaticTleProvider:
    def fetch(self, norad_id, *, now=None):
        return tle(fetched=now)


class ObservationPlanningTests(unittest.TestCase):
    def test_tle_epoch_rejects_day_366_in_a_non_leap_year(self):
        with self.assertRaisesRegex(ValueError, "day-of-year"):
            parse_tle_epoch(
                "1 25544U 98067A   23366.50000000  .00016717  00000+0  30210-3 0  9997"
            )

    def test_probability_factorization_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "conditional factors"):
            ProbabilityEstimate(
                p_transmit=0.5,
                p_decode_given_transmit=0.5,
                p_success=0.5,
                standard_deviation=0.1,
                lower_90=0.2,
                upper_90=0.8,
            )

    def test_hardware_and_target_metadata_reject_non_finite_values(self):
        with self.assertRaisesRegex(ValueError, "gain_dbi"):
            AntennaSpec("bad", gain_dbi=float("nan"))
        with self.assertRaisesRegex(ValueError, "frequency range"):
            AntennaSpec("bad", ((float("nan"), 146_000_000),))
        with self.assertRaisesRegex(ValueError, "altitude_m"):
            GroundStation(
                "gs",
                52,
                21,
                float("nan"),
                (ReceiverResource("rx"),),
            )
        with self.assertRaisesRegex(ValueError, "transmit_power_dbw"):
            SatelliteTarget(25544, "ISS", transmit_power_dbw=float("inf"))

    def test_tle_provider_versions_and_validates_catalog_number(self):
        seen = []

        def fetch(url, headers, timeout, limit):
            seen.append((url, headers, timeout, limit))
            return f"ISS\n{TLE1}\n{TLE2}\n".encode()

        provider = CelesTrakTleProvider(user_agent="test/contact", fetch_bytes=fetch)
        snapshot = provider.fetch(25544, now=datetime(2024, 5, 3, tzinfo=UTC))
        self.assertEqual(snapshot.epoch, datetime(2024, 5, 2, 12, tzinfo=UTC))
        self.assertIn("CATNR=25544", seen[0][0])
        fresh = tle(TLE1_NEW)
        change = diff_tle_snapshots({25544: snapshot}, {25544: fresh})[0]
        self.assertTrue(change.changed)
        self.assertEqual(change.epoch_shift_seconds, 86400)

        def corrupted(url, headers, timeout, limit):
            return f"ISS\n{TLE1[:-1]}0\n{TLE2}\n".encode()

        with self.assertRaisesRegex(TleError, "checksum"):
            CelesTrakTleProvider(
                user_agent="test/contact", fetch_bytes=corrupted
            ).fetch(25544, now=datetime(2024, 5, 3, tzinfo=UTC))

    def test_tle_provider_retries_transient_timeouts_with_bounded_backoff(self):
        attempts = 0
        delays = []

        def flaky(_url, _headers, _timeout, _limit):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("temporary CelesTrak timeout")
            return f"ISS\n{TLE1}\n{TLE2}\n".encode()

        snapshot = CelesTrakTleProvider(
            user_agent="test/contact",
            fetch_bytes=flaky,
            max_retries=2,
            backoff_seconds=0.25,
            sleep=delays.append,
        ).fetch(25544, now=datetime(2024, 5, 3, tzinfo=UTC))
        self.assertEqual(snapshot.norad_id, 25544)
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_transmission_rule_supports_calendar_and_ground_region(self):
        rule = TransmissionRule(
            weekdays_utc=(4,),
            daily_windows_utc=((time(10), time(12)),),
            transmit_regions=(GeoBox(-5, 5, 170, -170),),
        )
        friday = datetime(2024, 5, 3, 11, tzinfo=UTC)
        self.assertTrue(
            rule.allows(
                friday,
                subsatellite_latitude_deg=0,
                subsatellite_longitude_deg=179,
            )
        )
        self.assertFalse(
            rule.allows(
                friday + timedelta(days=1),
                subsatellite_latitude_deg=0,
                subsatellite_longitude_deg=179,
            )
        )

    def test_probability_estimate_survives_missing_inputs_and_ignores_unknown_negative(self):
        estimator = HierarchicalBetaEstimator()
        sparse = ReceptionFeatures(
            norad_id=25544,
            station_id="gs",
            resource_id="rx",
            max_elevation_deg=40,
            min_range_km=500,
            duration_seconds=600,
            tle_age_hours=12,
        )
        unknown = ReceptionEvidence(
            norad_id=25544,
            station_id="gs",
            resource_id="rx",
            listened=True,
            transmitter_state="unknown",
            decoded=False,
            source="satnogs",
        )
        estimate = estimator.estimate(sparse, [unknown], transmitter_probability=0.8)
        self.assertGreater(estimate.p_success, 0)
        self.assertEqual(estimate.evidence_count, 0)
        self.assertEqual(estimate.p_signal_present, estimate.p_transmit)
        self.assertEqual(
            estimate.p_decode_given_signal, estimate.p_decode_given_transmit
        )
        self.assertEqual(estimate.model_version, "hierarchical-beta-signal-v2")
        complete = replace(
            sparse,
            frequency_hz=145_800_000,
            modulation="FSK",
            baud=9600,
            transmit_power_dbw=3,
            transmit_antenna_gain_dbi=1,
            receive_antenna_gain_dbi=12,
            system_noise_temperature_k=300,
            atmospheric_attenuation_db=0.2,
            space_weather_kp=2,
            ionospheric_scintillation_index=0.1,
            air_temperature_c=12,
            relative_humidity_percent=75,
            required_snr_db=6,
        )
        precise = estimator.estimate(complete, [], transmitter_probability=0.8)
        self.assertLess(precise.standard_deviation, estimate.standard_deviation)
        self.assertEqual(precise.missing_features, ())
        contract = estimator.feature_use_contract(complete)
        self.assertIn("space_weather_kp", contract["active_probability_features"])
        self.assertIn(
            "receive_antenna_gain_dbi", contract["active_probability_features"]
        )
        self.assertIn("air_temperature_c", contract["evaluation_only_features"])
        self.assertTrue(contract["link_margin_computed"])

    def test_reception_features_reject_invalid_supplied_values_but_allow_missing(self):
        values = {
            "norad_id": 25544,
            "station_id": "gs",
            "resource_id": "rx",
            "max_elevation_deg": 40,
            "min_range_km": 500,
            "duration_seconds": 600,
            "tle_age_hours": 12,
        }
        self.assertIsNone(ReceptionFeatures(**values).frequency_hz)
        with self.assertRaisesRegex(ValueError, "finite"):
            ReceptionFeatures(**values, transmit_power_dbw=float("nan"))
        with self.assertRaisesRegex(ValueError, "positive"):
            ReceptionFeatures(**values, frequency_hz=0)
        with self.assertRaisesRegex(ValueError, r"\[0, 9\]"):
            ReceptionFeatures(**values, space_weather_kp=10)

    def test_recorded_environment_is_time_bounded_and_composable(self):
        at = datetime(2024, 5, 3, 11, tzinfo=UTC)
        sample = OrbitSample(at, 40, 90, 500, 0, 0, 0)
        window = PassWindow(
            25544,
            "gs",
            at - timedelta(minutes=1),
            at + timedelta(minutes=1),
            40,
            at,
            500,
            1000,
            (sample,),
            tle().fingerprint,
        )
        station = GroundStation("gs", 52, 21, 100, (ReceiverResource("rx"),))
        local = RecordedEnvironmentProvider(
            [
                RecordedEnvironmentWindow(
                    at - timedelta(hours=1),
                    at + timedelta(hours=1),
                    "local-weather-v1",
                    station_id="gs",
                    atmospheric_attenuation_db=0.4,
                )
            ]
        )
        space = RecordedEnvironmentProvider(
            [
                RecordedEnvironmentWindow(
                    at - timedelta(hours=3),
                    at + timedelta(hours=3),
                    "space-weather-v1",
                    space_weather_kp=5,
                )
            ]
        )
        snapshot = CompositeEnvironmentProvider([local, space]).at(station, window)
        self.assertEqual(snapshot.atmospheric_attenuation_db, 0.4)
        self.assertEqual(snapshot.space_weather_kp, 5)
        features = ReceptionFeatures(
            norad_id=25544,
            station_id="gs",
            resource_id="rx",
            max_elevation_deg=40,
            min_range_km=500,
            duration_seconds=120,
            tle_age_hours=12,
        )
        enriched = EnvironmentFeatureEnricher(
            CompositeEnvironmentProvider([local, space])
        )(
            features,
            SatelliteTarget(25544, "ISS"),
            station,
            station.resources[0],
            window,
        )
        self.assertEqual(
            enriched.environment_source,
            "local-weather-v1 + space-weather-v1",
        )

    def test_environment_inputs_fail_closed_on_invalid_supplied_values(self):
        at = datetime(2024, 5, 3, 11, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "finite"):
            EnvironmentSnapshot(
                at,
                "weather",
                atmospheric_attenuation_db=float("nan"),
            )
        with self.assertRaisesRegex(ValueError, "Kp"):
            RecordedEnvironmentWindow(
                at,
                at + timedelta(hours=1),
                "space-weather",
                space_weather_kp=10,
            )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            RecordedEnvironmentWindow(
                datetime(2024, 5, 3, 11),
                datetime(2024, 5, 3, 12),
                "weather",
            )
        with self.assertRaisesRegex(ValueError, "source"):
            EnvironmentSnapshot(at, 123)  # type: ignore[arg-type]

    def test_opportunities_are_tle_derived_filtered_by_hardware_and_blockers(self):
        start = datetime(2024, 5, 3, 11, tzinfo=UTC)
        samples = tuple(
            OrbitSample(start + timedelta(minutes=index * 2), 20 + index * 10, 90, 700 - index * 50, -1, 0, 179)
            for index in range(3)
        )
        pass_window = PassWindow(
            norad_id=25544,
            station_id="gs",
            start=samples[0].at,
            end=samples[-1].at,
            max_elevation_deg=40,
            culmination_at=samples[-1].at,
            min_range_km=600,
            max_abs_doppler_hz=3000,
            samples=samples,
            tle_fingerprint=tle().fingerprint,
        )
        antenna = AntennaSpec("vhf", ((144e6, 146e6),), gain_dbi=10)
        station = GroundStation("gs", 52, 21, 100, (ReceiverResource("rx", antenna),))
        target = SatelliteTarget(
            25544,
            "ISS",
            frequency_hz=145_800_000,
            samples_per_second=2,
            transmission_rule=TransmissionRule(weekdays_utc=(4,)),
        )
        builder = OpportunityBuilder(FakePredictor(pass_window))
        built = builder.build([target], [station], {25544: tle()}, start, start + timedelta(hours=1))
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0].tle_fingerprint, tle().fingerprint)
        self.assertIn(
            "P(detectable signal",
            built[0].metadata["probability_semantics"]["p_transmit"],
        )
        feature_use = built[0].metadata["feature_use_contract"]
        self.assertEqual(
            feature_use["schema_version"], "probability-feature-use-v1"
        )
        self.assertIn(
            "receiver_antenna_frequency_range",
            feature_use["active_feasibility_constraints"],
        )
        self.assertEqual(feature_use["receiver_antenna"]["antenna_id"], "vhf")
        blocker = Blocker("maintenance", "gs", start, start + timedelta(minutes=10))
        self.assertEqual(
            builder.build(
                [target], [station], {25544: tle()}, start, start + timedelta(hours=1), blockers=[blocker]
            ),
            (),
        )
        partial = Blocker(
            "priority-observation",
            "gs",
            start + timedelta(minutes=1, seconds=30),
            start + timedelta(minutes=2, seconds=30),
        )
        clipped = builder.build(
            [target],
            [station],
            {25544: tle()},
            start,
            start + timedelta(hours=1),
            blockers=[partial],
        )
        self.assertEqual(len(clipped), 2)
        self.assertEqual(
            [(item.start, item.end) for item in clipped],
            [
                (start, start + timedelta(minutes=1, seconds=30)),
                (start + timedelta(minutes=2, seconds=30), start + timedelta(minutes=4)),
            ],
        )
        self.assertTrue(all(item.metadata["blocker_clipped"] for item in clipped))
        self.assertEqual(sum(item.nominal_unique_samples for item in clipped), 360)

    def test_continuous_visibility_is_split_into_interruptible_observations(self):
        start = datetime(2024, 5, 3, 11, tzinfo=UTC)
        samples = tuple(
            OrbitSample(
                start + timedelta(minutes=index * 5),
                30,
                90,
                40_000,
                0,
                0,
                0,
            )
            for index in range(13)
        )
        pass_window = PassWindow(
            norad_id=25544,
            station_id="gs",
            start=start,
            end=start + timedelta(hours=1),
            max_elevation_deg=30,
            culmination_at=start,
            min_range_km=40_000,
            max_abs_doppler_hz=0,
            samples=samples,
            tle_fingerprint=tle().fingerprint,
        )
        station = GroundStation(
            "gs", 52, 21, 100, (ReceiverResource("rx"),)
        )
        enriched_windows = []

        def capture_segment(
            features, target, selected_station, resource, selected_window
        ):
            enriched_windows.append(selected_window)
            return features

        builder = OpportunityBuilder(
            FakePredictor(pass_window),
            feature_enricher=capture_segment,
            minimum_opportunity_duration_seconds=60,
            maximum_opportunity_duration_seconds=900,
        )
        built = builder.build(
            [SatelliteTarget(25544, "continuous", samples_per_second=2)],
            [station],
            {25544: tle()},
            start,
            start + timedelta(hours=1),
        )
        self.assertEqual(len(built), 4)
        self.assertEqual(
            [(item.start, item.end) for item in built],
            [
                (
                    start + timedelta(minutes=15 * index),
                    start + timedelta(minutes=15 * (index + 1)),
                )
                for index in range(4)
            ],
        )
        self.assertTrue(
            all(item.metadata["duration_limited"] for item in built)
        )
        self.assertEqual(len(enriched_windows), 4)
        self.assertTrue(
            all(
                item.start <= item.culmination_at <= item.end
                and item.end - item.start == timedelta(minutes=15)
                for item in enriched_windows
            )
        )
        self.assertEqual(
            sum(item.nominal_unique_samples for item in built),
            2 * 60 * 60,
        )

        with self.assertRaisesRegex(ValueError, "finite and positive"):
            OpportunityBuilder(
                FakePredictor(pass_window),
                maximum_opportunity_duration_seconds=0,
            )
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            OpportunityBuilder(
                FakePredictor(pass_window),
                minimum_opportunity_duration_seconds=0,
            )
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            OpportunityBuilder(
                FakePredictor(pass_window),
                minimum_opportunity_duration_seconds=901,
                maximum_opportunity_duration_seconds=900,
            )

        sliver_window = replace(
            pass_window,
            end=start + timedelta(hours=1, seconds=1),
            samples=samples,
        )
        without_sliver = OpportunityBuilder(
            FakePredictor(sliver_window),
            minimum_opportunity_duration_seconds=60,
            maximum_opportunity_duration_seconds=900,
        ).build(
            [SatelliteTarget(25544, "continuous", samples_per_second=2)],
            [station],
            {25544: tle()},
            start,
            start + timedelta(hours=1, seconds=1),
        )
        self.assertEqual(len(without_sliver), 4)
        self.assertTrue(
            all((item.end - item.start).total_seconds() >= 60 for item in without_sliver)
        )

    def test_sgp4_predictor_produces_real_visibility_geometry(self):
        station = GroundStation(
            "gs",
            52.2,
            21.0,
            100,
            (ReceiverResource("rx"),),
        )
        start = datetime(2024, 5, 2, tzinfo=UTC)
        passes = Sgp4PassPredictor(step_seconds=60).predict(
            tle(),
            station,
            start,
            start + timedelta(hours=6),
            carrier_frequency_hz=145_800_000,
        )
        self.assertGreaterEqual(len(passes), 1)
        self.assertGreater(passes[0].max_elevation_deg, station.minimum_elevation_deg)
        self.assertGreater(passes[0].max_abs_doppler_hz, 0)

    def test_milp_selects_global_non_overlapping_maximum(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        candidates = (
            opportunity("long", base, base + timedelta(minutes=10), 0.10),
            opportunity("left", base, base + timedelta(minutes=5), 0.08),
            opportunity("right", base + timedelta(minutes=5), base + timedelta(minutes=10), 0.08),
        )
        plan = MilpScheduler().schedule(
            candidates,
            horizon_start=base,
            horizon_end=base + timedelta(hours=1),
            tle_fingerprints={25544: tle().fingerprint},
        )
        self.assertEqual({item.opportunity_id for item in plan.assignments}, {"left", "right"})
        self.assertEqual(plan.diagnostics["status"], "optimal")

    def test_scheduler_rejects_inconsistent_receiver_capacity_metadata(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        first = replace(
            opportunity("first", base, base + timedelta(minutes=10), 0.8),
            metadata={"receiver_capacity": 1},
        )
        second = replace(
            opportunity("second", base, base + timedelta(minutes=10), 0.7),
            metadata={"receiver_capacity": 2},
        )
        with self.assertRaisesRegex(ValueError, "inconsistent receiver capacity"):
            MilpScheduler().schedule(
                [first, second],
                horizon_start=base,
                horizon_end=base + timedelta(hours=1),
                tle_fingerprints={25544: tle().fingerprint},
            )

    def test_schedule_policy_rejects_type_confusion_and_inconsistent_limits(self):
        with self.assertRaisesRegex(ValueError, "integer counts"):
            SchedulePolicy(minimum_observations_by_satellite={25544: 1.5})
        with self.assertRaisesRegex(ValueError, "integer NORAD"):
            SchedulePolicy(maximum_observations_by_satellite={True: 1})
        with self.assertRaisesRegex(ValueError, "exceed maximum"):
            SchedulePolicy(
                minimum_observations_by_satellite={25544: 2},
                maximum_observations_by_satellite={25544: 1},
            )
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            SchedulePolicy(require_proven_optimum="yes")

    def test_tle_shift_creates_new_plan_revision_but_small_shift_does_not(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        old = opportunity("old", base + timedelta(hours=1), base + timedelta(hours=1, minutes=10), 0.8)
        initial = MilpScheduler().schedule(
            [old],
            horizon_start=base,
            horizon_end=base + timedelta(days=1),
            tle_fingerprints={25544: tle().fingerprint},
            created_at=base,
        )
        shifted = opportunity(
            "new",
            old.start + timedelta(seconds=30),
            old.end + timedelta(seconds=30),
            0.8,
            fingerprint=tle(TLE1_NEW).fingerprint,
        )
        impact = assess_tle_plan_impact([old], [shifted], initial)
        self.assertTrue(impact.material)
        result = RollingPlanner().refresh(
            initial,
            [old],
            [shifted],
            {25544: tle(TLE1_NEW)},
            now=base,
            replan_policy=ReplanPolicy(freeze_horizon=timedelta(0)),
        )
        self.assertTrue(result.replanned)
        self.assertEqual(result.plan.revision, 2)
        small = opportunity(
            "tiny",
            old.start + timedelta(seconds=5),
            old.end + timedelta(seconds=5),
            0.8,
            fingerprint=tle(TLE1_NEW).fingerprint,
        )
        self.assertFalse(assess_tle_plan_impact([old], [small], initial).material)

    def test_satnogs_export_is_exact_dry_run_and_reconciles_jobs(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        item = opportunity("selected", base, base + timedelta(minutes=10), 0.8)
        plan = MilpScheduler().schedule(
            [item],
            horizon_start=base,
            horizon_end=base + timedelta(hours=1),
            tle_fingerprints={25544: tle().fingerprint},
        )
        export = build_satnogs_schedule_export(plan)
        self.assertEqual(export.api_path, "observations/")
        self.assertEqual(export.api_payload[0]["ground_station"], 12)
        self.assertEqual(
            export.audit[0]["plan_canonical_fingerprint"],
            plan.canonical_fingerprint(),
        )
        jobs = [{"id": 7, "start": "2024-05-03T00:00:00Z", "end": "2024-05-03T00:10:00Z", "ground_station": 12, "transmitter": "A" * 22}]
        reconciliation = reconcile_satnogs_jobs(export, jobs)
        self.assertEqual(reconciliation.matched_opportunity_ids, ("selected",))

    def test_satnogs_export_rejects_cross_resource_station_overlap(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        first = opportunity("first", base, base + timedelta(minutes=10), 0.8)
        second = replace(
            opportunity(
                "second",
                base + timedelta(minutes=2),
                base + timedelta(minutes=8),
                0.7,
                resource="rx-2",
            ),
            norad_id=25545,
        )
        plan = ObservationPlan(
            plan_id="overlap",
            revision=1,
            created_at=base,
            horizon_start=base,
            horizon_end=base + timedelta(hours=1),
            assignments=(first, second),
            tle_fingerprints={25544: first.tle_fingerprint, 25545: second.tle_fingerprint},
            objective_value=first.expected_unique_samples + second.expected_unique_samples,
            solver="fixture",
            trigger="fixture",
        )
        with self.assertRaisesRegex(SatnogsExportError, "no overlapping jobs"):
            build_satnogs_schedule_export(plan)

    def test_store_keeps_tle_and_immutable_plan_revision(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        plan = MilpScheduler().schedule(
            [opportunity("selected", base, base + timedelta(minutes=10), 0.8)],
            horizon_start=base,
            horizon_end=base + timedelta(hours=1),
            tle_fingerprints={25544: tle().fingerprint},
        )
        with tempfile.TemporaryDirectory() as directory:
            store = PlanningStore(Path(directory) / "planning.sqlite")
            store.save_tle(tle())
            self.assertEqual(store.latest_tle(25544).fingerprint, tle().fingerprint)
            store.save_plan(plan)
            self.assertEqual(store.latest_plan_document(plan.plan_id)["revision"], 1)
            self.assertEqual(store.next_plan_revision(plan.plan_id), 2)
            revision_two = replace(plan, revision=store.next_plan_revision(plan.plan_id))
            store.save_plan(revision_two)
            self.assertEqual(store.latest_plan_document(plan.plan_id)["revision"], 2)
            self.assertEqual(store.next_plan_revision("new-logical-plan"), 1)
            with self.assertRaisesRegex(ValueError, "non-empty"):
                store.next_plan_revision("   ")
            with self.assertRaisesRegex(RuntimeError, "immutable"):
                store.save_plan(revision_two)

    def test_store_keeps_immutable_time_bounded_reception_evidence(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        item = ReceptionEvidence(
            norad_id=25544,
            station_id="gs",
            resource_id="rx",
            listened=True,
            transmitter_state="confirmed",
            decoded=True,
            signal_present=True,
            source="local",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = PlanningStore(Path(directory) / "planning.sqlite")
            store.save_reception_evidence(
                "local-1", base, item, source_observation_id="receiver-job-1"
            )
            self.assertEqual(
                store.load_reception_evidence(before=base + timedelta(seconds=1))[0].evidence,
                item,
            )
            self.assertEqual(store.load_reception_evidence(before=base), ())
            with self.assertRaisesRegex(RuntimeError, "immutable"):
                store.save_reception_evidence("local-1", base, item)

    def test_satnogs_history_never_infers_unknown_observation_as_failure(self):
        evidence = evidence_from_satnogs_observations(
            [
                {
                    "norad_cat_id": 25544,
                    "ground_station": 12,
                    "waterfall_status": "without-signal",
                    "demoddata": [],
                },
                {
                    "norad_cat_id": 25544,
                    "ground_station": 12,
                    "waterfall_status": "with-signal",
                    "demoddata": [],
                },
                {
                    "norad_cat_id": 25544,
                    "ground_station": 12,
                    "waterfall_status": "with-signal",
                    "demoddata": [{"payload": "x"}],
                },
                {
                    "norad_cat_id": 25544,
                    "ground_station": 12,
                    "waterfall_status": "with-signal",
                },
                {
                    "norad_cat_id": 25544,
                    "ground_station": 12,
                    "waterfall_status": "without-signal",
                    "demoddata": [{"payload": "contradiction"}],
                },
            ]
        )
        self.assertEqual(len(evidence), 4)
        self.assertIsNone(evidence[0].decoded)
        self.assertFalse(evidence[0].signal_present)
        self.assertEqual(evidence[0].transmitter_state, "unknown")
        self.assertFalse(evidence[1].decoded)
        self.assertTrue(evidence[1].signal_present)
        self.assertTrue(evidence[2].decoded)
        self.assertIsNone(evidence[3].decoded)

    def test_local_history_rejects_truthy_strings_as_boolean_evidence(self):
        with self.assertRaisesRegex(ValueError, "listened must be boolean"):
            evidence_from_local_rows(
                [
                    {
                        "norad_id": 25544,
                        "listened": "false",
                        "transmitter_state": "unknown",
                    }
                ]
            )

    def test_monte_carlo_is_seeded_and_matches_certain_yield(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        certain = opportunity("certain", base, base + timedelta(minutes=10), 1.0)
        plan = MilpScheduler().schedule(
            [certain],
            horizon_start=base,
            horizon_end=base + timedelta(hours=1),
            tle_fingerprints={25544: tle().fingerprint},
        )
        result = simulate_plan_yield(plan, trials=100, seed=42)
        self.assertEqual(result.mean_unique_samples, 100)
        self.assertEqual(result.percentile_05, 100)
        self.assertEqual(result.probability_of_any_success, 1)

        document = plan_document(plan)
        self.assertEqual(document["schema_version"], "observation-plan-v2")
        self.assertEqual(plan_from_document(document), plan)
        aliases = plan_document(plan)
        aliases["assignments"][0]["probability"]["p_signal_present"] = 0.5
        with self.assertRaisesRegex(ValueError, "aliases disagree"):
            plan_from_document(aliases)
        boolean_confusion = plan_document(plan)
        boolean_confusion["assignments"][0]["exclusive_transmission"] = "false"
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            plan_from_document(boolean_confusion)
        numeric_confusion = plan_document(plan)
        numeric_confusion["assignments"][0]["probability"]["p_success"] = "1.0"
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            plan_from_document(numeric_confusion)
        probability_tamper = plan_document(plan)
        probability_tamper["assignments"][0]["probability"][
            "standard_deviation"
        ] = 0.2
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            plan_from_document(probability_tamper)
        diagnostics_tamper = plan_document(plan)
        diagnostics_tamper["diagnostics"]["status"] = "tampered"
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            plan_from_document(diagnostics_tamper)
        unsigned = dict(document)
        unsigned.pop("canonical_fingerprint")
        with self.assertRaisesRegex(ValueError, "fingerprint is required"):
            plan_from_document(unsigned)
        document["assignments"][0]["start"] = (base + timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            plan_from_document(document)

    def test_correlated_monte_carlo_models_shared_operational_risk(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        certain = opportunity("certain", base, base + timedelta(minutes=10), 1.0)
        plan = MilpScheduler().schedule(
            [certain],
            horizon_start=base,
            horizon_end=base + timedelta(hours=1),
            tle_fingerprints={25544: tle().fingerprint},
        )
        result = simulate_plan_yield_correlated(
            plan,
            scenario=CorrelatedRiskScenario(
                station_day_availability=0.0,
                satellite_day_transmit_availability=1.0,
                severe_environment_probability=0.0,
                severe_environment_success_multiplier=1.0,
            ),
            trials=100,
            seed=42,
        )
        self.assertEqual(result.mean_unique_samples, 0)
        self.assertEqual(result.probability_of_any_success, 0)
        self.assertEqual(result.probability_below_half_expected, 1)

    def test_dynamic_engine_replans_when_maintenance_changes(self):
        start = datetime(2024, 5, 3, 11, tzinfo=UTC)
        samples = tuple(
            OrbitSample(
                start + timedelta(minutes=index * 2),
                20 + index * 10,
                90,
                700 - index * 50,
                -1,
                0,
                0,
            )
            for index in range(3)
        )
        pass_window = PassWindow(
            25544,
            "gs",
            samples[0].at,
            samples[-1].at,
            40,
            samples[-1].at,
            600,
            3000,
            samples,
            tle().fingerprint,
        )
        station = GroundStation("gs", 52, 21, 100, (ReceiverResource("rx"),))
        target = SatelliteTarget(25544, "ISS")
        engine = DynamicObservationPlanner(
            StaticTleProvider(), OpportunityBuilder(FakePredictor(pass_window))
        )
        initial = engine.plan(
            [target],
            [station],
            start,
            start + timedelta(hours=1),
            now=start - timedelta(hours=1),
        )
        self.assertEqual(len(initial.plan.assignments), 1)
        blocked = engine.refresh(
            initial,
            [target],
            [station],
            blockers=[Blocker("m", "gs", start, start + timedelta(minutes=10))],
            now=start - timedelta(minutes=30),
        )
        self.assertTrue(blocked.replanned)
        self.assertEqual(blocked.plan.revision, 2)
        self.assertEqual(blocked.plan.assignments, ())
        self.assertIn("blockers_changed", blocked.plan.trigger)

    def test_dynamic_engine_replans_when_target_or_policy_changes(self):
        start = datetime(2024, 5, 3, 11, tzinfo=UTC)
        samples = tuple(
            OrbitSample(
                start + timedelta(minutes=index * 2),
                20 + index * 10,
                90,
                700 - index * 50,
                -1,
                0,
                0,
            )
            for index in range(3)
        )
        pass_window = PassWindow(
            25544,
            "gs",
            samples[0].at,
            samples[-1].at,
            40,
            samples[-1].at,
            600,
            3000,
            samples,
            tle().fingerprint,
        )
        station = GroundStation("gs", 52, 21, 100, (ReceiverResource("rx"),))
        target = SatelliteTarget(25544, "ISS", priority=1)
        engine = DynamicObservationPlanner(
            StaticTleProvider(), OpportunityBuilder(FakePredictor(pass_window))
        )
        initial = engine.plan(
            [target],
            [station],
            start,
            start + timedelta(hours=1),
            now=start - timedelta(hours=1),
        )
        changed = engine.refresh(
            initial,
            [replace(target, priority=2)],
            [station],
            schedule_policy=SchedulePolicy(
                maximum_observations_by_satellite={25544: 1}
            ),
            now=start - timedelta(minutes=31),
        )
        self.assertTrue(changed.replanned)
        self.assertEqual(changed.plan.revision, 2)
        self.assertIn("planning_inputs_changed", changed.plan.trigger)
        self.assertNotEqual(
            changed.planning_input_fingerprint,
            initial.planning_input_fingerprint,
        )
        self.assertEqual(
            changed.plan.diagnostics["planning_input_fingerprint"],
            changed.planning_input_fingerprint,
        )

    def test_rolling_replan_never_reschedules_elapsed_windows(self):
        base = datetime(2024, 5, 3, tzinfo=UTC)
        old_tle = tle()
        new_tle = tle(TLE1_NEW)
        elapsed = opportunity(
            "elapsed",
            base,
            base + timedelta(minutes=10),
            0.9,
            fingerprint=old_tle.fingerprint,
        )
        future = opportunity(
            "future",
            base + timedelta(hours=2),
            base + timedelta(hours=2, minutes=10),
            0.8,
            fingerprint=old_tle.fingerprint,
        )
        initial = MilpScheduler().schedule(
            [elapsed, future],
            horizon_start=base,
            horizon_end=base + timedelta(hours=3),
            tle_fingerprints={25544: old_tle.fingerprint},
        )
        shifted_future = opportunity(
            "future-new",
            future.start + timedelta(seconds=30),
            future.end + timedelta(seconds=30),
            0.8,
            fingerprint=new_tle.fingerprint,
        )
        result = RollingPlanner().refresh(
            initial,
            [elapsed, future],
            [elapsed, shifted_future],
            {25544: new_tle},
            now=base + timedelta(hours=1),
            replan_policy=ReplanPolicy(freeze_horizon=timedelta(0)),
        )
        self.assertTrue(result.replanned)
        self.assertEqual(result.plan.horizon_start, base + timedelta(hours=1))
        self.assertTrue(
            all(item.start >= result.plan.horizon_start for item in result.plan.assignments)
        )


if __name__ == "__main__":
    unittest.main()
