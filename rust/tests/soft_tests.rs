use super::*;

#[test]
fn compensated_python_cost_sum_and_lazy_combination_order_match() {
    assert_eq!(python_sum([0.1, 0.2, 0.3]), 0.6);
    assert_eq!(python_sum([f64::INFINITY, 1.0]), f64::INFINITY);
    let document: serde_json::Value =
        serde_json::from_str(include_str!("soft_cost_oracle.json")).unwrap();
    for (index, case) in document["cases"].as_array().unwrap().iter().enumerate() {
        let costs: Vec<f64> = serde_json::from_value(case["costs"].clone()).unwrap();
        assert_eq!(
            python_sum(costs.iter().copied()).to_bits(),
            case["sum"].as_f64().unwrap().to_bits()
        );
        let count = case["count"].as_u64().unwrap() as usize;
        let units = Arc::new((0..costs.len()).map(|i| vec![i]).collect());
        let mut combinations = Combinations::new(units, Arc::new(costs), count, count);
        let mut ordered = vec![];
        while let Some((_, selected)) = combinations.next() {
            ordered.push(selected);
        }
        assert_eq!(
            serde_json::to_value(ordered).unwrap(),
            case["ordered"],
            "cost case {index}"
        );
    }
}

#[test]
fn frozen_python_soft_search_full_results_and_errors_match() {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("soft_oracle.json")).unwrap();
    assert_eq!(fixture["legacy_tests_passed"], 16);
    for (index, case) in fixture["cases"].as_array().unwrap().iter().enumerate() {
        let signs = hex::decode(case["soft_sign_hex"].as_str().unwrap()).unwrap();
        let magnitude = case["soft_abs_default"].as_f64().unwrap();
        let mut values: Vec<f64> = (0..case["soft_len"].as_u64().unwrap() as usize)
            .map(|i| {
                if signs[i / 8] & (1 << (7 - i % 8)) != 0 {
                    -magnitude
                } else {
                    magnitude
                }
            })
            .collect();
        for item in case["soft_overrides"].as_array().unwrap() {
            values[item[0].as_u64().unwrap() as usize] = item[1].as_f64().unwrap();
        }
        let threshold = case["threshold"].as_f64().unwrap();
        let budget: SoftListBudget = serde_json::from_value(case["budget"].clone()).unwrap();
        let options: SoftDecodeOptions = serde_json::from_value(case["options"].clone()).unwrap();
        let result = if case["kind"] == "list" {
            protocol_constrained_list_decode(&values, threshold, &budget, &options)
        } else {
            protocol_constrained_syndrome_decode(&values, threshold, &budget, &options)
        };
        if let Some(expected) = case.get("error") {
            assert_eq!(
                result.unwrap_err(),
                expected.as_str().unwrap(),
                "case {index}"
            );
        } else {
            let result = result.unwrap_or_else(|e| panic!("case {index}: {e}"));
            let expected: SoftListResult = serde_json::from_value(case["result"].clone()).unwrap();
            assert_eq!(result, expected, "case {index} kind={}", case["kind"]);
        }
    }
}

#[test]
fn impulse_and_preamble_validation() {
    assert_eq!(raw_level_flip_effects(1, 20), vec![1, 2, 13, 14, 18, 19]);
    assert_eq!(raw_level_flip_effects(usize::MAX, 20), Vec::<usize>::new());
    assert_eq!(
        apply_raw_level_flips(&[0; 20], &[1, 1]).unwrap(),
        vec![0; 20]
    );
    assert!(apply_raw_level_flips(&[2], &[]).is_err());
    assert!(exact_flag_offsets(&[2]).is_err());
    let mut bits = FLAG.repeat(5);
    bits.extend([0; 160]);
    bits.extend(FLAG);
    assert_eq!(
        preamble_terminated_frame_starts(&bits, 4, 128).unwrap(),
        vec![32]
    );
    assert!(preamble_terminated_frame_starts(&bits, 1, 128).is_err());
}

#[test]
fn budgets_fail_closed_and_corrected_candidates_never_gain_trust() {
    let default = SoftListBudget::default();
    assert!(default.validate().is_ok());
    for key in [
        "least_reliable_symbols",
        "maximum_regions",
        "maximum_attempts",
        "maximum_boundary_patterns",
        "maximum_map_seed_states",
        "maximum_output_frames",
    ] {
        let mut value = serde_json::to_value(&default).unwrap();
        value[key] = 0.into();
        let budget: SoftListBudget = serde_json::from_value(value).unwrap();
        assert!(budget.validate().is_err(), "{key}");
    }
    let invalid = SoftListBudget {
        error_unit_penalty: f64::NAN,
        ..default.clone()
    };
    assert!(invalid.validate().is_err());
    let invalid = SoftListBudget {
        maximum_output_frames: 4097,
        ..default.clone()
    };
    assert!(invalid.validate().is_err());
    let mut frame = record(vec![0, 0], (0, 8, 0), vec![3], &[0.0; 4], 0.0);
    assert!(frame.is_repaired_candidate());
    frame.flipped_symbol_indices.clear();
    assert!(!frame.is_repaired_candidate());
    assert!(
        protocol_constrained_list_decode(&[], 0.0, &default, &SoftDecodeOptions::default())
            .is_err()
    );
    assert!(
        protocol_constrained_syndrome_decode(
            &[0.0],
            f64::NAN,
            &default,
            &SoftDecodeOptions::default()
        )
        .is_err()
    );
}

#[test]
fn sparse_map_classifier_matches_complete_reparse_for_all_single_and_pair_units() {
    let payload = hex::decode("94a662b2a0826094a662b29eb2e103f00018ad8001020304").unwrap();
    let mut frame = payload.clone();
    frame.extend(crc16_x25(&payload).to_le_bytes());
    let mut body = vec![];
    let mut ones = 0;
    for byte in &frame {
        for bit in 0..8 {
            let value = (byte >> bit) & 1;
            body.push(value);
            ones = if value == 1 { ones + 1 } else { 0 };
            if ones == 5 {
                body.push(0);
                ones = 0;
            }
        }
    }
    let baseline = unstuff_body_with_map(&body).unwrap().unwrap();
    let trace = stuffing_trace(&body).unwrap().unwrap();
    let retained: HashMap<usize, usize> = baseline
        .1
        .iter()
        .enumerate()
        .map(|(i, p)| (*p, i))
        .collect();
    for i in 24..24 + body.len() {
        for offset in 0..3 {
            let unit = if offset == 0 {
                vec![i]
            } else {
                vec![i, i + offset]
            };
            let effect = unit_body_effect(&unit, 24, 24 + body.len(), 40 + body.len());
            let modified = body_after_flips(&body, 24, 40 + body.len(), &unit);
            let reparsed = unstuff_body_with_map(&modified).unwrap();
            let expected = reparsed.as_ref().is_some_and(|(_, map)| map == &baseline.1);
            let actual = effect_preserves_stuffing_map(&body, &trace, &effect).unwrap();
            assert_eq!(actual, expected, "unit={unit:?}");
            if actual {
                assert_eq!(
                    frame_after_map_preserving_effect(&frame, &baseline.1, &retained, &effect)
                        .unwrap(),
                    reparsed.unwrap().0
                );
            }
        }
    }
}

// Frozen pre-optimization region enumeration, deliberately quadratic and used
// only on small fixtures as an independent ordering/count oracle.
fn brute_force_eligible_regions(
    plain: &[u8],
    options: &SoftDecodeOptions,
    budget: &SoftListBudget,
) -> Result<Vec<Region>, String> {
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
    let mut regions = vec![];
    for (left, distance) in lefts {
        for right in &flags {
            if *right < left + 8 + 128 {
                continue;
            }
            if right - left - 8 > maximum {
                break;
            }
            regions.push((left, *right, distance));
        }
    }
    if let Some(event) = options.event_start_symbol {
        regions.sort_by(|a, b| {
            fcmp((a.0 as f64 - event).abs(), (b.0 as f64 - event).abs())
                .then(a.2.cmp(&b.2))
                .then(a.0.cmp(&b.0))
                .then(a.1.cmp(&b.1))
        });
    }
    Ok(regions)
}

fn compare_bounded_regions(plain: &[u8], options: &SoftDecodeOptions, budget: &SoftListBudget) {
    let expected = brute_force_eligible_regions(plain, options, budget);
    let actual = eligible_regions(plain, options, budget);
    match (actual, expected) {
        (Ok((total, selected)), Ok(all)) => {
            assert_eq!(
                total,
                all.len(),
                "count: options={options:?}, budget={budget:?}"
            );
            assert_eq!(
                selected,
                all.into_iter()
                    .take(budget.maximum_regions)
                    .collect::<Vec<_>>(),
                "order: options={options:?}, budget={budget:?}"
            );
        }
        (Err(actual), Err(expected)) => assert_eq!(actual, expected),
        (actual, expected) => panic!("outcome mismatch: actual={actual:?}, expected={expected:?}"),
    }
}

#[test]
fn bounded_region_selection_matches_exhaustive_small_flag_layouts() {
    let positions = [0, 8, 128, 136, 264, 400];
    for mask in 0..(1 << positions.len()) {
        let mut plain = vec![0; 512];
        for (bit, position) in positions.iter().enumerate() {
            if mask & (1 << bit) != 0 {
                plain[*position..*position + 8].copy_from_slice(&FLAG);
            }
        }
        for event in [
            None,
            Some(0.0),
            Some(132.0),
            Some(132.5),
            Some(f64::NAN),
            Some(f64::INFINITY),
        ] {
            for hamming in 0..=3 {
                for limit in [0, 1, 2, 7, usize::MAX] {
                    let options = SoftDecodeOptions {
                        event_start_symbol: event,
                        ..Default::default()
                    };
                    let budget = SoftListBudget {
                        maximum_regions: limit,
                        maximum_left_flag_hamming: hamming,
                        localization_radius_bits: 144,
                        ..Default::default()
                    };
                    compare_bounded_regions(&plain, &options, &budget);
                }
            }
        }
    }
}

#[test]
fn bounded_region_selection_matches_seeded_random_and_exact_body_limits() {
    let mut state = 41_u64;
    for case in 0..256 {
        let length = 320 + case * 7;
        let mut plain: Vec<_> = (0..length)
            .map(|_| {
                state = state
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                ((state >> 47) & 1) as u8
            })
            .collect();
        for position in [0, 136, length - 8] {
            plain[position..position + 8].copy_from_slice(&FLAG);
        }
        let event = match case % 5 {
            0 => None,
            1 => Some(length as f64 / 2.0),
            2 => Some(131.5),
            3 => Some(1e20),
            _ => Some((length + 128) as f64),
        };
        let options = SoftDecodeOptions {
            event_start_symbol: event,
            ..Default::default()
        };
        let budget = SoftListBudget {
            maximum_regions: case % 17,
            maximum_left_flag_hamming: case % 4,
            localization_radius_bits: case * 3,
            ..Default::default()
        };
        compare_bounded_regions(&plain, &options, &budget);
    }
    let maximum = (1024usize - 1) * 8 + ((1024usize - 1) * 8).div_ceil(5);
    for body in [127, 128, maximum, maximum + 1] {
        let mut plain = vec![0; body + 16];
        plain[..8].copy_from_slice(&FLAG);
        plain[body + 8..].copy_from_slice(&FLAG);
        let (total, selected) = eligible_regions(
            &plain,
            &SoftDecodeOptions::default(),
            &SoftListBudget::default(),
        )
        .unwrap();
        assert_eq!(total, usize::from((128..=maximum).contains(&body)));
        assert_eq!(selected.len(), total);
        compare_bounded_regions(
            &plain,
            &SoftDecodeOptions::default(),
            &SoftListBudget::default(),
        );
    }
    compare_bounded_regions(
        &[2],
        &SoftDecodeOptions::default(),
        &SoftListBudget::default(),
    );
}

#[test]
fn million_sample_flag_dense_selection_counts_all_pairs_but_retains_only_budget() {
    let flags = 125_000;
    let plain = FLAG.repeat(flags);
    let budget = SoftListBudget {
        maximum_regions: 4,
        ..Default::default()
    };
    let (total, selected) =
        eligible_regions(&plain, &SoftDecodeOptions::default(), &budget).unwrap();
    // Flags are eight samples apart: allowed separations are exactly 17..1228.
    let expected: usize = (17..=1228).map(|distance| flags - distance).sum();
    assert_eq!(expected, 150_745_530);
    assert_eq!(total, expected);
    assert_eq!(
        selected,
        vec![(0, 136, 0), (0, 144, 0), (0, 152, 0), (0, 160, 0)]
    );
    assert!(selected.capacity() <= 4);
    let options = SoftDecodeOptions {
        event_start_symbol: Some(500_000.0),
        ..Default::default()
    };
    let (event_total, event_selected) = eligible_regions(&plain, &options, &budget).unwrap();
    assert_eq!(event_total, expected);
    assert_eq!(
        event_selected,
        vec![
            (500000, 500136, 0),
            (500000, 500144, 0),
            (500000, 500152, 0),
            (500000, 500160, 0)
        ]
    );
    let mut level = 0;
    let values: Vec<_> = plain
        .iter()
        .map(|bit| {
            if *bit == 0 {
                level ^= 1;
            }
            if level == 1 { 1.0 } else { -1.0 }
        })
        .collect();
    let (recovered, prepared, metadata) =
        prepare(&values, 0.0, &budget, &SoftDecodeOptions::default(), false).unwrap();
    assert_eq!(recovered, plain);
    assert_eq!(prepared, selected);
    assert_eq!(metadata.exact_flag_count, flags);
    assert_eq!(metadata.eligible_regions, expected);
    assert_eq!(metadata.searched_regions, budget.maximum_regions);
}
