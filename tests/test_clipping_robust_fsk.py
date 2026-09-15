from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

import telemetry_yield.clipping_robust_fsk as clipping_robust_fsk
from telemetry_yield.clipping_robust_fsk import (
    BlindPhaseFskConfig,
    ClippingRobustAx25Frame,
    PhaseWindowCandidate,
    _BlindReceiverPath,
    _constant_radius_declip_exact_endpoints,
    _can_resolve_start,
    _cluster_receiver_paths,
    _frontend_decimation_and_sps,
    _repair_path_rank,
    decode_clipping_robust_ax25_ci16,
    group_ax25_frame_consensus,
    select_ci16_phase_windows,
)
from telemetry_yield.clock_recovery import TimingHypothesis
from telemetry_yield.soft_sync import SoftDecodedFrame, SoftListResult


class ClippingRobustFskTests(unittest.TestCase):
    def _detection(
        self,
        *,
        lag: int,
        start: float = 10.0,
        flips: tuple[int, ...] = (100,),
    ) -> ClippingRobustAx25Frame:
        return ClippingRobustAx25Frame(
            payload=b"payload",
            fcs=b"\x00\x00",
            decoder_start_seconds=8.0,
            estimated_frame_start_seconds=start,
            constant_radius_factor=3.0,
            phase_difference_lag=lag,
            g3ruh_descramble=True,
            timing=TimingHypothesis(0.0, 0.5, 2.0, 0.0, 1.0, 256),
            frame_start_symbol=100,
            frame_stop_symbol=200,
            flipped_symbol_indices=flips,
            flipped_symbol_reliabilities=tuple(0.01 for _ in flips),
            search_stage="test",
            attempted_candidates=10,
        )

    def test_correlated_frontend_consensus_does_not_authenticate_repair(self) -> None:
        single = group_ax25_frame_consensus((self._detection(lag=1),))
        self.assertFalse(single[0].native_trusted)
        consensus = group_ax25_frame_consensus(
            (self._detection(lag=1), self._detection(lag=3, start=10.01))
        )
        self.assertTrue(consensus[0].has_correction_consensus)
        self.assertFalse(consensus[0].native_trusted)
        self.assertEqual(consensus[0].independent_frontends, ((3.0, 1), (3.0, 3)))

    def test_uncorrected_frame_is_native_trusted_without_list_consensus(self) -> None:
        grouped = group_ax25_frame_consensus(
            (self._detection(lag=1, flips=()),)
        )
        self.assertTrue(grouped[0].has_uncorrected_detection)
        self.assertTrue(grouped[0].native_trusted)

    def test_corrected_candidate_cannot_resolve_later_receiver_paths(self) -> None:
        base = {
            "frame_with_fcs": b"frame-with-fcs",
            "left_flag_bit": 100,
            "left_flag_hamming": 0,
            "right_flag_bit": 200,
        }
        uncorrected = SoftDecodedFrame(
            **base,
            flipped_symbol_indices=(),
            flipped_symbol_reliabilities=(),
        )
        corrected = SoftDecodedFrame(
            **base,
            flipped_symbol_indices=(123,),
            flipped_symbol_reliabilities=(0.01,),
        )
        self.assertTrue(_can_resolve_start(uncorrected))
        self.assertFalse(_can_resolve_start(corrected))

    def test_ci16_selector_ranks_signal_burst_without_timestamps(self) -> None:
        sample_rate = 100
        pairs = np.empty((800, 2), dtype="<i2")
        phase = np.arange(800, dtype=np.float64) * 0.17
        pairs[:, 0] = np.rint(1000 * np.cos(phase)).astype("<i2")
        pairs[:, 1] = np.rint(1000 * np.sin(phase)).astype("<i2")
        burst = slice(300, 400)
        pairs[burst, 0] = np.where(np.arange(100) % 2, 32767, -32768)
        pairs[burst, 1] = 32767
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.ci16"
            path.write_bytes(pairs.tobytes())
            config = BlindPhaseFskConfig(
                sample_rate_hz=sample_rate,
                baudrate=25.0,
                analysis_window_seconds=1.0,
                decoder_window_seconds=2.0,
                decoder_window_lead_seconds=0.5,
                candidate_window_limit=2,
                window_nms_seconds=1.5,
                phase_bins=4,
                short_search_timing_hypotheses=4,
                deep_search_timing_hypotheses=2,
            )
            selected = select_ci16_phase_windows(path, config=config)
        self.assertEqual(selected[0].analysis_index, 3)
        self.assertEqual(selected[0].decoder_start_seconds, 2.5)
        self.assertGreater(selected[0].endpoint_clip_fraction, 0.9)

    def test_declipping_mask_does_not_reclassify_minus_32767(self) -> None:
        raw = np.asarray(
            ((-32767, 100), (-32768, 100), (32767, 100)),
            dtype="<i2",
        )
        output = _constant_radius_declip_exact_endpoints(raw, 3.0)
        self.assertEqual(output[0].real, -32767.0)
        self.assertLess(output[1].real, -32768.0)
        self.assertGreater(output[2].real, 32767.0)

    def test_invalid_timing_budget_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(
                phase_bins=4,
                short_search_timing_hypotheses=5,
            )
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(constant_radius_factors=())
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(descramble_modes=())
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(descramble_modes=(True, True))
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(repair_event_maximum_attempts=0)
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(
                repair_path_maximum_attempts=201,
                repair_event_maximum_attempts=200,
            )

    def test_frontend_decimation_preserves_9600_and_accepts_19200(self) -> None:
        self.assertEqual(
            _frontend_decimation_and_sps(
                BlindPhaseFskConfig(
                    sample_rate_hz=57_600,
                    baudrate=9_600.0,
                )
            ),
            (3, 2.0),
        )
        self.assertEqual(
            _frontend_decimation_and_sps(
                BlindPhaseFskConfig(
                    sample_rate_hz=57_600,
                    baudrate=19_200.0,
                )
            ),
            (1, 3.0),
        )
        with self.assertRaises(ValueError):
            BlindPhaseFskConfig(
                sample_rate_hz=57_600,
                baudrate=38_400.0,
            )

    def test_event_cluster_and_rank_use_only_blind_path_properties(self) -> None:
        def path(start: float, score: float, rank: int) -> _BlindReceiverPath:
            timing = TimingHypothesis(0.0, 0.5, 2.0, 0.0, score, 256)
            return _BlindReceiverPath(
                constant_radius_factor=3.0,
                phase_difference_lag=1,
                g3ruh_descramble=True,
                timing_rank=rank,
                timing=timing,
                soft_symbols=(0.0,),
                frame_start_symbol=100,
                estimated_frame_start_seconds=start,
            )

        low = path(10.0, 1.0, 0)
        high = path(10.001, 2.0, 1)
        later = path(11.0, 3.0, 0)
        groups = _cluster_receiver_paths(
            (later, low, high),
            tolerance_seconds=0.002,
        )
        self.assertEqual(tuple(map(len, groups)), (2, 1))
        self.assertLess(_repair_path_rank(high), _repair_path_rank(low))

    def test_native_sweep_reaches_event_after_repair_cap(self) -> None:
        timing = TimingHypothesis(0.0, 0.5, 2.0, 0.0, 1.0, 256)
        paths = tuple(
            _BlindReceiverPath(
                constant_radius_factor=3.0,
                phase_difference_lag=1,
                g3ruh_descramble=False,
                timing_rank=0,
                timing=TimingHypothesis(
                    0.0,
                    0.5,
                    2.0,
                    0.0,
                    float(17 - index),
                    256,
                ),
                soft_symbols=(0.0,) * 256,
                frame_start_symbol=100 + index,
                estimated_frame_start_seconds=float(index),
            )
            for index in range(17)
        )
        native_starts: list[int] = []
        repair_starts: list[int] = []

        def decode(*args, **kwargs) -> SoftListResult:
            budget = kwargs["budget"]
            start = kwargs["event_start_symbol"]
            if budget.maximum_flips == 0:
                native_starts.append(start)
                frames = (
                    SoftDecodedFrame(
                        frame_with_fcs=b"native-after-cap\x00\x00",
                        left_flag_bit=start,
                        left_flag_hamming=0,
                        right_flag_bit=start + 200,
                        flipped_symbol_indices=(),
                        flipped_symbol_reliabilities=(),
                    ),
                ) if start == 116 else ()
            else:
                repair_starts.append(start)
                frames = ()
            return SoftListResult(
                threshold=0.0,
                exact_flag_count=0,
                eligible_regions=1,
                searched_regions=1,
                attempted_candidates=1,
                budget_exhausted=False,
                frames=frames,
            )

        pairs = np.zeros((16_000, 2), dtype="<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.ci16"
            path.write_bytes(pairs.tobytes())
            config = BlindPhaseFskConfig(
                sample_rate_hz=5_760,
                baudrate=960.0,
                decoder_window_seconds=2.75,
                decoder_window_lead_seconds=1.0,
                candidate_window_limit=1,
                constant_radius_factors=(3.0,),
                phase_difference_lags=(1,),
                descramble_modes=(False,),
                phase_bins=4,
                short_search_timing_hypotheses=1,
                deep_search_timing_hypotheses=1,
                maximum_regions_per_start=1,
                repair_path_maximum_attempts=1,
                repair_region_maximum_attempts=1,
                repair_event_maximum_attempts=1,
                repair_window_maximum_events=16,
                repair_window_maximum_attempts=16,
                repair_event_maximum_unique_frames=1,
                repair_window_maximum_unique_frames=1,
            )
            selected = (
                PhaseWindowCandidate(
                    analysis_index=0,
                    analysis_start_seconds=0.0,
                    decoder_start_seconds=0.0,
                    mean_power=1.0,
                    endpoint_clip_fraction=0.0,
                    lag1_phase_coherence=0.0,
                    score=1.0,
                ),
            )
            with (
                patch.object(
                    clipping_robust_fsk,
                    "_phase_first",
                    return_value=np.zeros(256),
                ),
                patch.object(
                    clipping_robust_fsk,
                    "recover_timing_hypotheses",
                    return_value=(timing,),
                ),
                patch.object(
                    clipping_robust_fsk,
                    "soft_symbols_with_hypothesis",
                    return_value=(0.0,) * 256,
                ),
                patch.object(
                    clipping_robust_fsk,
                    "decode_satnogs_symbols",
                    return_value=(0,) * 256,
                ),
                patch.object(
                    clipping_robust_fsk,
                    "preamble_terminated_frame_starts",
                    return_value=(),
                ),
                patch.object(
                    clipping_robust_fsk,
                    "_cluster_receiver_paths",
                    return_value=tuple((item,) for item in paths),
                ),
                patch.object(
                    clipping_robust_fsk,
                    "protocol_constrained_syndrome_decode",
                    side_effect=decode,
                ),
            ):
                result = decode_clipping_robust_ax25_ci16(
                    path,
                    config=config,
                    selected_windows=selected,
                )

        self.assertEqual(native_starts, list(range(100, 117)))
        self.assertEqual(repair_starts, list(range(100, 116)))
        self.assertEqual(len(result.frames), 1)
        self.assertEqual(result.frames[0].payload, b"native-after-cap")
        self.assertEqual(result.frames[0].search_stage, "native_all_paths")

    def test_deterministic_noise_has_no_blind_outputs(self) -> None:
        rng = np.random.default_rng(20260903)
        pairs = rng.integers(
            -20_000,
            20_001,
            size=(16_000, 2),
            dtype=np.int16,
        ).astype("<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "noise.ci16"
            path.write_bytes(pairs.tobytes())
            config = BlindPhaseFskConfig(
                sample_rate_hz=5_760,
                baudrate=960.0,
                decoder_window_seconds=2.75,
                decoder_window_lead_seconds=1.0,
                phase_bins=4,
                short_search_timing_hypotheses=4,
                deep_search_timing_hypotheses=4,
                phase_difference_lags=(1,),
                maximum_regions_per_start=2,
                repair_path_maximum_attempts=100,
                repair_region_maximum_attempts=50,
                repair_event_maximum_attempts=200,
                repair_event_maximum_unique_frames=2,
            )
            selected = (
                PhaseWindowCandidate(
                    analysis_index=0,
                    analysis_start_seconds=0.0,
                    decoder_start_seconds=0.0,
                    mean_power=1.0,
                    endpoint_clip_fraction=0.0,
                    lag1_phase_coherence=0.0,
                    score=1.0,
                ),
            )
            result = decode_clipping_robust_ax25_ci16(
                path,
                config=config,
                selected_windows=selected,
            )
        self.assertEqual(result.frames, ())
        self.assertEqual(result.protocol_candidates_attempted, 0)

    def test_receiver_paths_fail_before_exceeding_configured_cap(self) -> None:
        timing = TimingHypothesis(0.0, 0.5, 2.0, 0.0, 1.0, 256)
        pairs = np.zeros((16_000, 2), dtype="<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.ci16"
            path.write_bytes(pairs.tobytes())
            config = BlindPhaseFskConfig(
                sample_rate_hz=5_760,
                baudrate=960.0,
                decoder_window_seconds=2.75,
                decoder_window_lead_seconds=1.0,
                candidate_window_limit=1,
                constant_radius_factors=(3.0,),
                phase_difference_lags=(1,),
                descramble_modes=(False,),
                phase_bins=4,
                short_search_timing_hypotheses=1,
                deep_search_timing_hypotheses=1,
                maximum_receiver_paths_per_window=1,
            )
            selected = (
                PhaseWindowCandidate(0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0),
            )
            with (
                patch.object(clipping_robust_fsk, "_phase_first", return_value=np.zeros(256)),
                patch.object(clipping_robust_fsk, "recover_timing_hypotheses", return_value=(timing,)),
                patch.object(clipping_robust_fsk, "soft_symbols_with_hypothesis", return_value=(0.0,) * 256),
                patch.object(clipping_robust_fsk, "decode_satnogs_symbols", return_value=(0,) * 256),
                patch.object(clipping_robust_fsk, "preamble_terminated_frame_starts", return_value=(100, 200)),
            ):
                with self.assertRaisesRegex(ValueError, "receiver path bound"):
                    decode_clipping_robust_ax25_ci16(
                        path, config=config, selected_windows=selected
                    )

    def test_deep_attempt_budget_is_never_increased_by_path_budget(self) -> None:
        timing = TimingHypothesis(0.0, 0.5, 2.0, 0.0, 1.0, 256)
        repair_budgets: list[int] = []
        force_output_limit = False

        def decode(*args, **kwargs) -> SoftListResult:
            budget = kwargs["budget"]
            if budget.maximum_flips:
                repair_budgets.append(budget.maximum_attempts)
                return SoftListResult(
                    0.0, 0, 1, 1, 0, force_output_limit, (), force_output_limit
                )
            return SoftListResult(0.0, 0, 1, 1, 0, False, ())

        pairs = np.zeros((16_000, 2), dtype="<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.ci16"
            path.write_bytes(pairs.tobytes())
            config = BlindPhaseFskConfig(
                sample_rate_hz=5_760,
                baudrate=960.0,
                decoder_window_seconds=2.75,
                decoder_window_lead_seconds=1.0,
                candidate_window_limit=1,
                constant_radius_factors=(3.0,),
                phase_difference_lags=(1,),
                descramble_modes=(False,),
                phase_bins=4,
                short_search_timing_hypotheses=1,
                deep_search_timing_hypotheses=1,
                deep_maximum_attempts=7,
                repair_path_maximum_attempts=100,
                repair_region_maximum_attempts=7,
                repair_event_maximum_attempts=100,
                repair_window_maximum_attempts=100,
            )
            selected = (
                PhaseWindowCandidate(0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0),
            )
            with (
                patch.object(clipping_robust_fsk, "_phase_first", return_value=np.zeros(256)),
                patch.object(clipping_robust_fsk, "recover_timing_hypotheses", return_value=(timing,)),
                patch.object(clipping_robust_fsk, "soft_symbols_with_hypothesis", return_value=(0.0,) * 256),
                patch.object(clipping_robust_fsk, "decode_satnogs_symbols", return_value=(0,) * 256),
                patch.object(clipping_robust_fsk, "preamble_terminated_frame_starts", return_value=(100,)),
                patch.object(clipping_robust_fsk, "protocol_constrained_syndrome_decode", side_effect=decode),
            ):
                decode_clipping_robust_ax25_ci16(
                    path, config=config, selected_windows=selected
                )
                force_output_limit = True
                with self.assertRaisesRegex(ValueError, "repair path output bound"):
                    decode_clipping_robust_ax25_ci16(
                        path, config=config, selected_windows=selected
                    )
        self.assertEqual(repair_budgets, [7, 7])

    def test_frame_detections_fail_before_exceeding_global_cap(self) -> None:
        timing = TimingHypothesis(0.0, 0.5, 2.0, 0.0, 1.0, 512)

        def decode(*args, **kwargs) -> SoftListResult:
            start = int(kwargs["event_start_symbol"])
            return SoftListResult(
                0.0,
                0,
                1,
                1,
                1,
                False,
                (
                    SoftDecodedFrame(
                        frame_with_fcs=f"frame-{start}".encode() + b"\x00\x00",
                        left_flag_bit=start,
                        left_flag_hamming=0,
                        right_flag_bit=start + 128,
                        flipped_symbol_indices=(),
                        flipped_symbol_reliabilities=(),
                    ),
                ),
            )

        pairs = np.zeros((16_000, 2), dtype="<i2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.ci16"
            path.write_bytes(pairs.tobytes())
            config = BlindPhaseFskConfig(
                sample_rate_hz=5_760,
                baudrate=960.0,
                decoder_window_seconds=2.75,
                decoder_window_lead_seconds=1.0,
                candidate_window_limit=1,
                constant_radius_factors=(3.0,),
                phase_difference_lags=(1,),
                descramble_modes=(False,),
                phase_bins=4,
                short_search_timing_hypotheses=1,
                deep_search_timing_hypotheses=1,
                maximum_receiver_paths_per_window=2,
                maximum_frame_detections=1,
            )
            selected = (
                PhaseWindowCandidate(0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0),
            )
            with (
                patch.object(clipping_robust_fsk, "_phase_first", return_value=np.zeros(512)),
                patch.object(clipping_robust_fsk, "recover_timing_hypotheses", return_value=(timing,)),
                patch.object(clipping_robust_fsk, "soft_symbols_with_hypothesis", return_value=(0.0,) * 512),
                patch.object(clipping_robust_fsk, "decode_satnogs_symbols", return_value=(0,) * 512),
                patch.object(clipping_robust_fsk, "preamble_terminated_frame_starts", return_value=(100, 300)),
                patch.object(clipping_robust_fsk, "protocol_constrained_syndrome_decode", side_effect=decode),
            ):
                with self.assertRaisesRegex(ValueError, "frame detection bound"):
                    decode_clipping_robust_ax25_ci16(
                        path, config=config, selected_windows=selected
                    )


if __name__ == "__main__":
    unittest.main()
