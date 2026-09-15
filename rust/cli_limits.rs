//! Fail-closed resource planning for the legacy soft-search CLI.
//!
//! This module does not shorten a search, change ranking, or certify telemetry.
//! Bounds are intentionally conservative algorithm/allocation models, not an
//! operating-system RSS or wall-clock guarantee. In particular, the caller
//! must still validate emitted frames and enforce its final output contract.

use crate::soft::{SoftDecodeOptions, SoftListBudget};
use serde_json::{Value, json};

const MIB: u128 = 1024 * 1024;
const MEMORY_LIMIT: u128 = 512 * MIB;
const WORK_LIMIT: u128 = 100_000_000_000;
const COUNT_CEILING: u128 = 1 << 100;
const MAXIMUM_BODY_BITS: usize = 9821;

fn add(a: u128, b: u128) -> u128 {
    a.saturating_add(b).min(COUNT_CEILING)
}

fn mul(a: u128, b: u128) -> u128 {
    a.saturating_mul(b).min(COUNT_CEILING)
}

/// Saturating upper bounds for subsets, subset/seed incidences and predecessor
/// edges. Saturation is upward and vastly above any accepted resource budget.
fn subset_counts(n: usize, flips: usize, pairs: bool) -> (u128, u128, u128) {
    let mut choose = 1u128;
    let (mut subsets, mut incidences, mut edges) = (1, 1, 0);
    for k in 1..=flips.min(n) {
        let factor = (n - k + 1) as u128;
        choose = if choose > COUNT_CEILING / factor {
            COUNT_CEILING
        } else {
            choose * factor / k as u128
        };
        subsets = add(subsets, choose);
        incidences = add(incidences, mul(choose, 1u128 << k));
        let incoming = if pairs { k + k * (k - 1) / 2 } else { k };
        edges = add(edges, mul(choose, incoming as u128));
    }
    (subsets, incidences, edges)
}

fn count_json(value: u128) -> Value {
    if let Ok(value) = u64::try_from(value) {
        json!(value)
    } else {
        json!(value.to_string())
    }
}

/// Validate before calling either legacy soft decoder. No configuration is
/// silently modified. The returned policy must accompany successful output.
pub fn validate_soft(
    symbol_count: usize,
    budget: &SoftListBudget,
    options: &SoftDecodeOptions,
    method: &str,
) -> Result<Value, String> {
    budget.validate()?;
    if !matches!(method, "list" | "syndrome") {
        return Err("unknown soft-search method".into());
    }
    if !(1..=1_000_000).contains(&symbol_count)
        || budget.maximum_flips > 64
        || budget.least_reliable_symbols > 65_536
        || budget.maximum_attempts > 10_000_000
        || budget
            .maximum_attempts_per_region
            .is_some_and(|n| n > 10_000_000)
        || budget.maximum_regions > 10_000
        || budget.maximum_boundary_patterns > 262_144
        || budget.maximum_map_seed_states > 100_000
        || budget.localization_radius_bits > 1_000_000
        || options.additional_candidate_indices.len() > 65_536
        || !budget.error_unit_penalty.is_finite()
    {
        return Err(
            "soft CLI scalar resource bound exceeded (including maximum_flips <= 64)".into(),
        );
    }
    if !options.require_ax25_ui {
        return Err(
            "soft CLI requires AX.25 UI structure; caller must verify emitted candidates".into(),
        );
    }
    if options
        .event_start_symbol
        .is_some_and(|n| !n.is_finite() || n < 0.0 || n > isize::MAX as f64)
        || options
            .additional_candidate_indices
            .iter()
            .any(|n| *n >= symbol_count)
    {
        return Err("invalid soft CLI event position or additional candidate index".into());
    }
    if method == "list"
        && (!options.descramble
            || options.stop_after_first_frame
            || options.stop_after_uncorrected_frame
            || budget.maximum_attempts_per_region.is_some())
    {
        return Err("legacy list mode ignores descramble=false, stop flags and per-region limits; use syndrome or omit those options".into());
    }

    let body = symbol_count.min(MAXIMUM_BODY_BITS) as u128;
    // Candidate centres and explicit indices are deduplicated by the decoder.
    // Include the right boundary position for additional indices. Neighbours
    // add at most two indices per radius per centre, and at most that many pair
    // units. The same upper bound therefore covers distinct raw positions.
    let centres = budget.least_reliable_symbols.min(body as usize + 1);
    let extra = options
        .additional_candidate_indices
        .len()
        .min(body as usize + 1);
    let raw_positions =
        (centres * (1 + 2 * budget.candidate_neighbor_radius) + extra).min(body as usize + 1);
    let units = centres * (1 + 2 * budget.candidate_neighbor_radius) + extra;
    let flips = budget.maximum_flips;
    let pairs = budget.candidate_neighbor_radius != 0;
    let (raw_subsets, seed_incidences, incoming_edges) = subset_counts(raw_positions, flips, pairs);
    let (unit_subsets, _, _) = subset_counts(units, flips, pairs);
    let attempts = budget.maximum_attempts as u128;
    let regions = budget.maximum_regions as u128;
    let seeds = if method == "syndrome" {
        // Map-repair can retain additional valid frames beyond its map-state
        // cap, up to maximum_output_frames. Include the baseline seed too.
        add(
            budget.maximum_map_seed_states as u128,
            budget.maximum_output_frames as u128,
        )
        .min(raw_subsets)
    } else {
        0
    };
    let boundaries = if method == "list" {
        budget.maximum_boundary_patterns as u128
    } else {
        0
    };
    let stream_owners = if method == "list" { boundaries } else { seeds };
    let enumerates_boundaries = method == "list"
        && (budget.maximum_boundary_patterns > 1
            || (budget.maximum_left_flag_hamming > 0 && options.event_start_symbol.is_some()));
    let streams = mul(stream_owners, (flips.min(units) + 1) as u128);
    let full_stream_states = if method == "syndrome" && !pairs {
        // Each final singleton flip set T appears at most once per seed S⊆T.
        // This accounts for duplicate candidates across seed streams, which
        // are not charged again to the public candidate-attempt counter.
        mul(unit_subsets, seeds).min(seed_incidences)
    } else {
        mul(unit_subsets, stream_owners)
    };
    let stream_states = if pairs {
        // next() may visit all invalid overlapping/overweight unit subsets
        // before producing even its first externally counted candidate.
        full_stream_states
    } else {
        let duplicate_factor = if method == "syndrome" { seeds } else { 1 };
        let calls = add(streams, mul(add(attempts, 1), duplicate_factor));
        full_stream_states.min(add(streams, mul(calls, flips as u128)))
    };

    // Keys are retained separately by heap and hash table. Factors include
    // geometric capacities, allocation metadata and an intentionally generous
    // allowance for temporary vectors. List boundaries can add 18 raw flips.
    let record_flips = flips + usize::from(method == "list") * 18;
    let state_bytes = 256 + 32 * record_flips as u128;
    let stream_memory = mul(stream_states, state_bytes);
    let stream_headers = mul(streams, 512 + 64 * flips as u128 + 4 * units as u128);
    // Ranking enumerates 2^18 states even if only one or two patterns will be
    // retained. Include the temporary best-map and ranking vectors, not just
    // the requested number of boundary patterns.
    let boundary_memory = if enumerates_boundaries {
        262_144 * 512
    } else {
        0
    };
    let seed_units = mul(seeds, 192 * units as u128);
    let seed_maps = mul(
        seeds,
        16 * body + 2 * (body / 8).min(1023) + 512 + 32 * flips as u128,
    );

    // The map-search heap can contain stale improved-cost entries. Bound its
    // insertions by predecessor edges as well as attempts × available units;
    // counting only distinct map keys would miss those queue allocations.
    let map_heap_entries = if method == "syndrome" {
        add(1, incoming_edges.min(mul(attempts, units as u128)))
    } else {
        0
    };
    let map_keys = if method == "syndrome" {
        raw_subsets.min(add(1, mul(attempts, units as u128)))
    } else {
        0
    };
    let map_memory = mul(add(map_heap_entries, map_keys), state_bytes);
    let tried_memory = mul(
        attempts.min(mul(raw_subsets, boundaries.max(1))),
        state_bytes,
    );
    let frame_bytes = (body / 8).min(1023);
    let decoded_outputs = if method == "list" {
        // Legacy list ignores maximum_output_frames until the caller checks
        // the completed result, so all candidate attempts could be retained.
        attempts
    } else {
        budget.maximum_output_frames as u128
    };
    let output_memory = mul(
        decoded_outputs,
        2 * frame_bytes + 512 + 32 * record_flips as u128,
    );
    let json_memory = mul(
        budget.maximum_output_frames as u128,
        64 * frame_bytes + 2048 + 128 * record_flips as u128,
    );
    let base_memory = 16 * MIB
        + 64 * symbol_count as u128
        + 128 * regions
        + 32 * options.additional_candidate_indices.len() as u128;
    let memory = [
        base_memory,
        stream_memory,
        stream_headers,
        boundary_memory,
        seed_units,
        seed_maps,
        map_memory,
        tried_memory,
        output_memory,
        json_memory,
    ]
    .into_iter()
    .fold(0, add);

    // A disclosed work proxy, not seconds. Account for uncharged preparation
    // on every selected region, including all boundary states when requested.
    let boundary_work = if enumerates_boundaries {
        262_144u128 * 18
    } else {
        0
    };
    let per_region_work = add(
        mul(full_stream_states.min(stream_states), flips as u128 + 1),
        add(
            mul(seeds, mul(units as u128, body)),
            add(
                mul(map_keys.min(attempts), mul(units as u128, body)),
                boundary_work,
            ),
        ),
    );
    let work = add(
        mul(regions, per_region_work),
        add(
            mul(attempts, body),
            mul(regions, options.additional_candidate_indices.len() as u128),
        ),
    );
    if memory > MEMORY_LIMIT {
        return Err(format!(
            "soft CLI conservative working-set estimate {memory} bytes exceeds 512 MiB; reduce explicit input/search configuration (no pruning performed)"
        ));
    }
    if work > WORK_LIMIT {
        return Err(format!(
            "soft CLI preparation/search work estimate {work} exceeds {WORK_LIMIT}; reduce explicit search configuration (no pruning performed)"
        ));
    }
    Ok(json!({
        "schema": "rust-soft-cli-resource-policy-v1",
        "method": method,
        "configuration_modified": false,
        "search_pruning": false,
        "working_set_estimate_bytes": count_json(memory),
        "working_set_limit_bytes": count_json(MEMORY_LIMIT),
        "work_units_estimate": count_json(work),
        "work_units_limit": count_json(WORK_LIMIT),
        "maximum_body_bits_model": body,
        "candidate_units_upper_bound": units,
        "raw_candidate_positions_upper_bound": raw_positions,
        "seed_states_upper_bound": count_json(seeds),
        "boundary_patterns_upper_bound": count_json(boundaries),
        "boundary_enumeration_states_per_region": if enumerates_boundaries { 262_144 } else { 0 },
        "boundary_enumeration_memory_bytes": count_json(boundary_memory),
        "initialized_streams_upper_bound": count_json(streams),
        "retained_combination_states_upper_bound": count_json(stream_states),
        "full_combination_states_upper_bound": count_json(full_stream_states),
        "invalid_combination_expansion_included": pairs,
        "duplicate_seed_streams_included": method == "syndrome",
        "retained_decoded_outputs_upper_bound": count_json(decoded_outputs),
        "caller_must_check_output_count": true,
        "caller_must_validate_crc_and_ax25_ui": true,
        "native_trusted": false,
        "limits_are_os_enforced": false,
        "scope": "conservative decoder and output-conversion allocation/work model; excludes caller stdin JSON parsing, allocator/runtime RSS and OS wall time",
        "region_selection_requirement": "exact ordered bounded selection; maximum_regions must not be applied after unbounded pair materialization"
    }))
}

#[cfg(test)]
#[path = "tests/cli_limits_tests.rs"]
mod tests;
