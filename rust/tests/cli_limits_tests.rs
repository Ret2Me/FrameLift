use super::*;

#[test]
fn modest_default_frame_is_accepted_by_both_methods() {
    for method in ["list", "syndrome"] {
        let policy = validate_soft(
            4096,
            &SoftListBudget::default(),
            &SoftDecodeOptions::default(),
            method,
        )
        .unwrap();
        assert!(policy["working_set_estimate_bytes"].as_u64().unwrap() <= 512 * 1024 * 1024);
        assert_eq!(policy["configuration_modified"], false);
        assert_eq!(policy["native_trusted"], false);
        assert_eq!(policy["caller_must_validate_crc_and_ax25_ui"], true);
    }
}

#[test]
fn unlimited_flips_are_rejected_even_for_one_attempt() {
    let budget = SoftListBudget {
        maximum_flips: usize::MAX,
        maximum_attempts: 1,
        ..Default::default()
    };
    for method in ["list", "syndrome"] {
        assert!(
            validate_soft(1024, &budget, &SoftDecodeOptions::default(), method)
                .unwrap_err()
                .contains("maximum_flips")
        );
    }
}

#[test]
fn ignored_list_options_are_rejected_not_silently_changed() {
    let defaults = SoftDecodeOptions::default();
    for options in [
        SoftDecodeOptions {
            descramble: false,
            ..defaults.clone()
        },
        SoftDecodeOptions {
            stop_after_first_frame: true,
            ..defaults.clone()
        },
        SoftDecodeOptions {
            stop_after_uncorrected_frame: true,
            ..defaults.clone()
        },
    ] {
        assert!(
            validate_soft(1024, &SoftListBudget::default(), &options, "list")
                .unwrap_err()
                .contains("ignores")
        );
        assert!(validate_soft(1024, &SoftListBudget::default(), &options, "syndrome").is_ok());
    }
    let budget = SoftListBudget {
        maximum_attempts_per_region: Some(1),
        ..Default::default()
    };
    assert!(validate_soft(1024, &budget, &defaults, "list").is_err());
    assert!(validate_soft(1024, &budget, &defaults, "syndrome").is_ok());
}

#[test]
fn unrestricted_structure_is_rejected() {
    let options = SoftDecodeOptions {
        require_ax25_ui: false,
        ..Default::default()
    };
    assert!(validate_soft(1024, &SoftListBudget::default(), &options, "list").is_err());
}

#[test]
fn neighbor_combinations_cannot_hide_behind_one_attempt() {
    let budget = SoftListBudget {
        least_reliable_symbols: 64,
        maximum_flips: 16,
        maximum_attempts: 1,
        maximum_map_seed_states: 1,
        maximum_output_frames: 1,
        candidate_neighbor_radius: 2,
        ..Default::default()
    };
    for method in ["list", "syndrome"] {
        assert!(
            validate_soft(4096, &budget, &SoftDecodeOptions::default(), method)
                .unwrap_err()
                .contains("working-set")
        );
    }
}

#[test]
fn small_neighbor_search_discloses_full_internal_enumeration() {
    let budget = SoftListBudget {
        least_reliable_symbols: 2,
        maximum_flips: 2,
        maximum_attempts: 1,
        maximum_map_seed_states: 2,
        maximum_output_frames: 2,
        candidate_neighbor_radius: 1,
        ..Default::default()
    };
    let policy = validate_soft(1024, &budget, &SoftDecodeOptions::default(), "syndrome").unwrap();
    assert_eq!(
        policy["retained_combination_states_upper_bound"],
        policy["full_combination_states_upper_bound"]
    );
    assert!(
        policy["retained_combination_states_upper_bound"]
            .as_u64()
            .unwrap()
            > 1
    );
    assert_eq!(policy["invalid_combination_expansion_included"], true);
}

#[test]
fn all_boundary_initializations_are_budgeted_before_first_attempt() {
    let budget = SoftListBudget {
        maximum_boundary_patterns: 262_144,
        maximum_flips: 64,
        maximum_attempts: 1,
        ..Default::default()
    };
    assert!(validate_soft(4096, &budget, &SoftDecodeOptions::default(), "list").is_err());
}

#[test]
fn approximate_left_flag_budgets_full_boundary_ranking_even_for_one_pattern() {
    let budget = SoftListBudget {
        maximum_left_flag_hamming: 1,
        maximum_boundary_patterns: 1,
        maximum_attempts: 10,
        ..Default::default()
    };
    let options = SoftDecodeOptions {
        event_start_symbol: Some(100.0),
        ..Default::default()
    };
    let policy = validate_soft(1024, &budget, &options, "list").unwrap();
    assert_eq!(policy["boundary_enumeration_states_per_region"], 262_144);
    assert_eq!(
        policy["boundary_enumeration_memory_bytes"],
        128 * 1024 * 1024
    );
}

#[test]
fn list_retention_uses_attempts_not_ignored_output_cap() {
    let budget = SoftListBudget {
        maximum_attempts: 1000,
        maximum_output_frames: 1,
        ..Default::default()
    };
    let policy = validate_soft(1024, &budget, &SoftDecodeOptions::default(), "list").unwrap();
    assert_eq!(policy["retained_decoded_outputs_upper_bound"], 1000);
}

#[test]
fn singleton_seed_incidence_counts_include_duplicate_streams() {
    assert_eq!(subset_counts(3, 2, false), (7, 19, 9));
    let budget = SoftListBudget {
        least_reliable_symbols: 3,
        maximum_flips: 2,
        maximum_attempts: 100,
        maximum_map_seed_states: 6,
        maximum_output_frames: 1,
        ..Default::default()
    };
    let policy = validate_soft(1024, &budget, &SoftDecodeOptions::default(), "syndrome").unwrap();
    assert_eq!(policy["full_combination_states_upper_bound"], 19);
    assert_eq!(policy["seed_states_upper_bound"], 7);
}

#[test]
fn combinatorial_arithmetic_saturates_upward_without_overflow() {
    let (subsets, incidences, edges) = subset_counts(1_000_000, 64, true);
    assert_eq!(subsets, COUNT_CEILING);
    assert_eq!(incidences, COUNT_CEILING);
    assert_eq!(edges, COUNT_CEILING);
}

#[test]
fn invalid_positions_scalars_and_methods_fail_closed() {
    let budget = SoftListBudget::default();
    let defaults = SoftDecodeOptions::default();
    assert!(validate_soft(0, &budget, &defaults, "syndrome").is_err());
    assert!(validate_soft(1_000_001, &budget, &defaults, "syndrome").is_err());
    assert!(validate_soft(1024, &budget, &defaults, "unknown").is_err());
    for position in [f64::NAN, f64::INFINITY, -1.0] {
        let options = SoftDecodeOptions {
            event_start_symbol: Some(position),
            ..defaults.clone()
        };
        assert!(validate_soft(1024, &budget, &options, "syndrome").is_err());
    }
    let options = SoftDecodeOptions {
        additional_candidate_indices: vec![1024],
        ..defaults
    };
    assert!(validate_soft(1024, &budget, &options, "syndrome").is_err());
}
