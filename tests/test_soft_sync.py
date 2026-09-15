from __future__ import annotations

import unittest

from telemetry_yield.soft_sync import (
    SoftListBudget,
    _effect_preserves_stuffing_map,
    _decode_body,
    _frame_after_map_preserving_effect,
    _map_repair_seed_states,
    _stuffing_trace,
    _unit_body_effect,
    _unstuff_body_with_map,
    apply_raw_level_flips,
    preamble_terminated_frame_starts,
    protocol_constrained_list_decode,
    protocol_constrained_syndrome_decode,
    raw_level_flip_effects,
)
from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.symbol_boundary import (
    AX25_FLAG_BITS,
    nrzi_encode_satnogs,
    reference_hdlc_region,
    stuff_hdlc_bits,
)
from telemetry_yield.symbol_boundary import (
    decode_satnogs_symbols,
    synthetic_sliced_levels,
)


REFERENCE = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


class SoftSyncTests(unittest.TestCase):
    def test_preamble_detector_uses_only_repeated_flags(self) -> None:
        levels = synthetic_sliced_levels(REFERENCE, preamble_flags=5)
        plain = decode_satnogs_symbols(levels, descramble=True)
        self.assertEqual(preamble_terminated_frame_starts(plain), (32,))

    def test_hot_path_body_decoder_matches_complete_valid_frame(self) -> None:
        framed = append_ax25_fcs(REFERENCE)
        stuffed = stuff_hdlc_bits(bytes_to_bits(framed, lsb_first=True))
        self.assertEqual(_decode_body(stuffed), framed)
        corrupted = bytearray(stuffed)
        corrupted[31] ^= 1
        self.assertIsNone(_decode_body(corrupted))

    def test_raw_level_impulse_matches_full_decoder(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        baseline = decode_satnogs_symbols(levels, descramble=True)
        index = 47
        levels[index] ^= 1
        actual = decode_satnogs_symbols(levels, descramble=True)
        expected = apply_raw_level_flips(baseline, (index,))
        self.assertEqual(tuple(actual), expected)
        changed = tuple(
            offset for offset, (a, b) in enumerate(zip(actual, baseline)) if a != b
        )
        self.assertEqual(changed, raw_level_flip_effects(index, len(baseline)))

    def test_list_decoder_recovers_one_low_reliability_body_flip(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        flipped = 3 * 8 + 40
        levels[flipped] ^= 1
        soft = [1.0 if level else -1.0 for level in levels]
        soft[flipped] = 0.001 if levels[flipped] else -0.001
        result = protocol_constrained_list_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            budget=SoftListBudget(
                least_reliable_symbols=8,
                maximum_flips=1,
                maximum_regions=1,
                maximum_attempts=32,
            ),
        )
        self.assertTrue(
            any(frame.frame_with_fcs[:-2] == REFERENCE for frame in result.frames)
        )

    def test_syndrome_decoder_recovers_one_low_reliability_body_flip(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        flipped = 3 * 8 + 40
        levels[flipped] ^= 1
        soft = [1.0 if level else -1.0 for level in levels]
        soft[flipped] = 0.001 if levels[flipped] else -0.001
        result = protocol_constrained_syndrome_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            budget=SoftListBudget(
                least_reliable_symbols=8,
                maximum_flips=1,
                maximum_regions=1,
                maximum_attempts=32,
            ),
        )
        recovered = [
            frame.frame_with_fcs[:-2] for frame in result.frames
        ]
        self.assertIn(REFERENCE, recovered)

    def test_syndrome_decoder_golden_plain_and_g3ruh_modes(self) -> None:
        g3ruh_levels = synthetic_sliced_levels(REFERENCE)
        plain_bits = bytes(AX25_FLAG_BITS) * 3
        plain_bits += reference_hdlc_region(REFERENCE)
        plain_bits += bytes(AX25_FLAG_BITS)
        plain_levels = nrzi_encode_satnogs(plain_bits, initial_level=0)
        budget = SoftListBudget(
            least_reliable_symbols=8,
            maximum_flips=0,
            maximum_regions=1,
            maximum_attempts=16,
        )
        for levels, descramble in (
            (plain_levels, False),
            (g3ruh_levels, True),
        ):
            soft = [1.0 if level else -1.0 for level in levels]
            result = protocol_constrained_syndrome_decode(
                soft,
                threshold=0.0,
                event_start_symbol=16,
                budget=budget,
                descramble=descramble,
            )
            self.assertIn(
                REFERENCE,
                [frame.frame_with_fcs[:-2] for frame in result.frames],
            )

    def test_syndrome_decoder_stops_before_accumulating_past_output_cap(self) -> None:
        second = REFERENCE[:-1] + bytes((REFERENCE[-1] ^ 1,))
        plain = bytes(AX25_FLAG_BITS) * 3
        plain += reference_hdlc_region(REFERENCE)
        plain += bytes(AX25_FLAG_BITS)
        plain += reference_hdlc_region(second)
        plain += bytes(AX25_FLAG_BITS)
        levels = nrzi_encode_satnogs(plain, initial_level=0)
        soft = [1.0 if level else -1.0 for level in levels]
        result = protocol_constrained_syndrome_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            budget=SoftListBudget(
                least_reliable_symbols=8,
                maximum_flips=0,
                maximum_regions=4,
                maximum_attempts=32,
                maximum_output_frames=1,
            ),
            descramble=False,
        )
        self.assertEqual(len(result.frames), 1)
        self.assertTrue(result.budget_exhausted)
        self.assertTrue(result.output_limit_reached)

    def test_map_seed_search_repairs_body_with_no_stuffing_map(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        plain = tuple(decode_satnogs_symbols(levels, descramble=True))
        left = 16
        right = len(plain) - 8
        body_start = left + 8
        baseline_body = plain[body_start:right]
        chosen = None
        damaged_body = None
        for index in range(body_start, right - 18):
            candidate = apply_raw_level_flips(plain, (index,))[body_start:right]
            if _unstuff_body_with_map(candidate) is None:
                chosen = index
                damaged_body = candidate
                break
        self.assertIsNotNone(chosen)
        assert chosen is not None and damaged_body is not None
        seeds, attempts, truncated = _map_repair_seed_states(
            damaged_body,
            body_start=body_start,
            plain_bit_count=len(plain),
            candidate_units=((chosen,),),
            unit_costs=(0.001,),
            maximum_flips=1,
            maximum_attempts=16,
            maximum_states=4,
        )
        self.assertGreater(attempts, 0)
        self.assertFalse(truncated)
        self.assertTrue(
            any(frame == append_ax25_fcs(REFERENCE) for _, _, frame, _ in seeds)
        )

    def test_valid_baseline_map_seed_states_fail_closed_at_bound(self) -> None:
        levels = synthetic_sliced_levels(REFERENCE)
        with self.assertRaisesRegex(ValueError, "map seed state bound exceeded"):
            protocol_constrained_syndrome_decode(
                tuple(float(value) for value in levels),
                threshold=0.5,
                event_start_symbol=16,
                budget=SoftListBudget(
                    least_reliable_symbols=len(levels),
                    maximum_flips=1,
                    maximum_regions=4,
                    maximum_attempts=64,
                    maximum_map_seed_states=1,
                ),
                descramble=True,
            )

    def test_sparse_map_classifier_matches_complete_reparse(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        plain = tuple(decode_satnogs_symbols(levels, descramble=True))
        left = 16
        right = len(plain) - 8
        body_start = left + 8
        baseline_body = plain[body_start:right]
        parsed = _unstuff_body_with_map(baseline_body)
        trace = _stuffing_trace(baseline_body)
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(trace)
        assert parsed is not None and trace is not None
        for index in range(body_start, right + 1):
            effect = _unit_body_effect(
                (index,),
                body_start=body_start,
                body_stop=right,
                plain_bit_count=len(plain),
            )
            candidate = apply_raw_level_flips(plain, (index,))[body_start:right]
            reparsed = _unstuff_body_with_map(candidate)
            expected = reparsed is not None and reparsed[1] == parsed[1]
            actual = _effect_preserves_stuffing_map(
                baseline_body,
                trace=trace,
                toggled_positions=effect,
            )
            self.assertEqual(actual, expected, index)
            if actual:
                retained_index = {
                    position: bit_index
                    for bit_index, position in enumerate(parsed[1])
                }
                self.assertEqual(
                    _frame_after_map_preserving_effect(
                        parsed[0],
                        retained_positions=parsed[1],
                        retained_index_by_position=retained_index,
                        toggled_positions=effect,
                    ),
                    reparsed[0],
                    index,
                )

    def test_syndrome_and_exhaustive_agree_on_bounded_repairs(self) -> None:
        cases = (
            (3 * 8 + 40,),
            (3 * 8 + 40, 3 * 8 + 72),
            (3 * 8 + 40, 3 * 8 + 72, 3 * 8 + 104),
        )
        for flipped in cases:
            levels = bytearray(synthetic_sliced_levels(REFERENCE))
            for index in flipped:
                levels[index] ^= 1
            soft = [10.0 if level else -10.0 for level in levels]
            for rank, index in enumerate(flipped, start=1):
                soft[index] = (
                    0.001 * rank if levels[index] else -0.001 * rank
                )
            budget = SoftListBudget(
                least_reliable_symbols=len(flipped),
                maximum_flips=len(flipped),
                maximum_regions=1,
                maximum_attempts=256,
            )
            exhaustive = protocol_constrained_list_decode(
                soft,
                threshold=0.0,
                event_start_symbol=16,
                budget=budget,
            )
            syndrome = protocol_constrained_syndrome_decode(
                soft,
                threshold=0.0,
                event_start_symbol=16,
                budget=budget,
            )
            expected = {
                frame.frame_with_fcs for frame in exhaustive.frames
            }
            actual = {frame.frame_with_fcs for frame in syndrome.frames}
            self.assertEqual(actual, expected)
            self.assertIn(append_ax25_fcs(REFERENCE), actual)

    def test_state_aware_boundary_search_repairs_soft_flag(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        flipped = 15
        levels[flipped] ^= 1
        soft = [1.0 if level else -1.0 for level in levels]
        soft[flipped] = 0.001 if levels[flipped] else -0.001
        result = protocol_constrained_list_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            budget=SoftListBudget(
                least_reliable_symbols=4,
                maximum_flips=0,
                maximum_regions=1,
                maximum_attempts=128,
                maximum_boundary_patterns=16,
                maximum_left_flag_hamming=3,
            ),
        )
        recovered = [
            frame
            for frame in result.frames
            if frame.frame_with_fcs[:-2] == REFERENCE
        ]
        self.assertTrue(recovered)
        self.assertGreaterEqual(recovered[0].left_flag_hamming, 1)

    def test_diversity_candidate_can_add_a_high_confidence_disagreement(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        low_reliability = 3 * 8 + 40
        diversity_only = 3 * 8 + 72
        levels[low_reliability] ^= 1
        levels[diversity_only] ^= 1
        soft = [1.0 if level else -1.0 for level in levels]
        soft[low_reliability] = 0.001 if levels[low_reliability] else -0.001
        soft[diversity_only] = 2.0 if levels[diversity_only] else -2.0
        result = protocol_constrained_list_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            additional_candidate_indices=(diversity_only,),
            budget=SoftListBudget(
                least_reliable_symbols=1,
                maximum_flips=2,
                maximum_regions=1,
                maximum_attempts=8,
            ),
        )
        recovered = [
            frame
            for frame in result.frames
            if frame.frame_with_fcs[:-2] == REFERENCE
        ]
        self.assertTrue(recovered)
        self.assertEqual(
            set(recovered[0].flipped_symbol_indices),
            {low_reliability, diversity_only},
        )

    def test_global_soft_cost_reaches_low_cost_three_flip_candidate(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        flipped = (3 * 8 + 40, 3 * 8 + 72, 3 * 8 + 104)
        for index in flipped:
            levels[index] ^= 1
        soft = [1.0 if level else -1.0 for level in levels]
        for rank, index in enumerate(flipped, start=1):
            soft[index] = (0.001 * rank) if levels[index] else (-0.001 * rank)
        result = protocol_constrained_list_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            budget=SoftListBudget(
                least_reliable_symbols=12,
                maximum_flips=3,
                maximum_regions=1,
                maximum_attempts=32,
            ),
        )
        recovered = [
            frame
            for frame in result.frames
            if frame.frame_with_fcs[:-2] == REFERENCE
        ]
        self.assertTrue(recovered)
        self.assertEqual(set(recovered[0].flipped_symbol_indices), set(flipped))

    def test_neighbor_radius_adds_adjacent_high_reliability_error(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        first = 3 * 8 + 72
        flipped = (first, first + 1)
        for index in flipped:
            levels[index] ^= 1
        soft = [1.0 if level else -1.0 for level in levels]
        soft[first] = 0.001 if levels[first] else -0.001
        soft[first + 1] = 2.0 if levels[first + 1] else -2.0
        result = protocol_constrained_list_decode(
            soft,
            threshold=0.0,
            event_start_symbol=16,
            budget=SoftListBudget(
                least_reliable_symbols=1,
                maximum_flips=2,
                maximum_regions=1,
                maximum_attempts=32,
                candidate_neighbor_radius=1,
            ),
        )
        recovered = [
            frame
            for frame in result.frames
            if frame.frame_with_fcs[:-2] == REFERENCE
        ]
        self.assertTrue(recovered)
        self.assertEqual(set(recovered[0].flipped_symbol_indices), set(flipped))

    def test_budget_validation(self) -> None:
        with self.assertRaises(ValueError):
            SoftListBudget(maximum_left_flag_hamming=4)
        with self.assertRaises(ValueError):
            SoftListBudget(maximum_boundary_patterns=0)
        with self.assertRaises(ValueError):
            SoftListBudget(candidate_neighbor_radius=3)
        with self.assertRaises(ValueError):
            SoftListBudget(error_unit_penalty=-0.1)
        with self.assertRaises(ValueError):
            SoftListBudget(maximum_map_seed_states=0)
        with self.assertRaises(ValueError):
            SoftListBudget(maximum_output_frames=0)
        with self.assertRaises(ValueError):
            SoftListBudget(maximum_output_frames=4097)
        with self.assertRaises(ValueError):
            SoftListBudget(maximum_attempts_per_region=0)


if __name__ == "__main__":
    unittest.main()
