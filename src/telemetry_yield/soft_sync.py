"""Bounded soft-decision helpers for G3RUH AX.25 replay experiments.

The functions in this module do not perform clock recovery.  They consume one
soft value per recovered symbol, preserve the exact SatNOGS NRZI/G3RUH order,
and provide a deterministic list decoder constrained by an exact or explicitly
bounded soft left delimiter, an exact right HDLC flag, the gr-satnogs frame-size
contract, and CRC-16/X.25.  A list candidate is never reported merely because
it resembles a reference frame.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Callable, Iterator, Sequence

from .ax25_validation import parse_ax25_ui
from .crc import crc16_x25, validate_ax25_fcs
from .symbol_boundary import AX25_FLAG_BITS, decode_satnogs_symbols


_AX25_GOOD_CRC_RESIDUE = 0x0F47
_MAX_OUTPUT_FRAMES = 4096


@dataclass(frozen=True)
class SoftListBudget:
    """Hard bounds for one deterministic protocol-constrained list search."""

    least_reliable_symbols: int = 20
    maximum_flips: int = 5
    maximum_regions: int = 4
    maximum_attempts: int = 100_000
    maximum_attempts_per_region: int | None = None
    maximum_boundary_patterns: int = 1
    maximum_left_flag_hamming: int = 0
    localization_radius_bits: int = 64
    require_exact_left_flag_after_boundary_state: bool = True
    candidate_neighbor_radius: int = 0
    error_unit_penalty: float = 0.0
    maximum_map_seed_states: int = 512
    maximum_output_frames: int = 1024

    def __post_init__(self) -> None:
        if self.least_reliable_symbols < 1:
            raise ValueError("least_reliable_symbols must be positive")
        if self.maximum_flips < 0:
            raise ValueError("maximum_flips must be non-negative")
        if (
            self.maximum_regions < 1
            or self.maximum_attempts < 1
            or self.maximum_boundary_patterns < 1
        ):
            raise ValueError("region and attempt budgets must be positive")
        if (
            self.maximum_attempts_per_region is not None
            and self.maximum_attempts_per_region < 1
        ):
            raise ValueError("per-region attempt budget must be positive")
        if not 0 <= self.maximum_left_flag_hamming <= 3:
            raise ValueError("maximum_left_flag_hamming must be between zero and three")
        if self.localization_radius_bits < 0:
            raise ValueError("localization_radius_bits must be non-negative")
        if not 0 <= self.candidate_neighbor_radius <= 2:
            raise ValueError("candidate_neighbor_radius must be between zero and two")
        if self.error_unit_penalty < 0 or self.error_unit_penalty != self.error_unit_penalty:
            raise ValueError("error_unit_penalty must be finite and non-negative")
        if self.maximum_map_seed_states < 1:
            raise ValueError("maximum_map_seed_states must be positive")
        if not 1 <= self.maximum_output_frames <= _MAX_OUTPUT_FRAMES:
            raise ValueError("maximum_output_frames is outside the hard bound")


@dataclass(frozen=True)
class SoftDecodedFrame:
    """One strict CRC-valid frame and the automated raw-level corrections."""

    frame_with_fcs: bytes
    left_flag_bit: int
    left_flag_hamming: int
    right_flag_bit: int
    flipped_symbol_indices: tuple[int, ...]
    flipped_symbol_reliabilities: tuple[float, ...]


@dataclass(frozen=True)
class SoftListResult:
    """Summary of a bounded list search."""

    threshold: float
    exact_flag_count: int
    eligible_regions: int
    searched_regions: int
    attempted_candidates: int
    budget_exhausted: bool
    frames: tuple[SoftDecodedFrame, ...]
    output_limit_reached: bool = False


def preamble_terminated_frame_starts(
    plain_bits: Sequence[int],
    *,
    minimum_consecutive_flags: int = 4,
    minimum_body_bits: int = 128,
) -> tuple[int, ...]:
    """Locate frame starts from repeated HDLC flags without an event oracle."""

    if minimum_consecutive_flags < 2 or minimum_body_bits < 1:
        raise ValueError("invalid preamble detector limits")
    flags = exact_flag_offsets(plain_bits)
    starts: list[int] = []
    run = 1
    for index in range(1, len(flags) - 1):
        if flags[index] - flags[index - 1] == 8:
            run += 1
        else:
            run = 1
        if (
            run >= minimum_consecutive_flags
            and flags[index + 1] - flags[index] >= minimum_body_bits
        ):
            starts.append(flags[index])
    return tuple(starts)


def exact_flag_offsets(bits: Sequence[int]) -> tuple[int, ...]:
    """Return all exact LSB-first HDLC flag starts."""

    checked = tuple(bits)
    if any(bit not in (0, 1) for bit in checked):
        raise ValueError("bits must contain only zero and one")
    flag = tuple(AX25_FLAG_BITS)
    return tuple(
        index
        for index in range(len(checked) - len(flag) + 1)
        if checked[index : index + len(flag)] == flag
    )


def raw_level_flip_effects(index: int, bit_count: int) -> tuple[int, ...]:
    """Return plaintext bits toggled by one raw level decision.

    NRZI makes a level error affect the current and following NRZI bits.  The
    ``x^17 + x^12 + 1`` descrambler maps each of those errors to three output
    positions, giving offsets 0, 1, 12, 13, 17, and 18.
    """

    if index < 0 or bit_count < 0:
        raise ValueError("index and bit_count must be non-negative")
    return tuple(
        position
        for offset in (0, 1, 12, 13, 17, 18)
        if (position := index + offset) < bit_count
    )


def apply_raw_level_flips(
    plain_bits: Sequence[int], flipped_symbol_indices: Sequence[int]
) -> tuple[int, ...]:
    """Apply the exact NRZI/G3RUH error impulse response to plaintext bits."""

    output = list(plain_bits)
    if any(bit not in (0, 1) for bit in output):
        raise ValueError("plain_bits must contain only zero and one")
    for index in flipped_symbol_indices:
        for position in raw_level_flip_effects(index, len(output)):
            output[position] ^= 1
    return tuple(output)


def _decode_region(
    plain_bits: Sequence[int], left: int, right: int
) -> bytes | None:
    region = plain_bits[left + 8 : right]
    return _decode_body(region)


def _decode_body(region: Sequence[int]) -> bytes | None:
    """Unstuff, pack, and check one body without intermediate bit lists.

    This is the list decoder's hot path.  The public replay helpers retain
    their deliberately simple reference implementation; here the same state
    machine packs LSB-first octets as it goes so a large bounded list search
    does not allocate two Python lists for every candidate.
    """

    if not region:
        return None
    frame = bytearray()
    ones = 0
    byte = 0
    bit_index = 0
    for bit in region:
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        if bit:
            ones += 1
            if ones > 5:
                return None
        elif ones == 5:
            ones = 0
            continue
        else:
            ones = 0
        byte |= int(bit) << bit_index
        bit_index += 1
        if bit_index == 8:
            frame.append(byte)
            byte = 0
            bit_index = 0
    if bit_index:
        return None
    if not 16 <= len(frame) < 1024 or not validate_ax25_fcs(frame):
        return None
    return bytes(frame)


def _unstuff_body_with_map(
    region: Sequence[int],
) -> tuple[bytes, tuple[int, ...]] | None:
    """Return packed octets and retained stuffed-bit positions.

    The position map makes CRC syndrome composition safe: two candidates are
    combined algebraically only when each preserves the exact same HDLC
    deletion map.  Any map-changing candidate is handled as a new seed state.
    """

    if not region:
        return None
    frame = bytearray()
    kept: list[int] = []
    ones = 0
    byte = 0
    bit_index = 0
    for position, bit in enumerate(region):
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        if bit:
            ones += 1
            if ones > 5:
                return None
        elif ones == 5:
            ones = 0
            continue
        else:
            ones = 0
        kept.append(position)
        byte |= int(bit) << bit_index
        bit_index += 1
        if bit_index == 8:
            frame.append(byte)
            byte = 0
            bit_index = 0
    if bit_index or not 16 <= len(frame) < 1024:
        return None
    return bytes(frame), tuple(kept)


def _body_after_flips(
    baseline_body: Sequence[int],
    *,
    body_start: int,
    plain_bit_count: int,
    flipped: Sequence[int],
) -> bytearray:
    output = bytearray(baseline_body)
    body_stop = body_start + len(output)
    for index in flipped:
        for position in raw_level_flip_effects(index, plain_bit_count):
            if body_start <= position < body_stop:
                output[position - body_start] ^= 1
    return output


def _invalid_one_runs(bits: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    """Return maximal runs that make an HDLC body immediately invalid."""

    runs: list[tuple[int, ...]] = []
    start = 0
    while start < len(bits):
        if not bits[start]:
            start += 1
            continue
        stop = start + 1
        while stop < len(bits) and bits[stop]:
            stop += 1
        if stop - start > 5:
            runs.append(tuple(range(start, stop)))
        start = stop
    return tuple(runs)


def _unit_body_effect(
    unit: Sequence[int],
    *,
    body_start: int,
    body_stop: int,
    plain_bit_count: int,
) -> frozenset[int]:
    """Return body-local positions toggled an odd number of times by a unit."""

    toggled: set[int] = set()
    for index in unit:
        for position in raw_level_flip_effects(index, plain_bit_count):
            if not body_start <= position < body_stop:
                continue
            local = position - body_start
            if local in toggled:
                toggled.remove(local)
            else:
                toggled.add(local)
    return frozenset(toggled)


def _stuffing_trace(
    region: Sequence[int],
) -> tuple[tuple[int, ...], frozenset[int]] | None:
    """Return the HDLC run state before each bit and deleted-zero offsets.

    The trace lets the map-repair search decide whether a sparse raw-level
    error unit changes the stuffing deletion map without reparsing the whole
    frame.  Once the modified and original one-run states meet after the last
    changed bit, the untouched suffix is necessarily identical.
    """

    states = [0]
    skipped: set[int] = set()
    ones = 0
    for position, bit in enumerate(region):
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        if bit:
            ones += 1
            if ones > 5:
                return None
        elif ones == 5:
            skipped.add(position)
            ones = 0
        else:
            ones = 0
        states.append(ones)
    return tuple(states), frozenset(skipped)


def _effect_preserves_stuffing_map(
    region: Sequence[int],
    *,
    trace: tuple[tuple[int, ...], frozenset[int]],
    toggled_positions: frozenset[int],
) -> bool:
    """Return whether a sparse toggle preserves the exact deletion map."""

    if not toggled_positions:
        return True
    states, skipped = trace
    first = min(toggled_positions)
    last = max(toggled_positions)
    if first < 0 or last >= len(region):
        raise ValueError("toggled body position is outside the region")
    ones = states[first]
    changed_map = False
    for position in range(first, len(region)):
        bit = int(region[position]) ^ int(position in toggled_positions)
        candidate_skipped = False
        if bit:
            ones += 1
            if ones > 5:
                return False
        elif ones == 5:
            candidate_skipped = True
            ones = 0
        else:
            ones = 0
        if candidate_skipped != (position in skipped):
            changed_map = True
        if position >= last and ones == states[position + 1]:
            return not changed_map
    return not changed_map


def _frame_after_map_preserving_effect(
    frame: bytes,
    *,
    retained_positions: Sequence[int],
    retained_index_by_position: dict[int, int],
    toggled_positions: frozenset[int],
) -> bytes:
    """Apply a sparse stuffed-body effect directly to unstuffed octets."""

    output = bytearray(frame)
    for position in toggled_positions:
        bit_index = retained_index_by_position.get(position)
        if bit_index is None:
            raise ValueError("map-preserving effect toggled a deleted bit")
        output[bit_index // 8] ^= 1 << (bit_index % 8)
    if len(retained_positions) != len(frame) * 8:
        raise ValueError("retained map and frame length disagree")
    return bytes(output)


def _map_repair_seed_states(
    baseline_body: Sequence[int],
    *,
    body_start: int,
    plain_bit_count: int,
    candidate_units: Sequence[tuple[int, ...]],
    unit_costs: Sequence[float],
    maximum_flips: int,
    maximum_attempts: int,
    maximum_states: int,
    maximum_valid_overflow_states: int = 1024,
    stop_when: Callable[[bytes], bool] | None = None,
) -> tuple[
    tuple[tuple[tuple[int, ...], float, bytes, tuple[int, ...]], ...],
    int,
    bool,
]:
    """Find low-cost seeds that restore a parseable HDLC stuffing map.

    CRC syndromes are linear only after the stuffing deletion map exists and
    remains fixed.  A damaged hard body can contain six-one runs and therefore
    have no map at all.  This bounded weight-then-soft-cost repair searches
    only units that can break a currently invalid run; every terminal seed is
    independently unstuffed before it is admitted to the later syndrome stage.
    """

    if (
        maximum_attempts < 1
        or maximum_states < 1
        or maximum_valid_overflow_states < 1
    ):
        return (), 0, True
    body_stop = body_start + len(baseline_body)
    effects = tuple(
        _unit_body_effect(
            unit,
            body_start=body_start,
            body_stop=body_stop,
            plain_bit_count=plain_bit_count,
        )
        for unit in candidate_units
    )
    pending: list[tuple[int, float, tuple[int, ...]]] = [(0, 0.0, ())]
    best_cost: dict[tuple[int, ...], float] = {(): 0.0}
    seeds: list[tuple[tuple[int, ...], float, bytes, tuple[int, ...]]] = []
    valid_overflow_states = 0
    attempts = 0
    stopped = False
    while pending and attempts < maximum_attempts:
        _, cost, flipped = heapq.heappop(pending)
        if cost != best_cost.get(flipped):
            continue
        attempts += 1
        candidate_body = _body_after_flips(
            baseline_body,
            body_start=body_start,
            plain_bit_count=plain_bit_count,
            flipped=flipped,
        )
        parsed = _unstuff_body_with_map(candidate_body)
        if parsed is not None:
            # Retain a bounded set of low-cost linearization seeds, but never
            # discard a complete CRC-valid repair discovered later in the
            # bounded map search.
            if len(seeds) < maximum_states:
                seeds.append((flipped, cost, *parsed))
            elif validate_ax25_fcs(parsed[0]):
                if valid_overflow_states >= maximum_valid_overflow_states:
                    raise ValueError(
                        "valid map-repair seed output bound exceeded"
                    )
                seeds.append((flipped, cost, *parsed))
                valid_overflow_states += 1
            if stop_when is not None and stop_when(parsed[0]):
                stopped = True
                break
            if len(flipped) >= maximum_flips:
                continue
            # The later CRC stage covers every additional unit that preserves
            # this map.  Continue the seed graph only across units that make
            # the body invalid again or transition to a different map.  A
            # sparse stuffing trace makes this exact test local instead of
            # reparsing the entire body once for every outgoing unit.
            trace = _stuffing_trace(candidate_body)
            assert trace is not None
            selected_flips = set(flipped)
            eligible_units = []
            for unit_index, unit in enumerate(candidate_units):
                if selected_flips & set(unit):
                    continue
                updated = tuple(sorted(flipped + unit))
                if len(updated) > maximum_flips or len(set(updated)) != len(updated):
                    continue
                if _effect_preserves_stuffing_map(
                    candidate_body,
                    trace=trace,
                    toggled_positions=effects[unit_index],
                ):
                    continue
                eligible_units.append(unit_index)
        elif len(flipped) >= maximum_flips:
            continue
        elif not (runs := _invalid_one_runs(candidate_body)):
            # The stuffing automaton is valid, so failure is an impossible
            # unstuffed octet count.  A unit that preserves the deletion map
            # cannot change that count and is therefore provably irrelevant
            # to map repair.  Unlike the old top-32 fallback this exact filter
            # does not silently discard a higher-ranked necessary decision.
            trace = _stuffing_trace(candidate_body)
            assert trace is not None
            selected_flips = set(flipped)
            eligible_units = [
                unit_index
                for unit_index, unit in enumerate(candidate_units)
                if not (selected_flips & set(unit))
                and len(flipped) + len(unit) <= maximum_flips
                and not _effect_preserves_stuffing_map(
                    candidate_body,
                    trace=trace,
                    toggled_positions=effects[unit_index],
                )
            ]
        else:
            selected_flips = set(flipped)
            choices_by_run: list[tuple[int, tuple[int, ...]]] = []
            for run in runs:
                positions = set(run)
                choices = tuple(
                    index
                    for index, unit in enumerate(candidate_units)
                    if not (selected_flips & set(unit))
                    and len(flipped) + len(unit) <= maximum_flips
                    and bool(effects[index] & positions)
                )
                choices_by_run.append((len(choices), choices))
            _, choices = min(choices_by_run, key=lambda item: item[0])
            eligible_units = list(choices)
        selected_flips = set(flipped)
        for unit_index in eligible_units:
            unit = candidate_units[unit_index]
            if selected_flips & set(unit):
                continue
            updated = tuple(sorted(flipped + unit))
            if len(updated) > maximum_flips or len(set(updated)) != len(updated):
                continue
            updated_cost = cost + unit_costs[unit_index]
            if updated_cost >= best_cost.get(updated, float("inf")):
                continue
            best_cost[updated] = updated_cost
            heapq.heappush(pending, (len(updated), updated_cost, updated))
    seeds.sort(key=lambda item: (len(item[0]), item[1], item[0]))
    return tuple(seeds), attempts, bool(pending) and not stopped


def _eligible_regions(
    plain_bits: Sequence[int],
    *,
    event_start_symbol: float | None,
    maximum_left_flag_hamming: int,
    localization_radius_bits: int,
) -> tuple[tuple[int, int, int], ...]:
    flags = exact_flag_offsets(plain_bits)
    minimum_stuffed_bits = 16 * 8
    maximum_unstuffed_bits = (1024 - 1) * 8
    maximum_stuffed_bits = maximum_unstuffed_bits + (
        maximum_unstuffed_bits + 4
    ) // 5
    if maximum_left_flag_hamming == 0 or event_start_symbol is None:
        left_candidates = [(left, 0) for left in flags]
    else:
        flag = tuple(AX25_FLAG_BITS)
        center = int(round(event_start_symbol))
        lower = max(0, center - localization_radius_bits)
        upper = min(
            len(plain_bits) - len(flag), center + localization_radius_bits
        )
        left_candidates = []
        for left in range(lower, upper + 1):
            distance = sum(
                observed != wanted
                for observed, wanted in zip(
                    plain_bits[left : left + 8], flag, strict=True
                )
            )
            if distance <= maximum_left_flag_hamming:
                left_candidates.append((left, distance))
    regions: list[tuple[int, int, int]] = []
    for left, distance in left_candidates:
        for right in flags:
            stuffed_bits = right - left - 8
            if stuffed_bits < minimum_stuffed_bits:
                continue
            if stuffed_bits > maximum_stuffed_bits:
                break
            regions.append((left, right, distance))
    if event_start_symbol is None:
        return tuple(regions)
    return tuple(
        sorted(
            regions,
            key=lambda item: (
                abs(item[0] - event_start_symbol),
                item[2],
                item[0],
                item[1],
            ),
        )
    )


def _boundary_state_patterns(
    values: Sequence[float],
    threshold: float,
    plain_bit_count: int,
    left_flag: int,
    required_flag_effect: int,
    maximum_patterns: int,
) -> tuple[tuple[int, ...], ...]:
    """Rank pre-body raw patterns that produce the required left-flag effect."""

    if required_flag_effect == 0 and maximum_patterns == 1:
        return ((),)
    body_start = left_flag + 8
    indices = tuple(range(body_start - 18, body_start))
    if indices[0] < 0:
        return ((),)
    flag_basis: list[int] = []
    body_basis: list[int] = []
    for index in indices:
        flag_effect = 0
        body_effect = 0
        for position in raw_level_flip_effects(index, plain_bit_count):
            if left_flag <= position < body_start:
                flag_effect ^= 1 << (position - left_flag)
            elif body_start <= position < body_start + 18:
                body_effect ^= 1 << (position - body_start)
        flag_basis.append(flag_effect)
        body_basis.append(body_effect)

    # Each reachable first-body effect needs only its lowest soft-cost raw
    # representation.  There are at most 2^10 effects after the eight exact
    # flag constraints, so retaining this quotient is bounded and stable.
    best_by_body_effect: dict[
        int, tuple[float, int, tuple[int, ...]]
    ] = {}
    for state in range(1 << 18):
        flag_effect = 0
        body_effect = 0
        flips: list[int] = []
        cost = 0.0
        remaining = state
        while remaining:
            lowest = remaining & -remaining
            bit = lowest.bit_length() - 1
            flag_effect ^= flag_basis[bit]
            body_effect ^= body_basis[bit]
            index = indices[bit]
            flips.append(index)
            cost += abs(values[index] - threshold)
            remaining ^= lowest
        if flag_effect != required_flag_effect:
            continue
        candidate = (cost, len(flips), tuple(flips))
        previous = best_by_body_effect.get(body_effect)
        if previous is None or candidate < previous:
            best_by_body_effect[body_effect] = candidate
    ranked = sorted(
        best_by_body_effect.values(),
        key=lambda item: (item[1], item[0], item[2]),
    )
    return tuple(item[2] for item in ranked[:maximum_patterns])


def _combinations_by_cost(
    items: Sequence[int],
    count: int,
    costs: Sequence[float],
) -> Iterator[tuple[int, ...]]:
    """Yield fixed-size combinations by increasing additive soft cost."""

    if count < 0 or count > len(items):
        return
    if count == 0:
        yield ()
        return
    start = tuple(range(count))
    heap: list[tuple[float, tuple[int, ...]]] = [
        (sum(costs[index] for index in start), start)
    ]
    seen = {start}
    while heap:
        _, positions = heapq.heappop(heap)
        yield tuple(items[index] for index in positions)
        for slot in range(count - 1, -1, -1):
            maximum = len(items) - count + slot
            if positions[slot] >= maximum:
                continue
            updated = list(positions)
            updated[slot] += 1
            for later in range(slot + 1, count):
                updated[later] = updated[later - 1] + 1
            candidate = tuple(updated)
            if candidate in seen:
                continue
            seen.add(candidate)
            heapq.heappush(
                heap,
                (sum(costs[index] for index in candidate), candidate),
            )


def _unit_combinations_by_cost(
    units: Sequence[tuple[int, ...]],
    count: int,
    costs: Sequence[float],
    maximum_symbol_flips: int,
) -> Iterator[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Yield non-overlapping error-unit combinations by soft cost.

    Units are either single raw decisions or short adjacent bursts.  The
    latter represent the correlated two-sample errors produced at a clipped
    phase transition without making every high-confidence neighbour an
    independent combinatorial branch.
    """

    for selected in _combinations_by_cost(
        range(len(units)), count, costs
    ):
        flattened = tuple(index for unit_index in selected for index in units[unit_index])
        if len(flattened) > maximum_symbol_flips:
            continue
        if len(set(flattened)) != len(flattened):
            continue
        yield tuple(sorted(flattened)), tuple(selected)


def protocol_constrained_list_decode(
    soft_symbols: Sequence[float],
    *,
    threshold: float,
    budget: SoftListBudget = SoftListBudget(),
    event_start_symbol: float | None = None,
    additional_candidate_indices: Sequence[int] = (),
) -> SoftListResult:
    """Search low-reliability raw decisions under strict HDLC/FCS constraints.

    ``event_start_symbol`` is optional localization calibration.  It only ranks
    already flag-delimited regions and never supplies frame bits, payload
    bytes, a length, or a CRC.  The same hard budget and stable tie-breaking
    apply with or without this hint.
    """

    values = tuple(float(value) for value in soft_symbols)
    if not values or any(value != value for value in values):
        raise ValueError("soft_symbols must be non-empty and finite")
    if threshold != threshold:
        raise ValueError("threshold must be finite")
    if event_start_symbol is not None and event_start_symbol < 0:
        raise ValueError("event_start_symbol must be non-negative")
    supplemental = tuple(int(index) for index in additional_candidate_indices)
    if any(index < 0 or index >= len(values) for index in supplemental):
        raise ValueError("additional candidate index is outside the symbol stream")

    levels = tuple(int(value >= threshold) for value in values)
    plain = tuple(decode_satnogs_symbols(levels, descramble=True))
    flags = exact_flag_offsets(plain)
    regions = _eligible_regions(
        plain,
        event_start_symbol=event_start_symbol,
        maximum_left_flag_hamming=budget.maximum_left_flag_hamming,
        localization_radius_bits=budget.localization_radius_bits,
    )
    selected_regions = regions[: budget.maximum_regions]

    attempts = 0
    exhausted = False
    frames: list[SoftDecodedFrame] = []
    seen: set[tuple[bytes, tuple[int, ...]]] = set()
    for left, right, left_hamming in selected_regions:
        body_start = left + 8
        body_stop = right
        baseline_body = plain[body_start:body_stop]
        required_flag_effect = 0
        if budget.require_exact_left_flag_after_boundary_state:
            for offset, (observed, wanted) in enumerate(
                zip(plain[left:body_start], AX25_FLAG_BITS, strict=True)
            ):
                if observed != wanted:
                    required_flag_effect ^= 1 << offset
        boundary_patterns = _boundary_state_patterns(
            values,
            threshold,
            len(plain),
            left,
            required_flag_effect,
            budget.maximum_boundary_patterns,
        )
        candidate_indices: list[int] = []
        for index in range(body_start, min(len(values), body_stop + 1)):
            effects = raw_level_flip_effects(index, len(plain))
            if not any(body_start <= position < body_stop for position in effects):
                continue
            if any(
                left <= position < left + 8 or right <= position < right + 8
                for position in effects
            ):
                continue
            candidate_indices.append(index)
        candidate_indices.sort(key=lambda index: (abs(values[index] - threshold), index))
        candidate_indices = candidate_indices[: budget.least_reliable_symbols]
        eligible_set = set(candidate_indices)
        neighbor_units: set[tuple[int, ...]] = set()
        for center in candidate_indices:
            for index in range(
                center - budget.candidate_neighbor_radius,
                center + budget.candidate_neighbor_radius + 1,
            ):
                if index < body_start or index > body_stop or index >= len(values):
                    continue
                effects = raw_level_flip_effects(index, len(plain))
                if not any(body_start <= position < body_stop for position in effects):
                    continue
                if any(
                    left <= position < left + 8 or right <= position < right + 8
                    for position in effects
                ):
                    continue
                if index != center:
                    neighbor_units.add(tuple(sorted((center, index))))
        for index in supplemental:
            effects = raw_level_flip_effects(index, len(plain))
            if index < body_start or index > body_stop:
                continue
            if not any(body_start <= position < body_stop for position in effects):
                continue
            if any(right <= position < right + 8 for position in effects):
                continue
            eligible_set.add(index)
        candidate_indices = sorted(
            eligible_set,
            key=lambda index: (abs(values[index] - threshold), index),
        )
        units = {(index,) for index in candidate_indices} | neighbor_units
        candidate_units = sorted(
            units,
            key=lambda unit: (
                sum(abs(values[index] - threshold) for index in unit),
                len(unit),
                unit,
            ),
        )
        candidate_costs = tuple(
            budget.error_unit_penalty
            + sum(abs(values[index] - threshold) for index in unit)
            for unit in candidate_units
        )
        # Merge every permitted Hamming weight into one soft-cost queue.  A
        # very reliable multi-symbol burst must be tried before a much less
        # likely one-symbol correction; enumerating weights serially can spend
        # the entire hard budget before reaching the correct weight.
        streams: list[
            Iterator[tuple[tuple[int, ...], tuple[int, ...]]]
        ] = []
        pending: list[
            tuple[
                float,
                int,
                tuple[int, ...],
                tuple[int, ...],
                tuple[int, ...],
            ]
        ] = []
        for unit_count in range(budget.maximum_flips + 1):
            for boundary_flips in boundary_patterns:
                stream = _unit_combinations_by_cost(
                    candidate_units,
                    unit_count,
                    candidate_costs,
                    budget.maximum_flips,
                )
                try:
                    interior, selected_units = next(stream)
                except StopIteration:
                    continue
                stream_index = len(streams)
                streams.append(stream)
                cost = sum(abs(values[index] - threshold) for index in boundary_flips)
                cost += sum(candidate_costs[index] for index in selected_units)
                heapq.heappush(
                    pending,
                    (
                        cost,
                        stream_index,
                        boundary_flips,
                        interior,
                        selected_units,
                    ),
                )
        attempted_patterns: set[tuple[int, ...]] = set()
        while pending:
            (
                _,
                stream_index,
                boundary_flips,
                interior_flips,
                selected_units,
            ) = heapq.heappop(pending)
            if attempts >= budget.maximum_attempts:
                exhausted = True
                break
            flipped = boundary_flips + interior_flips
            if flipped not in attempted_patterns:
                attempted_patterns.add(flipped)
                attempts += 1
                candidate_body = bytearray(baseline_body)
                for index in flipped:
                    for position in raw_level_flip_effects(index, len(plain)):
                        if body_start <= position < body_stop:
                            candidate_body[position - body_start] ^= 1
                frame = _decode_body(candidate_body)
                if frame is not None:
                    key = (frame, flipped)
                    if key not in seen:
                        seen.add(key)
                        frames.append(
                            SoftDecodedFrame(
                                frame_with_fcs=frame,
                                left_flag_bit=left,
                                left_flag_hamming=left_hamming,
                                right_flag_bit=right,
                                flipped_symbol_indices=tuple(flipped),
                                flipped_symbol_reliabilities=tuple(
                                    abs(values[index] - threshold)
                                    for index in flipped
                                ),
                            )
                        )
            stream = streams[stream_index]
            try:
                interior, selected_units = next(stream)
            except StopIteration:
                continue
            cost = sum(
                abs(values[index] - threshold) for index in boundary_flips
            )
            cost += sum(candidate_costs[index] for index in selected_units)
            heapq.heappush(
                pending,
                (
                    cost,
                    stream_index,
                    boundary_flips,
                    interior,
                    selected_units,
                ),
            )
        if exhausted:
            break

    return SoftListResult(
        threshold=float(threshold),
        exact_flag_count=len(flags),
        eligible_regions=len(regions),
        searched_regions=len(selected_regions),
        attempted_candidates=attempts,
        budget_exhausted=exhausted,
        frames=tuple(frames),
    )


def protocol_constrained_syndrome_decode(
    soft_symbols: Sequence[float],
    *,
    threshold: float,
    budget: SoftListBudget = SoftListBudget(),
    event_start_symbol: float | None = None,
    additional_candidate_indices: Sequence[int] = (),
    require_ax25_ui: bool = True,
    stop_after_first_frame: bool = False,
    stop_after_uncorrected_frame: bool = False,
    descramble: bool = True,
) -> SoftListResult:
    """Accelerate a bounded AX.25 list search with CRC syndromes.

    The decoder first finds a baseline (or a one-unit repair) whose HDLC body
    can be unstuffed.  Candidate error units that preserve that exact stuffing
    map have linear CRC effects, so only syndrome-matching combinations need a
    full body decode.  Every reported frame is still reconstructed and checked
    by the ordinary HDLC/FCS path; the syndrome is solely a reject filter.

    ``event_start_symbol`` may be supplied by a reference-free preamble
    detector.  It is never interpreted as reference truth and supplies no
    payload bits, length, callsign, or CRC value.
    """

    values = tuple(float(value) for value in soft_symbols)
    if not values or any(value != value for value in values):
        raise ValueError("soft_symbols must be non-empty and finite")
    if threshold != threshold:
        raise ValueError("threshold must be finite")
    if event_start_symbol is not None and event_start_symbol < 0:
        raise ValueError("event_start_symbol must be non-negative")
    supplemental = tuple(int(index) for index in additional_candidate_indices)
    if any(index < 0 or index >= len(values) for index in supplemental):
        raise ValueError("additional candidate index is outside the symbol stream")

    levels = tuple(int(value >= threshold) for value in values)
    plain = tuple(decode_satnogs_symbols(levels, descramble=descramble))
    flags = exact_flag_offsets(plain)
    regions = _eligible_regions(
        plain,
        event_start_symbol=event_start_symbol,
        maximum_left_flag_hamming=budget.maximum_left_flag_hamming,
        localization_radius_bits=budget.localization_radius_bits,
    )
    selected_regions = regions[: budget.maximum_regions]
    attempts = 0
    exhausted = False
    frames: list[SoftDecodedFrame] = []
    seen_frames: set[bytes] = set()
    accepted_uncorrected = False
    output_limit_reached = False
    attempts_per_region = (
        budget.maximum_attempts_per_region
        if budget.maximum_attempts_per_region is not None
        else max(
            1,
            budget.maximum_attempts // max(1, len(selected_regions)),
        )
    )

    for left, right, left_hamming in selected_regions:
        region_attempts = 0
        body_start = left + 8
        body_stop = right
        baseline_body = plain[body_start:body_stop]

        candidate_indices: list[int] = []
        for index in range(body_start, min(len(values), body_stop + 1)):
            effects = raw_level_flip_effects(index, len(plain))
            if not any(body_start <= position < body_stop for position in effects):
                continue
            if any(
                left <= position < left + 8 or right <= position < right + 8
                for position in effects
            ):
                continue
            candidate_indices.append(index)
        candidate_indices.sort(
            key=lambda index: (abs(values[index] - threshold), index)
        )
        candidate_indices = candidate_indices[: budget.least_reliable_symbols]
        singleton_indices = set(candidate_indices)
        for index in supplemental:
            effects = raw_level_flip_effects(index, len(plain))
            if index < body_start or index > body_stop:
                continue
            if not any(body_start <= position < body_stop for position in effects):
                continue
            if any(right <= position < right + 8 for position in effects):
                continue
            singleton_indices.add(index)

        units: set[tuple[int, ...]] = {(index,) for index in singleton_indices}
        for center in candidate_indices:
            for index in range(
                center - budget.candidate_neighbor_radius,
                center + budget.candidate_neighbor_radius + 1,
            ):
                if index == center or index < body_start or index > body_stop:
                    continue
                effects = raw_level_flip_effects(index, len(plain))
                if not any(body_start <= position < body_stop for position in effects):
                    continue
                if any(
                    left <= position < left + 8 or right <= position < right + 8
                    for position in effects
                ):
                    continue
                units.add(tuple(sorted((center, index))))
        candidate_units = sorted(
            units,
            key=lambda unit: (
                budget.error_unit_penalty
                + sum(abs(values[index] - threshold) for index in unit),
                len(unit),
                unit,
            ),
        )
        unit_costs = tuple(
            budget.error_unit_penalty
            + sum(abs(values[index] - threshold) for index in unit)
            for unit in candidate_units
        )

        baseline_unstuffed = _unstuff_body_with_map(baseline_body)
        seed_states: list[
            tuple[tuple[int, ...], float, bytes, tuple[int, ...]]
        ] = []
        if baseline_unstuffed is not None:
            seed_states.append(((), 0.0, *baseline_unstuffed))
        baseline_map = (
            baseline_unstuffed[1] if baseline_unstuffed is not None else None
        )
        if baseline_unstuffed is None:
            seed_unit_indices = tuple(
                index
                for index, unit in enumerate(candidate_units)
                if len(unit) == 1
            )
            stop_when = None
            if stop_after_first_frame:
                stop_when = lambda frame: (
                    validate_ax25_fcs(frame)
                    and (
                        not require_ax25_ui
                        or parse_ax25_ui(frame[:-2]) is not None
                    )
                )
            remaining_attempts = budget.maximum_attempts - attempts
            if remaining_attempts <= 0:
                exhausted = True
                break
            repaired, seed_attempts, seed_truncated = _map_repair_seed_states(
                baseline_body,
                body_start=body_start,
                plain_bit_count=len(plain),
                candidate_units=tuple(
                    candidate_units[index] for index in seed_unit_indices
                ),
                unit_costs=tuple(
                    unit_costs[index] for index in seed_unit_indices
                ),
                maximum_flips=budget.maximum_flips,
                maximum_attempts=min(
                    attempts_per_region,
                    remaining_attempts,
                ),
                maximum_states=budget.maximum_map_seed_states,
                maximum_valid_overflow_states=budget.maximum_output_frames,
                stop_when=stop_when,
            )
            attempts += seed_attempts
            region_attempts += seed_attempts
            seed_states.extend(repaired)
            if seed_truncated:
                exhausted = True
        else:
            for unit, cost in zip(candidate_units, unit_costs, strict=True):
                if len(unit) > budget.maximum_flips:
                    continue
                seeded_body = _body_after_flips(
                    baseline_body,
                    body_start=body_start,
                    plain_bit_count=len(plain),
                    flipped=unit,
                )
                seeded = _unstuff_body_with_map(seeded_body)
                if seeded is None or seeded[1] == baseline_map:
                    continue
                if len(seed_states) >= budget.maximum_map_seed_states:
                    raise ValueError("map seed state bound exceeded")
                seed_states.append((unit, cost, *seeded))

        # A map-repair seed may itself be the complete correction.  Validate
        # it through the ordinary FCS and protocol path before spending the
        # remaining syndrome budget.
        for seed, _, seed_frame, _ in seed_states:
            if not validate_ax25_fcs(seed_frame):
                continue
            payload = seed_frame[:-2]
            if require_ax25_ui and parse_ax25_ui(payload) is None:
                continue
            if seed_frame in seen_frames:
                continue
            seen_frames.add(seed_frame)
            frames.append(
                SoftDecodedFrame(
                    frame_with_fcs=seed_frame,
                    left_flag_bit=left,
                    left_flag_hamming=left_hamming,
                    right_flag_bit=right,
                    flipped_symbol_indices=seed,
                    flipped_symbol_reliabilities=tuple(
                        abs(values[index] - threshold) for index in seed
                    ),
                )
            )
            if stop_after_uncorrected_frame and not seed:
                accepted_uncorrected = True
                break
            if stop_after_first_frame:
                break
            if len(frames) >= budget.maximum_output_frames:
                output_limit_reached = True
                exhausted = True
                break
        if (
            output_limit_reached
            or accepted_uncorrected
            or (frames and stop_after_first_frame)
        ):
            break

        stream_records: list[
            tuple[
                Iterator[tuple[tuple[int, ...], tuple[int, ...]]],
                tuple[float, ...],
                tuple[int, ...],
                tuple[int, ...],
                int,
                float,
            ]
        ] = []
        pending: list[
            tuple[
                float,
                int,
                tuple[int, ...],
                tuple[int, ...],
            ]
        ] = []
        for seed, seed_cost, seed_frame, seed_map in seed_states:
            seed_crc = crc16_x25(seed_frame)
            target_effect = seed_crc ^ _AX25_GOOD_CRC_RESIDUE
            seed_body = _body_after_flips(
                baseline_body,
                body_start=body_start,
                plain_bit_count=len(plain),
                flipped=seed,
            )
            seed_trace = _stuffing_trace(seed_body)
            assert seed_trace is not None
            retained_index_by_position = {
                position: index for index, position in enumerate(seed_map)
            }
            compatible_units: list[tuple[int, ...]] = []
            compatible_costs: list[float] = []
            compatible_effects: list[int] = []
            for unit, cost in zip(candidate_units, unit_costs, strict=True):
                if set(unit) & set(seed):
                    continue
                combined = tuple(sorted(seed + unit))
                if len(combined) > budget.maximum_flips:
                    continue
                effect = _unit_body_effect(
                    unit,
                    body_start=body_start,
                    body_stop=body_stop,
                    plain_bit_count=len(plain),
                )
                if not _effect_preserves_stuffing_map(
                    seed_body,
                    trace=seed_trace,
                    toggled_positions=effect,
                ):
                    continue
                candidate_frame = _frame_after_map_preserving_effect(
                    seed_frame,
                    retained_positions=seed_map,
                    retained_index_by_position=retained_index_by_position,
                    toggled_positions=effect,
                )
                compatible_units.append(unit)
                compatible_costs.append(cost)
                compatible_effects.append(seed_crc ^ crc16_x25(candidate_frame))
            remaining_flips = budget.maximum_flips - len(seed)
            compatible_units_tuple = tuple(compatible_units)
            compatible_costs_tuple = tuple(compatible_costs)
            compatible_effects_tuple = tuple(compatible_effects)
            for unit_count in range(remaining_flips + 1):
                stream = _unit_combinations_by_cost(
                    compatible_units_tuple,
                    unit_count,
                    compatible_costs_tuple,
                    remaining_flips,
                )
                try:
                    interior, selected_units = next(stream)
                except StopIteration:
                    continue
                stream_index = len(stream_records)
                stream_records.append(
                    (
                        stream,
                        compatible_costs_tuple,
                        compatible_effects_tuple,
                        seed,
                        target_effect,
                        seed_cost,
                    )
                )
                cost = seed_cost + sum(
                    compatible_costs[index] for index in selected_units
                )
                heapq.heappush(
                    pending,
                    (cost, stream_index, interior, selected_units),
                )

        tried: set[tuple[int, ...]] = set()
        while pending:
            _, stream_index, interior, selected_units = heapq.heappop(pending)
            (
                stream,
                costs,
                effects,
                seed,
                target_effect,
                seed_cost,
            ) = stream_records[stream_index]
            flipped = tuple(sorted(seed + interior))
            if flipped not in tried:
                if (
                    region_attempts >= attempts_per_region
                    or attempts >= budget.maximum_attempts
                ):
                    exhausted = True
                    break
                tried.add(flipped)
                attempts += 1
                region_attempts += 1
                effect = 0
                for index in selected_units:
                    effect ^= effects[index]
                if effect == target_effect:
                    candidate_body = _body_after_flips(
                        baseline_body,
                        body_start=body_start,
                        plain_bit_count=len(plain),
                        flipped=flipped,
                    )
                    frame = _decode_body(candidate_body)
                    if frame is not None:
                        payload = frame[:-2]
                        valid_protocol = (
                            not require_ax25_ui
                            or parse_ax25_ui(payload) is not None
                        )
                        if valid_protocol and frame not in seen_frames:
                            seen_frames.add(frame)
                            frames.append(
                                SoftDecodedFrame(
                                    frame_with_fcs=frame,
                                    left_flag_bit=left,
                                    left_flag_hamming=left_hamming,
                                    right_flag_bit=right,
                                    flipped_symbol_indices=flipped,
                                    flipped_symbol_reliabilities=tuple(
                                        abs(values[index] - threshold)
                                        for index in flipped
                                    ),
                                )
                            )
                            if (
                                stop_after_uncorrected_frame
                                and not flipped
                            ):
                                accepted_uncorrected = True
                                pending.clear()
                                break
                            if stop_after_first_frame:
                                pending.clear()
                                break
                            if len(frames) >= budget.maximum_output_frames:
                                output_limit_reached = True
                                exhausted = True
                                pending.clear()
                                break
            try:
                interior, selected_units = next(stream)
            except StopIteration:
                continue
            cost = sum(costs[index] for index in selected_units)
            heapq.heappush(
                pending,
                (seed_cost + cost, stream_index, interior, selected_units),
            )
        if (
            output_limit_reached
            or accepted_uncorrected
            or (frames and stop_after_first_frame)
        ):
            break

    return SoftListResult(
        threshold=float(threshold),
        exact_flag_count=len(flags),
        eligible_regions=len(regions),
        searched_regions=len(selected_regions),
        attempted_candidates=attempts,
        budget_exhausted=exhausted,
        frames=tuple(frames),
        output_limit_reached=output_limit_reached,
    )
