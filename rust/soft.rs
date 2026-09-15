//! Reference-faithful bounded soft-decision search. Corrected CRC-valid frames
//! remain research candidates: this module never upgrades them to trusted RF
//! telemetry. Existing list/syndrome attempt, ordering and stuffing-map
//! semantics are preserved, including the explicitly documented legacy limits.

use crate::protocol::{crc16_x25, valid_ax25_fcs, valid_ax25_ui};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::{BTreeSet, BinaryHeap, HashMap, HashSet};
use std::sync::Arc;

const FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SoftListBudget {
    pub least_reliable_symbols: usize,
    pub maximum_flips: usize,
    pub maximum_regions: usize,
    pub maximum_attempts: usize,
    pub maximum_attempts_per_region: Option<usize>,
    pub maximum_boundary_patterns: usize,
    pub maximum_left_flag_hamming: usize,
    pub localization_radius_bits: usize,
    pub require_exact_left_flag_after_boundary_state: bool,
    pub candidate_neighbor_radius: usize,
    pub error_unit_penalty: f64,
    pub maximum_map_seed_states: usize,
    pub maximum_output_frames: usize,
}
impl Default for SoftListBudget {
    fn default() -> Self {
        Self {
            least_reliable_symbols: 20,
            maximum_flips: 5,
            maximum_regions: 4,
            maximum_attempts: 100_000,
            maximum_attempts_per_region: None,
            maximum_boundary_patterns: 1,
            maximum_left_flag_hamming: 0,
            localization_radius_bits: 64,
            require_exact_left_flag_after_boundary_state: true,
            candidate_neighbor_radius: 0,
            error_unit_penalty: 0.0,
            maximum_map_seed_states: 512,
            maximum_output_frames: 1024,
        }
    }
}
impl SoftListBudget {
    pub fn validate(&self) -> Result<(), String> {
        if self.least_reliable_symbols == 0 {
            return Err("least_reliable_symbols must be positive".into());
        }
        if self.maximum_regions == 0
            || self.maximum_attempts == 0
            || self.maximum_boundary_patterns == 0
        {
            return Err("region and attempt budgets must be positive".into());
        }
        if self.maximum_attempts_per_region == Some(0) {
            return Err("per-region attempt budget must be positive".into());
        }
        if self.maximum_left_flag_hamming > 3 {
            return Err("maximum_left_flag_hamming must be between zero and three".into());
        }
        if self.candidate_neighbor_radius > 2 {
            return Err("candidate_neighbor_radius must be between zero and two".into());
        }
        // Exactly the old check: positive infinity is not rejected here.
        if self.error_unit_penalty < 0.0 || self.error_unit_penalty.is_nan() {
            return Err("error_unit_penalty must be finite and non-negative".into());
        }
        if self.maximum_map_seed_states == 0 {
            return Err("maximum_map_seed_states must be positive".into());
        }
        if !(1..=4096).contains(&self.maximum_output_frames) {
            return Err("maximum_output_frames is outside the hard bound".into());
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct SoftDecodedFrame {
    pub frame_with_fcs: Vec<u8>,
    pub left_flag_bit: usize,
    pub left_flag_hamming: usize,
    pub right_flag_bit: usize,
    pub flipped_symbol_indices: Vec<usize>,
    pub flipped_symbol_reliabilities: Vec<f64>,
}
impl SoftDecodedFrame {
    pub fn is_repaired_candidate(&self) -> bool {
        !self.flipped_symbol_indices.is_empty()
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct SoftListResult {
    pub threshold: f64,
    pub exact_flag_count: usize,
    pub eligible_regions: usize,
    pub searched_regions: usize,
    pub attempted_candidates: usize,
    pub budget_exhausted: bool,
    pub frames: Vec<SoftDecodedFrame>,
    pub output_limit_reached: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SoftDecodeOptions {
    pub event_start_symbol: Option<f64>,
    pub additional_candidate_indices: Vec<usize>,
    pub require_ax25_ui: bool,
    pub stop_after_first_frame: bool,
    pub stop_after_uncorrected_frame: bool,
    pub descramble: bool,
}
impl Default for SoftDecodeOptions {
    fn default() -> Self {
        Self {
            event_start_symbol: None,
            additional_candidate_indices: vec![],
            require_ax25_ui: true,
            stop_after_first_frame: false,
            stop_after_uncorrected_frame: false,
            descramble: true,
        }
    }
}

fn checked_bits(bits: &[u8]) -> Result<(), String> {
    if bits.iter().any(|b| *b > 1) {
        Err("bits must contain only zero and one".into())
    } else {
        Ok(())
    }
}
pub fn exact_flag_offsets(bits: &[u8]) -> Result<Vec<usize>, String> {
    checked_bits(bits)?;
    Ok(bits
        .windows(8)
        .enumerate()
        .filter_map(|(i, v)| (v == FLAG).then_some(i))
        .collect())
}
pub fn preamble_terminated_frame_starts(
    bits: &[u8],
    minimum_consecutive_flags: usize,
    minimum_body_bits: usize,
) -> Result<Vec<usize>, String> {
    if minimum_consecutive_flags < 2 || minimum_body_bits == 0 {
        return Err("invalid preamble detector limits".into());
    }
    let flags = exact_flag_offsets(bits)?;
    let mut starts = vec![];
    let mut run = 1;
    for i in 1..flags.len().saturating_sub(1) {
        run = if flags[i] - flags[i - 1] == 8 {
            run + 1
        } else {
            1
        };
        if run >= minimum_consecutive_flags && flags[i + 1] - flags[i] >= minimum_body_bits {
            starts.push(flags[i]);
        }
    }
    Ok(starts)
}
pub fn raw_level_flip_effects(index: usize, bit_count: usize) -> Vec<usize> {
    [0usize, 1, 12, 13, 17, 18]
        .iter()
        .filter_map(|offset| index.checked_add(*offset).filter(|p| *p < bit_count))
        .collect()
}
pub fn apply_raw_level_flips(bits: &[u8], flipped: &[usize]) -> Result<Vec<u8>, String> {
    checked_bits(bits)?;
    let mut output = bits.to_vec();
    for index in flipped {
        for p in raw_level_flip_effects(*index, bits.len()) {
            output[p] ^= 1;
        }
    }
    Ok(output)
}
fn decode_satnogs(levels: &[u8], descramble: bool) -> Vec<u8> {
    let mut previous = 0;
    let nrzi: Vec<u8> = levels
        .iter()
        .map(|v| {
            let bit = u8::from(*v == previous);
            previous = *v;
            bit
        })
        .collect();
    let mut output = nrzi.clone();
    if descramble {
        for i in 12..nrzi.len() {
            output[i] ^= nrzi[i - 12];
        }
        for i in 17..nrzi.len() {
            output[i] ^= nrzi[i - 17];
        }
    }
    output
}
type UnstuffedBody = (Vec<u8>, Vec<usize>);
fn unstuff_body_with_map(region: &[u8]) -> Result<Option<UnstuffedBody>, String> {
    if region.is_empty() {
        return Ok(None);
    }
    let mut frame = vec![];
    let mut kept = vec![];
    let (mut ones, mut byte, mut bit_index) = (0, 0u8, 0);
    for (position, bit) in region.iter().enumerate() {
        if *bit > 1 {
            return Err("bits must contain only zero and one".into());
        }
        if *bit == 1 {
            ones += 1;
            if ones > 5 {
                return Ok(None);
            }
        } else if ones == 5 {
            ones = 0;
            continue;
        } else {
            ones = 0;
        }
        kept.push(position);
        byte |= *bit << bit_index;
        bit_index += 1;
        if bit_index == 8 {
            frame.push(byte);
            byte = 0;
            bit_index = 0;
        }
    }
    if bit_index != 0 || !(16..1024).contains(&frame.len()) {
        return Ok(None);
    }
    Ok(Some((frame, kept)))
}
fn decode_body(region: &[u8]) -> Result<Option<Vec<u8>>, String> {
    Ok(unstuff_body_with_map(region)?
        .and_then(|(frame, _)| valid_ax25_fcs(&frame).then_some(frame)))
}
fn body_after_flips(
    body: &[u8],
    body_start: usize,
    plain_count: usize,
    flipped: &[usize],
) -> Vec<u8> {
    let mut output = body.to_vec();
    for index in flipped {
        for p in raw_level_flip_effects(*index, plain_count) {
            if p >= body_start && p - body_start < body.len() {
                output[p - body_start] ^= 1;
            }
        }
    }
    output
}
fn invalid_one_runs(bits: &[u8]) -> Vec<Vec<usize>> {
    let mut runs = vec![];
    let mut start = 0;
    while start < bits.len() {
        if bits[start] == 0 {
            start += 1;
            continue;
        }
        let mut stop = start + 1;
        while stop < bits.len() && bits[stop] != 0 {
            stop += 1;
        }
        if stop - start > 5 {
            runs.push((start..stop).collect());
        }
        start = stop;
    }
    runs
}
fn unit_body_effect(
    unit: &[usize],
    body_start: usize,
    body_stop: usize,
    plain_count: usize,
) -> BTreeSet<usize> {
    let mut effect = BTreeSet::new();
    for i in unit {
        for p in raw_level_flip_effects(*i, plain_count) {
            if p >= body_start && p < body_stop {
                let local = p - body_start;
                if !effect.insert(local) {
                    effect.remove(&local);
                }
            }
        }
    }
    effect
}
type StuffingTrace = (Vec<usize>, BTreeSet<usize>);
fn stuffing_trace(region: &[u8]) -> Result<Option<StuffingTrace>, String> {
    let mut states = vec![0];
    let mut skipped = BTreeSet::new();
    let mut ones = 0;
    for (position, bit) in region.iter().enumerate() {
        if *bit > 1 {
            return Err("bits must contain only zero and one".into());
        }
        if *bit == 1 {
            ones += 1;
            if ones > 5 {
                return Ok(None);
            }
        } else if ones == 5 {
            skipped.insert(position);
            ones = 0;
        } else {
            ones = 0;
        }
        states.push(ones);
    }
    Ok(Some((states, skipped)))
}
fn effect_preserves_stuffing_map(
    region: &[u8],
    trace: &StuffingTrace,
    toggled: &BTreeSet<usize>,
) -> Result<bool, String> {
    let Some(first) = toggled.first().copied() else {
        return Ok(true);
    };
    let last = *toggled.last().unwrap();
    if last >= region.len() {
        return Err("toggled body position is outside the region".into());
    }
    let (states, skipped) = trace;
    let mut ones = states[first];
    let mut changed = false;
    for position in first..region.len() {
        let bit = region[position] ^ u8::from(toggled.contains(&position));
        let mut deleted = false;
        if bit == 1 {
            ones += 1;
            if ones > 5 {
                return Ok(false);
            }
        } else if ones == 5 {
            deleted = true;
            ones = 0;
        } else {
            ones = 0;
        }
        if deleted != skipped.contains(&position) {
            changed = true;
        }
        if position >= last && ones == states[position + 1] {
            return Ok(!changed);
        }
    }
    Ok(!changed)
}
fn frame_after_map_preserving_effect(
    frame: &[u8],
    retained: &[usize],
    index_by_position: &HashMap<usize, usize>,
    effect: &BTreeSet<usize>,
) -> Result<Vec<u8>, String> {
    let mut output = frame.to_vec();
    for position in effect {
        let bit = *index_by_position
            .get(position)
            .ok_or("map-preserving effect toggled a deleted bit")?;
        output[bit / 8] ^= 1 << (bit % 8);
    }
    if retained.len() != frame.len() * 8 {
        return Err("retained map and frame length disagree".into());
    }
    Ok(output)
}

fn fcmp(a: f64, b: f64) -> Ordering {
    a.partial_cmp(&b).unwrap_or(Ordering::Equal)
}

// Python 3.12 uses Neumaier compensation in built-in sum(). Queue tie-breaking
// depends on the exact resulting double, so a naive Rust Iterator::sum would
// change a bounded search even if it only differed by one ULP.
// Reference: CPython v3.12.3 Python/bltinmodule.c, builtin_sum_impl.
fn python_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut total = 0.0f64;
    let mut compensation = 0.0f64;
    for value in values {
        let updated = total + value;
        compensation += if total.abs() >= value.abs() {
            (total - updated) + value
        } else {
            (value - updated) + total
        };
        total = updated;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    total
}
/// Reversed ordering makes BinaryHeap a Python-compatible cost/lexicographic min-heap.
#[derive(Clone, Debug)]
struct QueueEntry {
    weight: usize,
    cost: f64,
    key: Vec<usize>,
}
impl PartialEq for QueueEntry {
    fn eq(&self, o: &Self) -> bool {
        self.weight == o.weight && self.cost == o.cost && self.key == o.key
    }
}
impl Eq for QueueEntry {}
impl Ord for QueueEntry {
    fn cmp(&self, o: &Self) -> Ordering {
        o.weight
            .cmp(&self.weight)
            .then_with(|| fcmp(o.cost, self.cost))
            .then_with(|| o.key.cmp(&self.key))
    }
}
impl PartialOrd for QueueEntry {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}

struct Combinations {
    units: Arc<Vec<Vec<usize>>>,
    costs: Arc<Vec<f64>>,
    maximum_flips: usize,
    count: usize,
    pending: BinaryHeap<QueueEntry>,
    seen: HashSet<Vec<usize>>,
}
impl Combinations {
    fn new(
        units: Arc<Vec<Vec<usize>>>,
        costs: Arc<Vec<f64>>,
        count: usize,
        maximum_flips: usize,
    ) -> Self {
        let mut pending = BinaryHeap::new();
        let mut seen = HashSet::new();
        if count <= units.len() {
            let start: Vec<usize> = (0..count).collect();
            let cost = python_sum(start.iter().map(|i| costs[*i]));
            seen.insert(start.clone());
            pending.push(QueueEntry {
                weight: 0,
                cost,
                key: start,
            });
        }
        Self {
            units,
            costs,
            maximum_flips,
            count,
            pending,
            seen,
        }
    }
    fn next(&mut self) -> Option<(Vec<usize>, Vec<usize>)> {
        while let Some(entry) = self.pending.pop() {
            let selected = entry.key;
            for slot in (0..self.count).rev() {
                let maximum = self.units.len() - self.count + slot;
                if selected[slot] >= maximum {
                    continue;
                }
                let mut candidate = selected.clone();
                candidate[slot] += 1;
                for later in slot + 1..self.count {
                    candidate[later] = candidate[later - 1] + 1;
                }
                if self.seen.insert(candidate.clone()) {
                    let cost = python_sum(candidate.iter().map(|i| self.costs[*i]));
                    self.pending.push(QueueEntry {
                        weight: 0,
                        cost,
                        key: candidate,
                    });
                }
            }
            let mut flattened: Vec<usize> = selected
                .iter()
                .flat_map(|i| self.units[*i].iter().copied())
                .collect();
            if flattened.len() > self.maximum_flips {
                continue;
            }
            flattened.sort_unstable();
            if flattened.windows(2).any(|w| w[0] == w[1]) {
                continue;
            }
            return Some((flattened, selected));
        }
        None
    }
}

type Region = (usize, usize, usize);
fn eligible_regions(
    plain: &[u8],
    options: &SoftDecodeOptions,
    budget: &SoftListBudget,
) -> Result<(usize, Vec<Region>), String> {
    let flags = exact_flag_offsets(plain)?;
    let mut lefts = vec![];
    if budget.maximum_left_flag_hamming == 0 || options.event_start_symbol.is_none() {
        lefts.extend(flags.iter().map(|left| (*left, 0)));
    } else if let Some(event_start) = options.event_start_symbol {
        let center = event_start.round_ties_even();
        if !center.is_finite() || center > isize::MAX as f64 {
            return Err("event start cannot be rounded to a supported index".into());
        }
        let center = center as usize;
        let lower = center.saturating_sub(budget.localization_radius_bits);
        if plain.len() >= 8 {
            let upper =
                (plain.len() - 8).min(center.saturating_add(budget.localization_radius_bits));
            for left in lower..=upper {
                let distance = plain[left..left + 8]
                    .iter()
                    .zip(FLAG)
                    .filter(|(a, b)| **a != *b)
                    .count();
                if distance <= budget.maximum_left_flag_hamming {
                    lefts.push((left, distance));
                }
            }
        }
    }
    let maximum = (1024usize - 1) * 8 + ((1024usize - 1) * 8).div_ceil(5);
    if let Some(event) = options.event_start_symbol {
        // Every left candidate is unique. The original full-region comparator
        // orders all rows for a given left contiguously, with right ascending
        // last. Sorting these left keys first is therefore exactly equivalent
        // to sorting the Cartesian result, including fcmp's NaN/Inf semantics.
        lefts.sort_by(|a, b| {
            fcmp((a.0 as f64 - event).abs(), (b.0 as f64 - event).abs())
                .then(a.1.cmp(&b.1))
                .then(a.0.cmp(&b.0))
        });
    }
    let mut eligible_count = 0usize;
    let mut regions = vec![];
    for (left, distance) in lefts {
        // Exact inclusive body-length bounds from the original nested loop.
        // The sorted flags let us count every eligible pair without retaining
        // pairs that prepare would immediately discard after maximum_regions.
        let lower = flags.partition_point(|right| *right < left + 8 + 128);
        let upper = flags.partition_point(|right| *right <= left + 8 + maximum);
        eligible_count = eligible_count
            .checked_add(upper - lower)
            .ok_or("eligible region count overflows the supported index range")?;
        let remaining = budget.maximum_regions - regions.len();
        regions.extend(
            flags[lower..upper]
                .iter()
                .take(remaining)
                .map(|right| (left, *right, distance)),
        );
    }
    Ok((eligible_count, regions))
}
fn boundary_state_patterns(
    values: &[f64],
    threshold: f64,
    plain_count: usize,
    left: usize,
    required: usize,
    maximum: usize,
) -> Vec<Vec<usize>> {
    if required == 0 && maximum == 1 {
        return vec![vec![]];
    }
    let body_start = left + 8;
    if body_start < 18 {
        return vec![vec![]];
    }
    let indices: Vec<usize> = (body_start - 18..body_start).collect();
    let mut flag_basis = [0usize; 18];
    let mut body_basis = [0usize; 18];
    for (i, index) in indices.iter().enumerate() {
        for p in raw_level_flip_effects(*index, plain_count) {
            if p >= left && p < body_start {
                flag_basis[i] ^= 1 << (p - left);
            } else if p >= body_start && p < body_start + 18 {
                body_basis[i] ^= 1 << (p - body_start);
            }
        }
    }
    let mut best: HashMap<usize, (f64, Vec<usize>)> = HashMap::new();
    for state in 0..1usize << 18 {
        let (mut flag_effect, mut body_effect, mut cost) = (0, 0, 0.0);
        let mut flips = vec![];
        let mut remaining = state;
        while remaining != 0 {
            let bit = remaining.trailing_zeros() as usize;
            flag_effect ^= flag_basis[bit];
            body_effect ^= body_basis[bit];
            let index = indices[bit];
            flips.push(index);
            cost += (values[index] - threshold).abs();
            remaining &= remaining - 1;
        }
        if flag_effect != required {
            continue;
        }
        let better = best.get(&body_effect).is_none_or(|(old, old_flips)| {
            fcmp(cost, *old)
                .then(flips.len().cmp(&old_flips.len()))
                .then(flips.cmp(old_flips))
                .is_lt()
        });
        if better {
            best.insert(body_effect, (cost, flips));
        }
    }
    let mut ranked: Vec<_> = best.into_values().collect();
    ranked.sort_by(|a, b| {
        a.1.len()
            .cmp(&b.1.len())
            .then_with(|| fcmp(a.0, b.0))
            .then(a.1.cmp(&b.1))
    });
    ranked
        .into_iter()
        .take(maximum)
        .map(|(_, flips)| flips)
        .collect()
}

fn prepare(
    values: &[f64],
    threshold: f64,
    budget: &SoftListBudget,
    options: &SoftDecodeOptions,
    descramble: bool,
) -> Result<(Vec<u8>, Vec<Region>, SoftListResult), String> {
    budget.validate()?;
    if values.is_empty() || values.iter().any(|v| v.is_nan()) {
        return Err("soft_symbols must be non-empty and finite".into());
    }
    if threshold.is_nan() {
        return Err("threshold must be finite".into());
    }
    if options.event_start_symbol.is_some_and(|v| v < 0.0) {
        return Err("event_start_symbol must be non-negative".into());
    }
    if options
        .additional_candidate_indices
        .iter()
        .any(|i| *i >= values.len())
    {
        return Err("additional candidate index is outside the symbol stream".into());
    }
    let levels: Vec<u8> = values.iter().map(|v| u8::from(*v >= threshold)).collect();
    let plain = decode_satnogs(&levels, descramble);
    let flags = exact_flag_offsets(&plain)?;
    let (eligible_count, regions) = eligible_regions(&plain, options, budget)?;
    let result = SoftListResult {
        threshold,
        exact_flag_count: flags.len(),
        eligible_regions: eligible_count,
        searched_regions: regions.len(),
        attempted_candidates: 0,
        budget_exhausted: false,
        frames: vec![],
        output_limit_reached: false,
    };
    Ok((plain, regions, result))
}
fn candidate_units(
    values: &[f64],
    threshold: f64,
    plain_count: usize,
    region: Region,
    budget: &SoftListBudget,
    options: &SoftDecodeOptions,
    syndrome: bool,
) -> (Arc<Vec<Vec<usize>>>, Arc<Vec<f64>>) {
    let (left, right, _) = region;
    let start = left + 8;
    let eligible = |index: usize| {
        let effects = raw_level_flip_effects(index, plain_count);
        effects.iter().any(|p| *p >= start && *p < right)
            && !effects
                .iter()
                .any(|p| (*p >= left && *p < left + 8) || (*p >= right && *p < right + 8))
    };
    let mut candidates: Vec<usize> = (start..values.len().min(right + 1))
        .filter(|i| eligible(*i))
        .collect();
    candidates.sort_by(|a, b| {
        fcmp(
            (values[*a] - threshold).abs(),
            (values[*b] - threshold).abs(),
        )
        .then(a.cmp(b))
    });
    candidates.truncate(budget.least_reliable_symbols);
    let mut units: BTreeSet<Vec<usize>> = candidates.iter().map(|i| vec![*i]).collect();
    for center in &candidates {
        for index in center.saturating_sub(budget.candidate_neighbor_radius)
            ..=center.saturating_add(budget.candidate_neighbor_radius)
        {
            if index == *center
                || index < start
                || index > right
                || index >= values.len()
                || !eligible(index)
            {
                continue;
            }
            let mut unit = vec![*center, index];
            unit.sort_unstable();
            units.insert(unit);
        }
    }
    for index in &options.additional_candidate_indices {
        let effects = raw_level_flip_effects(*index, plain_count);
        if *index < start
            || *index > right
            || !effects.iter().any(|p| *p >= start && *p < right)
            || effects.iter().any(|p| *p >= right && *p < right + 8)
        {
            continue;
        }
        units.insert(vec![*index]);
    }
    let cost = |unit: &Vec<usize>| python_sum(unit.iter().map(|i| (values[*i] - threshold).abs()));
    let mut units: Vec<_> = units.into_iter().collect();
    units.sort_by(|a, b| {
        let penalty = if syndrome {
            budget.error_unit_penalty
        } else {
            0.0
        };
        fcmp(penalty + cost(a), penalty + cost(b))
            .then(a.len().cmp(&b.len()))
            .then(a.cmp(b))
    });
    let costs = units
        .iter()
        .map(|u| budget.error_unit_penalty + cost(u))
        .collect();
    (Arc::new(units), Arc::new(costs))
}
fn record(
    frame: Vec<u8>,
    region: Region,
    flipped: Vec<usize>,
    values: &[f64],
    threshold: f64,
) -> SoftDecodedFrame {
    let reliability = flipped
        .iter()
        .map(|i| (values[*i] - threshold).abs())
        .collect();
    SoftDecodedFrame {
        frame_with_fcs: frame,
        left_flag_bit: region.0,
        right_flag_bit: region.1,
        left_flag_hamming: region.2,
        flipped_symbol_indices: flipped,
        flipped_symbol_reliabilities: reliability,
    }
}

/// Faithful legacy exhaustive list path. Unlike the syndrome path the Python
/// reference does not apply per-region/output limits here; callers needing
/// those limits must use syndrome or a separately bounded external container.
pub fn protocol_constrained_list_decode(
    values: &[f64],
    threshold: f64,
    budget: &SoftListBudget,
    options: &SoftDecodeOptions,
) -> Result<SoftListResult, String> {
    let (plain, regions, mut result) = prepare(values, threshold, budget, options, true)?;
    let mut seen = HashSet::new();
    for region in regions {
        let (left, right, _) = region;
        let start = left + 8;
        let baseline = &plain[start..right];
        let required = if budget.require_exact_left_flag_after_boundary_state {
            plain[left..start]
                .iter()
                .zip(FLAG)
                .enumerate()
                .fold(0, |mask, (i, (a, b))| {
                    mask | if *a != b { 1 << i } else { 0 }
                })
        } else {
            0
        };
        let boundaries = boundary_state_patterns(
            values,
            threshold,
            plain.len(),
            left,
            required,
            budget.maximum_boundary_patterns,
        );
        let (units, costs) = candidate_units(
            values,
            threshold,
            plain.len(),
            region,
            budget,
            options,
            false,
        );
        let mut streams: Vec<Combinations> = vec![];
        let mut records: Vec<(Vec<usize>, Vec<usize>, Vec<usize>)> = vec![];
        let mut pending = BinaryHeap::new();
        for count in 0..=budget.maximum_flips {
            for boundary in &boundaries {
                let mut stream =
                    Combinations::new(units.clone(), costs.clone(), count, budget.maximum_flips);
                let Some((interior, selected)) = stream.next() else {
                    continue;
                };
                let index = streams.len();
                streams.push(stream);
                let cost = python_sum(boundary.iter().map(|i| (values[*i] - threshold).abs()))
                    + python_sum(selected.iter().map(|i| costs[*i]));
                records.push((boundary.clone(), interior, selected));
                pending.push(QueueEntry {
                    weight: 0,
                    cost,
                    key: vec![index],
                });
            }
        }
        let mut tried = HashSet::new();
        while let Some(entry) = pending.pop() {
            let index = entry.key[0];
            let (boundary, interior, _) = records[index].clone();
            if result.attempted_candidates >= budget.maximum_attempts {
                result.budget_exhausted = true;
                break;
            }
            let mut flipped = boundary.clone();
            flipped.extend(interior);
            if tried.insert(flipped.clone()) {
                result.attempted_candidates += 1;
                let body = body_after_flips(baseline, start, plain.len(), &flipped);
                if let Some(frame) = decode_body(&body)?
                    && seen.insert((frame.clone(), flipped.clone()))
                {
                    result
                        .frames
                        .push(record(frame, region, flipped, values, threshold));
                }
            }
            if let Some((interior, selected)) = streams[index].next() {
                let cost = python_sum(boundary.iter().map(|i| (values[*i] - threshold).abs()))
                    + python_sum(selected.iter().map(|i| costs[*i]));
                records[index] = (boundary, interior, selected);
                pending.push(QueueEntry {
                    weight: 0,
                    cost,
                    key: vec![index],
                });
            }
        }
        if result.budget_exhausted {
            break;
        }
    }
    Ok(result)
}

type SeedState = (Vec<usize>, f64, Vec<u8>, Vec<usize>);
type StopWhen<'a> = Option<&'a dyn Fn(&[u8]) -> bool>;
// Separate bounds preserve the frozen scheduler's per-region/shared semantics.
#[allow(clippy::too_many_arguments)]
fn map_repair_seed_states(
    baseline: &[u8],
    body_start: usize,
    plain_count: usize,
    units: &[Vec<usize>],
    costs: &[f64],
    maximum_flips: usize,
    maximum_attempts: usize,
    maximum_states: usize,
    maximum_overflow: usize,
    stop_when: StopWhen<'_>,
) -> Result<(Vec<SeedState>, usize, bool), String> {
    if maximum_attempts < 1 || maximum_states < 1 || maximum_overflow < 1 {
        return Ok((vec![], 0, true));
    }
    let stop = body_start + baseline.len();
    let effects: Vec<_> = units
        .iter()
        .map(|u| unit_body_effect(u, body_start, stop, plain_count))
        .collect();
    let mut pending = BinaryHeap::new();
    pending.push(QueueEntry {
        weight: 0,
        cost: 0.0,
        key: vec![],
    });
    let mut best = HashMap::new();
    best.insert(vec![], 0.0);
    let (mut attempts, mut overflow, mut stopped) = (0, 0, false);
    let mut seeds = vec![];
    while !pending.is_empty() && attempts < maximum_attempts {
        let entry = pending.pop().unwrap();
        let flipped = entry.key;
        let cost = entry.cost;
        if best.get(&flipped) != Some(&cost) {
            continue;
        }
        attempts += 1;
        let body = body_after_flips(baseline, body_start, plain_count, &flipped);
        let parsed = unstuff_body_with_map(&body)?;
        let mut eligible = vec![];
        let overlaps = |unit: &Vec<usize>| unit.iter().any(|i| flipped.contains(i));
        if let Some((frame, map)) = parsed {
            if seeds.len() < maximum_states {
                seeds.push((flipped.clone(), cost, frame.clone(), map));
            } else if valid_ax25_fcs(&frame) {
                if overflow >= maximum_overflow {
                    return Err("valid map-repair seed output bound exceeded".into());
                }
                seeds.push((flipped.clone(), cost, frame.clone(), map));
                overflow += 1;
            }
            if stop_when.is_some_and(|stop_when| stop_when(&frame)) {
                stopped = true;
                break;
            }
            if flipped.len() >= maximum_flips {
                continue;
            }
            let trace = stuffing_trace(&body)?.ok_or("parseable body lacks stuffing trace")?;
            for (index, unit) in units.iter().enumerate() {
                if overlaps(unit) || flipped.len() + unit.len() > maximum_flips {
                    continue;
                }
                if !effect_preserves_stuffing_map(&body, &trace, &effects[index])? {
                    eligible.push(index);
                }
            }
        } else if flipped.len() >= maximum_flips {
            continue;
        } else {
            let runs = invalid_one_runs(&body);
            if runs.is_empty() {
                let trace = stuffing_trace(&body)?.ok_or("valid automaton lacks trace")?;
                for (index, unit) in units.iter().enumerate() {
                    if overlaps(unit) || flipped.len() + unit.len() > maximum_flips {
                        continue;
                    }
                    if !effect_preserves_stuffing_map(&body, &trace, &effects[index])? {
                        eligible.push(index);
                    }
                }
            } else {
                let mut choices_by_run = vec![];
                for run in runs {
                    let choices: Vec<usize> = units
                        .iter()
                        .enumerate()
                        .filter_map(|(i, u)| {
                            (!overlaps(u)
                                && flipped.len() + u.len() <= maximum_flips
                                && run.iter().any(|p| effects[i].contains(p)))
                            .then_some(i)
                        })
                        .collect();
                    choices_by_run.push(choices);
                }
                eligible = choices_by_run.into_iter().min_by_key(|v| v.len()).unwrap();
            }
        }
        for index in eligible {
            let unit = &units[index];
            if overlaps(unit) {
                continue;
            }
            let mut updated = flipped.clone();
            updated.extend(unit);
            updated.sort_unstable();
            if updated.len() > maximum_flips || updated.windows(2).any(|w| w[0] == w[1]) {
                continue;
            }
            let updated_cost = cost + costs[index];
            if updated_cost >= *best.get(&updated).unwrap_or(&f64::INFINITY) {
                continue;
            }
            best.insert(updated.clone(), updated_cost);
            pending.push(QueueEntry {
                weight: updated.len(),
                cost: updated_cost,
                key: updated,
            });
        }
    }
    seeds.sort_by(|a, b| {
        a.0.len()
            .cmp(&b.0.len())
            .then_with(|| fcmp(a.1, b.1))
            .then(a.0.cmp(&b.0))
    });
    Ok((seeds, attempts, !pending.is_empty() && !stopped))
}

struct SyndromeStream {
    combinations: Combinations,
    costs: Arc<Vec<f64>>,
    effects: Vec<u16>,
    seed: Vec<usize>,
    target: u16,
    seed_cost: f64,
    interior: Vec<usize>,
    selected: Vec<usize>,
}

/// Syndrome agreement is only a rejection filter. Every accepted candidate is
/// rebuilt through HDLC/FCS and, by default, strict AX.25 UI validation.
/// `descramble=false` preserves the legacy raw impulse convention, not a new
/// claim that plain-mode correction has been independently designed/qualified.
pub fn protocol_constrained_syndrome_decode(
    values: &[f64],
    threshold: f64,
    budget: &SoftListBudget,
    options: &SoftDecodeOptions,
) -> Result<SoftListResult, String> {
    let (plain, regions, mut result) =
        prepare(values, threshold, budget, options, options.descramble)?;
    let mut seen = HashSet::new();
    let mut accepted_uncorrected = false;
    let attempts_per_region = budget
        .maximum_attempts_per_region
        .unwrap_or_else(|| (budget.maximum_attempts / regions.len().max(1)).max(1));
    for region in regions {
        let (left, right, _) = region;
        let start = left + 8;
        let baseline = &plain[start..right];
        let mut region_attempts = 0;
        let (units, costs) = candidate_units(
            values,
            threshold,
            plain.len(),
            region,
            budget,
            options,
            true,
        );
        let baseline_unstuffed = unstuff_body_with_map(baseline)?;
        let mut seeds: Vec<SeedState> = vec![];
        if let Some((frame, map)) = &baseline_unstuffed {
            seeds.push((vec![], 0.0, frame.clone(), map.clone()));
        }
        if baseline_unstuffed.is_none() {
            let singletons: Vec<usize> = units
                .iter()
                .enumerate()
                .filter_map(|(i, u)| (u.len() == 1).then_some(i))
                .collect();
            let seed_units: Vec<_> = singletons.iter().map(|i| units[*i].clone()).collect();
            let seed_costs: Vec<_> = singletons.iter().map(|i| costs[*i]).collect();
            let stop_when = |frame: &[u8]| {
                valid_ax25_fcs(frame)
                    && (!options.require_ax25_ui || valid_ax25_ui(&frame[..frame.len() - 2]))
            };
            let remaining = budget
                .maximum_attempts
                .saturating_sub(result.attempted_candidates);
            if remaining == 0 {
                result.budget_exhausted = true;
                break;
            }
            let (repaired, attempts, truncated) = map_repair_seed_states(
                baseline,
                start,
                plain.len(),
                &seed_units,
                &seed_costs,
                budget.maximum_flips,
                attempts_per_region.min(remaining),
                budget.maximum_map_seed_states,
                budget.maximum_output_frames,
                if options.stop_after_first_frame {
                    Some(&stop_when)
                } else {
                    None
                },
            )?;
            result.attempted_candidates += attempts;
            region_attempts += attempts;
            seeds.extend(repaired);
            if truncated {
                result.budget_exhausted = true;
            }
        } else if let Some((_, baseline_map)) = &baseline_unstuffed {
            for (unit, cost) in units.iter().zip(costs.iter()) {
                if unit.len() > budget.maximum_flips {
                    continue;
                }
                let seeded_body = body_after_flips(baseline, start, plain.len(), unit);
                let Some((frame, map)) = unstuff_body_with_map(&seeded_body)? else {
                    continue;
                };
                if map == *baseline_map {
                    continue;
                }
                if seeds.len() >= budget.maximum_map_seed_states {
                    return Err("map seed state bound exceeded".into());
                }
                seeds.push((unit.clone(), *cost, frame, map));
            }
        }
        for (seed, _, frame, _) in &seeds {
            if !valid_ax25_fcs(frame)
                || (options.require_ax25_ui && !valid_ax25_ui(&frame[..frame.len() - 2]))
                || !seen.insert(frame.clone())
            {
                continue;
            }
            result.frames.push(record(
                frame.clone(),
                region,
                seed.clone(),
                values,
                threshold,
            ));
            if options.stop_after_uncorrected_frame && seed.is_empty() {
                accepted_uncorrected = true;
                break;
            }
            if options.stop_after_first_frame {
                break;
            }
            if result.frames.len() >= budget.maximum_output_frames {
                result.output_limit_reached = true;
                result.budget_exhausted = true;
                break;
            }
        }
        if result.output_limit_reached
            || accepted_uncorrected
            || (!result.frames.is_empty() && options.stop_after_first_frame)
        {
            break;
        }
        let mut streams: Vec<SyndromeStream> = vec![];
        let mut pending = BinaryHeap::new();
        for (seed, seed_cost, seed_frame, seed_map) in seeds {
            let seed_crc = crc16_x25(&seed_frame);
            let target = seed_crc ^ 0x0f47;
            let seed_body = body_after_flips(baseline, start, plain.len(), &seed);
            let trace = stuffing_trace(&seed_body)?.ok_or("seed lacks stuffing trace")?;
            let retained: HashMap<usize, usize> =
                seed_map.iter().enumerate().map(|(i, p)| (*p, i)).collect();
            let (mut compatible_units, mut compatible_costs, mut compatible_effects) =
                (vec![], vec![], vec![]);
            for (unit, cost) in units.iter().zip(costs.iter()) {
                if unit.iter().any(|i| seed.contains(i))
                    || seed.len() + unit.len() > budget.maximum_flips
                {
                    continue;
                }
                let effect = unit_body_effect(unit, start, right, plain.len());
                if !effect_preserves_stuffing_map(&seed_body, &trace, &effect)? {
                    continue;
                }
                let frame =
                    frame_after_map_preserving_effect(&seed_frame, &seed_map, &retained, &effect)?;
                compatible_units.push(unit.clone());
                compatible_costs.push(*cost);
                compatible_effects.push(seed_crc ^ crc16_x25(&frame));
            }
            let remaining = budget.maximum_flips - seed.len();
            let compatible_units = Arc::new(compatible_units);
            let compatible_costs = Arc::new(compatible_costs);
            for count in 0..=remaining {
                let mut combinations = Combinations::new(
                    compatible_units.clone(),
                    compatible_costs.clone(),
                    count,
                    remaining,
                );
                let Some((interior, selected)) = combinations.next() else {
                    continue;
                };
                let index = streams.len();
                let cost = seed_cost + python_sum(selected.iter().map(|i| compatible_costs[*i]));
                streams.push(SyndromeStream {
                    combinations,
                    costs: compatible_costs.clone(),
                    effects: compatible_effects.clone(),
                    seed: seed.clone(),
                    target,
                    seed_cost,
                    interior,
                    selected,
                });
                pending.push(QueueEntry {
                    weight: 0,
                    cost,
                    key: vec![index],
                });
            }
        }
        let mut tried = HashSet::new();
        while let Some(entry) = pending.pop() {
            let index = entry.key[0];
            let stream = &mut streams[index];
            let mut flipped = stream.seed.clone();
            flipped.extend_from_slice(&stream.interior);
            flipped.sort_unstable();
            if !tried.contains(&flipped) {
                if region_attempts >= attempts_per_region
                    || result.attempted_candidates >= budget.maximum_attempts
                {
                    result.budget_exhausted = true;
                    break;
                }
                tried.insert(flipped.clone());
                result.attempted_candidates += 1;
                region_attempts += 1;
                let effect = stream
                    .selected
                    .iter()
                    .fold(0, |value, i| value ^ stream.effects[*i]);
                if effect == stream.target {
                    let body = body_after_flips(baseline, start, plain.len(), &flipped);
                    if let Some(frame) = decode_body(&body)?
                        && (!options.require_ax25_ui || valid_ax25_ui(&frame[..frame.len() - 2]))
                        && seen.insert(frame.clone())
                    {
                        result.frames.push(record(
                            frame,
                            region,
                            flipped.clone(),
                            values,
                            threshold,
                        ));
                        if options.stop_after_uncorrected_frame && flipped.is_empty() {
                            accepted_uncorrected = true;
                            pending.clear();
                            break;
                        }
                        if options.stop_after_first_frame {
                            pending.clear();
                            break;
                        }
                        if result.frames.len() >= budget.maximum_output_frames {
                            result.output_limit_reached = true;
                            result.budget_exhausted = true;
                            pending.clear();
                            break;
                        }
                    }
                }
            }
            if let Some((interior, selected)) = stream.combinations.next() {
                let cost = stream.seed_cost + python_sum(selected.iter().map(|i| stream.costs[*i]));
                stream.interior = interior;
                stream.selected = selected;
                pending.push(QueueEntry {
                    weight: 0,
                    cost,
                    key: vec![index],
                });
            }
        }
        if result.output_limit_reached
            || accepted_uncorrected
            || (!result.frames.is_empty() && options.stop_after_first_frame)
        {
            break;
        }
    }
    Ok(result)
}

#[cfg(test)]
#[path = "tests/soft_tests.rs"]
mod tests;
