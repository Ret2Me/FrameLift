//! Experimental, guarded pooling of codec calibration across native anchors.
//!
//! Pooling changes evidence volume, not the fixed `fit_variance` acceptance
//! thresholds. Valid sources are ordered by geometry only, deterministically. The caller
//! must derive residuals from native received-CRC anchors with independently
//! trained signal/noise models; this module never decodes or repairs a frame.
use crate::codec_reliability::{self, CalibrationBlock, CodecMetadata, VarianceFit};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

const MAX_SOURCES: usize = 4096;
const MAX_BLOCKS: usize = 32;
const MAX_SAMPLES: usize = 131_072;
const MIN_TRAINING_RMS: f64 = 1e-7;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolSource {
    pub id: String,
    pub source_sha256: String,
    pub frontend: String,
    pub window_start_sample: usize,
    pub window_end_sample: usize,
    /// Full received frame hashes, including the received FCS. Hash equality
    /// conservatively rejects retransmissions too: it cannot prove that two
    /// copies with identical bytes were independent physical evidence.
    pub received_frame_sha256: Vec<String>,
    pub training: Vec<CalibrationBlock>,
    pub heldout: Vec<CalibrationBlock>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolBlockEvidence {
    pub original_id: String,
    pub pooled_id: String,
    pub first_absolute_sample: f64,
    pub last_absolute_sample: f64,
    pub samples: usize,
    pub original_residual_sha256: String,
    pub normalized_residual_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolSelection {
    pub id: String,
    pub window_start_sample: usize,
    pub window_end_sample: usize,
    pub start_distance_samples: usize,
    pub received_frame_sha256: Vec<String>,
    /// Computed on this source's training partition only and also applied,
    /// without adjustment, to its heldout partition.
    pub training_rms: f64,
    pub training_blocks: Vec<PoolBlockEvidence>,
    pub heldout_blocks: Vec<PoolBlockEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolRejection {
    pub id: String,
    pub window_start_sample: usize,
    pub window_end_sample: usize,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolFit {
    pub schema: String,
    pub source_sha256: String,
    pub frontend: String,
    pub target_bounds: (usize, usize),
    pub sample_rate: u32,
    pub selected_sources: Vec<PoolSelection>,
    pub rejected_sources: Vec<PoolRejection>,
    pub distinct_received_frames: usize,
    pub training_samples: usize,
    pub heldout_samples: usize,
    /// Stable machine-readable outcome, independent of explanatory prose.
    pub status: String,
    pub reason: String,
    /// None means insufficient eligible pooled evidence; rejected statistical
    /// fits remain Some with accepted=false and never expose a usable model.
    pub variance_fit: Option<VarianceFit>,
}

/// Fit once using at least two nonoverlapping same-frontend source windows and
/// two distinct received-frame hashes. Sources must be >=1s away from the whole
/// target window and start within 60s of its start, matching adaptive guards.
/// No alternative subsets are tried after seeing heldout likelihood or CRC.
pub fn fit_pool(
    metadata: &CodecMetadata,
    sources: &[PoolSource],
    frontend: &str,
    target_bounds: (usize, usize),
    sample_rate: u32,
) -> Result<PoolFit, String> {
    let prepared = prepare_pool(metadata, sources, frontend, target_bounds, sample_rate)?;
    let mut result = prepared.report;
    if result.selected_sources.len() < 2 || result.distinct_received_frames < 2 {
        result.status = "insufficient_sources".into();
        result.reason =
            "need two disjoint source windows with distinct native received frames".into();
        return Ok(result);
    }
    if result.training_samples < 128 || result.heldout_samples < 128 {
        result.status = "insufficient_samples".into();
        result.reason = "pooled partitions still need at least 128 samples each".into();
        return Ok(result);
    }
    let variance_fit =
        codec_reliability::fit_variance(metadata, &prepared.training, &prepared.heldout)?;
    result.reason = variance_fit.reason.clone();
    result.status = if variance_fit.accepted {
        "gate_accepted"
    } else {
        "gate_rejected"
    }
    .into();
    result.variance_fit = Some(variance_fit);
    Ok(result)
}

struct PreparedPool {
    report: PoolFit,
    training: Vec<CalibrationBlock>,
    heldout: Vec<CalibrationBlock>,
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

fn source_geometry(
    source: &PoolSource,
    metadata: &CodecMetadata,
    frontend: &str,
    target: (usize, usize),
    rate: usize,
) -> Result<(), String> {
    if source.id.is_empty() || source.id.len() > 512 {
        return Err("empty or excessive source ID".into());
    }
    if source.source_sha256 != metadata.source.sha256 {
        return Err("source identity does not match original codec recording".into());
    }
    if source.frontend != frontend {
        return Err("different frontend".into());
    }
    let (start, end) = (source.window_start_sample, source.window_end_sample);
    if start >= end || end > metadata.decoded_samples {
        return Err("invalid source window geometry".into());
    }
    if start.abs_diff(target.0) > rate * 60 {
        return Err("source window start is farther than 60 seconds".into());
    }
    if !(end.checked_add(rate).is_some_and(|n| n <= target.0)
        || target.1.checked_add(rate).is_some_and(|n| n <= start))
    {
        return Err("source window overlaps target or its one-second guard".into());
    }
    if source.received_frame_sha256.is_empty()
        || source.received_frame_sha256.len() > MAX_BLOCKS
        || source
            .received_frame_sha256
            .iter()
            .any(|s| !valid_sha256(s))
    {
        return Err("missing, invalid or excessive native received-frame hashes".into());
    }
    Ok(())
}

fn validate_source_blocks(
    source: &PoolSource,
    metadata: &CodecMetadata,
) -> Result<(usize, usize, f64), String> {
    if source.training.is_empty()
        || source.heldout.is_empty()
        || source.training.len() + source.heldout.len() > MAX_BLOCKS
    {
        return Err("need separate nonempty source partitions and at most 32 blocks".into());
    }
    let mut ids = BTreeSet::new();
    let mut intervals = Vec::new();
    let mut counts = [0usize; 2];
    for (partition, blocks) in [&source.training, &source.heldout].into_iter().enumerate() {
        for block in blocks {
            if block.id.is_empty()
                || block.id.len() > 1024
                || !ids.insert(&block.id)
                || block.absolute_samples.len() != block.residuals.len()
                || !(32..=MAX_SAMPLES).contains(&block.residuals.len())
            {
                return Err("invalid, duplicate or undersized source calibration block".into());
            }
            counts[partition] += block.residuals.len();
            if counts[partition] > MAX_SAMPLES {
                return Err("source calibration partition exceeds sample limit".into());
            }
            let mut previous = None;
            for (&position, &residual) in block.absolute_samples.iter().zip(&block.residuals) {
                if !position.is_finite()
                    || position < source.window_start_sample as f64
                    || position >= source.window_end_sample as f64
                    || previous.is_some_and(|old| position <= old)
                    || !residual.is_finite()
                    || residual.abs() > 1e100
                {
                    return Err(
                        "invalid residual or sample coordinate outside source window".into(),
                    );
                }
                metadata.feature_at(position)?;
                previous = Some(position);
            }
            intervals.push((
                block.absolute_samples[0],
                *block.absolute_samples.last().unwrap(),
            ));
        }
    }
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0));
    if intervals.windows(2).any(|pair| pair[0].1 >= pair[1].0) {
        return Err("overlapping source calibration partitions or blocks".into());
    }
    let training_energy = source
        .training
        .iter()
        .flat_map(|b| &b.residuals)
        .map(|r| r * r)
        .sum::<f64>()
        / counts[0] as f64;
    let rms = training_energy.sqrt();
    // Do not normalize previously disallowed vanishing residual energy into a
    // usable fit: this retains the original absolute energy floor.
    if !rms.is_finite() || rms < MIN_TRAINING_RMS {
        return Err("source training residual energy is below the unchanged floor".into());
    }
    if source
        .training
        .iter()
        .chain(&source.heldout)
        .flat_map(|b| &b.residuals)
        .any(|r| (r / rms).abs() > 1e100)
    {
        return Err("normalized residual exceeds numerical safety limit".into());
    }
    Ok((counts[0], counts[1], rms))
}

fn residual_hash(values: &[f64]) -> String {
    let mut digest = Sha256::new();
    for value in values {
        digest.update(value.to_le_bytes());
    }
    hex::encode(digest.finalize())
}

fn normalize_blocks(
    source: &PoolSource,
    blocks: &[CalibrationBlock],
    partition: &str,
    rms: f64,
) -> (Vec<CalibrationBlock>, Vec<PoolBlockEvidence>) {
    let mut normalized = Vec::with_capacity(blocks.len());
    let mut evidence = Vec::with_capacity(blocks.len());
    for (index, block) in blocks.iter().enumerate() {
        let id = format!("pool:{}:{}:{partition}:{index}", source.id.len(), source.id);
        let residuals: Vec<f64> = block.residuals.iter().map(|r| r / rms).collect();
        evidence.push(PoolBlockEvidence {
            original_id: block.id.clone(),
            pooled_id: id.clone(),
            first_absolute_sample: block.absolute_samples[0],
            last_absolute_sample: *block.absolute_samples.last().unwrap(),
            samples: residuals.len(),
            original_residual_sha256: residual_hash(&block.residuals),
            normalized_residual_sha256: residual_hash(&residuals),
        });
        normalized.push(CalibrationBlock {
            id,
            absolute_samples: block.absolute_samples.clone(),
            residuals,
        });
    }
    (normalized, evidence)
}

fn prepare_pool(
    metadata: &CodecMetadata,
    sources: &[PoolSource],
    frontend: &str,
    target_bounds: (usize, usize),
    sample_rate: u32,
) -> Result<PreparedPool, String> {
    if sample_rate == 0
        || sample_rate != metadata.sample_rate
        || frontend.is_empty()
        || frontend.len() > 128
        || target_bounds.0 >= target_bounds.1
        || target_bounds.1 > metadata.decoded_samples
        || metadata.decoded_samples > (1usize << 53)
        || !valid_sha256(&metadata.source.sha256)
        || sources.len() > MAX_SOURCES
    {
        return Err(
            "codec pool: invalid recording, frontend, target geometry or source limit".into(),
        );
    }
    let rate = usize::try_from(sample_rate).map_err(|e| e.to_string())?;
    rate.checked_mul(60)
        .ok_or("codec pool: sample rate overflow")?;
    let mut prepared = PreparedPool {
        report: PoolFit {
            schema: "codec-pool-v1".into(),
            source_sha256: metadata.source.sha256.clone(),
            frontend: frontend.into(),
            target_bounds,
            sample_rate,
            selected_sources: Vec::new(),
            rejected_sources: Vec::new(),
            distinct_received_frames: 0,
            training_samples: 0,
            heldout_samples: 0,
            status: "prepared".into(),
            reason: String::new(),
            variance_fit: None,
        },
        training: Vec::new(),
        heldout: Vec::new(),
    };
    let mut id_counts = BTreeMap::new();
    for source in sources {
        *id_counts.entry(&source.id).or_insert(0usize) += 1;
    }
    let mut order: Vec<&PoolSource> = sources.iter().collect();
    order.sort_by_key(|s| {
        (
            s.window_start_sample.abs_diff(target_bounds.0),
            s.window_start_sample,
            s.window_end_sample,
            &s.id,
        )
    });
    let mut frames = BTreeSet::new();
    for source in order {
        let attempt = (|| {
            if id_counts[&source.id] != 1 {
                return Err("duplicate source ID is ambiguous; all copies excluded".into());
            }
            source_geometry(source, metadata, frontend, target_bounds, rate)?;
            if prepared.report.selected_sources.iter().any(|selected| {
                source.window_start_sample < selected.window_end_sample
                    && selected.window_start_sample < source.window_end_sample
            }) {
                return Err("source window overlaps an already selected source window".into());
            }
            if source
                .received_frame_sha256
                .iter()
                .any(|hash| frames.contains(hash))
            {
                return Err("native received-frame hash already used by selected source".into());
            }
            let (training_count, heldout_count, rms) = validate_source_blocks(source, metadata)?;
            if prepared.training.len()
                + prepared.heldout.len()
                + source.training.len()
                + source.heldout.len()
                > MAX_BLOCKS
                || prepared.report.training_samples + training_count > MAX_SAMPLES
                || prepared.report.heldout_samples + heldout_count > MAX_SAMPLES
            {
                return Err("deterministic whole-source pool capacity reached".into());
            }
            Ok((training_count, heldout_count, rms))
        })();
        match attempt {
            Err(reason) => prepared.report.rejected_sources.push(PoolRejection {
                id: source.id.clone(),
                window_start_sample: source.window_start_sample,
                window_end_sample: source.window_end_sample,
                reason,
            }),
            Ok((training_count, heldout_count, rms)) => {
                let (training, training_blocks) =
                    normalize_blocks(source, &source.training, "training", rms);
                let (heldout, heldout_blocks) =
                    normalize_blocks(source, &source.heldout, "heldout", rms);
                prepared.training.extend(training);
                prepared.heldout.extend(heldout);
                prepared.report.training_samples += training_count;
                prepared.report.heldout_samples += heldout_count;
                frames.extend(source.received_frame_sha256.iter().cloned());
                prepared.report.selected_sources.push(PoolSelection {
                    id: source.id.clone(),
                    window_start_sample: source.window_start_sample,
                    window_end_sample: source.window_end_sample,
                    start_distance_samples: source.window_start_sample.abs_diff(target_bounds.0),
                    received_frame_sha256: source
                        .received_frame_sha256
                        .iter()
                        .cloned()
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect(),
                    training_rms: rms,
                    training_blocks,
                    heldout_blocks,
                });
            }
        }
    }
    prepared.report.distinct_received_frames = frames.len();
    // A stable rejection list keeps reports invariant under input permutations,
    // including ambiguous duplicate IDs with identical geometric sort keys.
    prepared.report.rejected_sources.sort_by(|a, b| {
        (&a.id, a.window_start_sample, a.window_end_sample, &a.reason).cmp(&(
            &b.id,
            b.window_start_sample,
            b.window_end_sample,
            &b.reason,
        ))
    });
    Ok(prepared)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::codec_reliability::CodecPacket;
    use crate::input;

    fn metadata() -> CodecMetadata {
        CodecMetadata {
            schema: "pool-test-fixture".into(),
            source: input::Identity {
                path: "pool-fixture.ogg".into(),
                sha256: "a".repeat(64),
                bytes: 1_000_000,
            },
            sample_rate: 1000,
            decoded_samples: 100_000,
            packets: (0..1000)
                .map(|i| CodecPacket {
                    packet_index: i,
                    source_pts: (i * 100) as i64,
                    source_duration: 100,
                    source_position: (i * 100) as u64,
                    encoded_bytes: if i % 2 == 0 { 16 } else { 128 },
                    decoded_start: i as usize * 100,
                    decoded_samples: 100,
                    duration_transition: false,
                })
                .collect(),
            priming_packets: 0,
            timestamp_duration_disagreements: 0,
            probe_command: Vec::new(),
            coordinate_contract: "synthetic unit fixture, not actual OGG".into(),
        }
    }

    fn source(id: &str, start: usize, hash: char, amplitude: f64) -> PoolSource {
        let block = |label: &str, offset: usize| {
            let absolute_samples: Vec<f64> =
                (0..800).map(|i| (start + offset + i) as f64).collect();
            let residuals = absolute_samples
                .iter()
                .enumerate()
                .map(|(i, &position)| {
                    let scale = if (position as usize / 100) % 2 == 0 {
                        0.5
                    } else {
                        1.5
                    };
                    amplitude * scale * if i % 2 == 0 { 1.0 } else { -1.0 }
                })
                .collect();
            CalibrationBlock {
                id: format!("{id}/{label}"),
                absolute_samples,
                residuals,
            }
        };
        PoolSource {
            id: id.into(),
            source_sha256: "a".repeat(64),
            frontend: "legacy-fir512".into(),
            window_start_sample: start,
            window_end_sample: start + 2000,
            received_frame_sha256: vec![hash.to_string().repeat(64)],
            training: vec![block("training", 100)],
            heldout: vec![block("heldout", 1100)],
        }
    }

    fn prepare(sources: &[PoolSource]) -> PreparedPool {
        prepare_pool(
            &metadata(),
            sources,
            "legacy-fir512",
            (10_000, 12_000),
            1000,
        )
        .unwrap()
    }

    #[test]
    fn pooled_predictive_proxy_passes_unchanged_gate() {
        let sources = [source("a", 0, 'b', 0.2), source("b", 4000, 'c', 3.0)];
        let fit = fit_pool(
            &metadata(),
            &sources,
            "legacy-fir512",
            (10_000, 12_000),
            1000,
        )
        .unwrap();
        assert_eq!(fit.selected_sources.len(), 2);
        assert_eq!(fit.distinct_received_frames, 2);
        let variance = fit.variance_fit.unwrap();
        assert!(variance.accepted, "{}", variance.reason);
        assert!(variance.heldout_log_score_gain_per_sample >= 0.01);
        assert!(
            variance
                .heldout_block_gains
                .iter()
                .all(|gain| *gain >= -0.01)
        );
    }

    #[test]
    fn nonpredictive_heldout_rejects_without_adjusting_gate() {
        let mut sources = [source("a", 0, 'b', 0.2), source("b", 4000, 'c', 3.0)];
        for source in &mut sources {
            let amplitude = if source.id == "a" { 0.2 } else { 3.0 };
            source.heldout[0].residuals.fill(1.5 * amplitude);
        }
        let fit = fit_pool(
            &metadata(),
            &sources,
            "legacy-fir512",
            (10_000, 12_000),
            1000,
        )
        .unwrap();
        assert_eq!(fit.selected_sources.len(), 2);
        let variance = fit.variance_fit.unwrap();
        assert!(!variance.accepted);
        assert!(variance.model.is_none());
    }

    #[test]
    fn excludes_target_guard_far_different_frontend_and_other_recording() {
        let mut wrong_frontend = source("frontend", 20_000, 'e', 1.0);
        wrong_frontend.frontend = "boxcar32-rms50".into();
        let mut wrong_recording = source("recording", 24_000, 'f', 1.0);
        wrong_recording.source_sha256 = "b".repeat(64);
        let sources = vec![
            source("safe", 4000, 'b', 1.0),
            source("target", 10_000, 'c', 1.0),
            source("guard", 7500, 'd', 1.0),
            source("far", 72_000, 'a', 1.0),
            wrong_frontend,
            wrong_recording,
        ];
        let prepared = prepare(&sources);
        assert_eq!(
            prepared
                .report
                .selected_sources
                .iter()
                .map(|s| s.id.as_str())
                .collect::<Vec<_>>(),
            ["safe"]
        );
        assert_eq!(prepared.report.rejected_sources.len(), 5);
        assert!(
            prepared
                .report
                .rejected_sources
                .iter()
                .any(|s| s.reason.contains("identity"))
        );
    }

    #[test]
    fn duplicates_overlap_and_input_order_cannot_multiply_evidence() {
        let sources = vec![
            source("nearest", 4000, 'b', 1.0),
            source("overlap", 5000, 'c', 1.0),
            source("copy", 0, 'c', 1.0),
            source("independent", 14_000, 'd', 1.0),
        ];
        let expected = prepare(&sources);
        let mut reversed = sources.clone();
        reversed.reverse();
        let actual = prepare(&reversed);
        assert_eq!(
            serde_json::to_value(expected.report).unwrap(),
            serde_json::to_value(actual.report).unwrap()
        );
        assert_eq!(actual.training.len(), 2);
        let ids: Vec<_> = actual.training.iter().map(|b| b.id.as_str()).collect();
        assert!(ids.iter().any(|id| id.contains("independent")));
        assert!(ids.iter().any(|id| id.contains("overlap")));
    }

    #[test]
    fn ambiguous_source_ids_are_all_rejected() {
        let sources = vec![
            source("ambiguous", 0, 'b', 1.0),
            source("ambiguous", 4000, 'c', 1.0),
        ];
        let prepared = prepare(&sources);
        assert!(prepared.report.selected_sources.is_empty());
        assert_eq!(prepared.report.rejected_sources.len(), 2);
    }

    #[test]
    fn heldout_values_cannot_change_training_or_normalization() {
        let sources = vec![source("a", 0, 'b', 0.2), source("b", 4000, 'c', 3.0)];
        let before = prepare(&sources);
        let mut changed = sources;
        for source in &mut changed {
            source.heldout[0]
                .residuals
                .iter_mut()
                .for_each(|r| *r *= 17.0);
        }
        let after = prepare(&changed);
        assert_eq!(
            serde_json::to_value(before.training).unwrap(),
            serde_json::to_value(after.training).unwrap()
        );
        for (a, b) in before
            .report
            .selected_sources
            .iter()
            .zip(&after.report.selected_sources)
        {
            assert_eq!(a.training_rms, b.training_rms);
            assert_eq!(
                serde_json::to_value(&a.training_blocks).unwrap(),
                serde_json::to_value(&b.training_blocks).unwrap()
            );
            assert_ne!(
                a.heldout_blocks[0].normalized_residual_sha256,
                b.heldout_blocks[0].normalized_residual_sha256
            );
        }
    }

    #[test]
    fn validates_geometry_blocks_and_preserves_absolute_energy_floor() {
        let mut overlapping = source("overlap", 4000, 'b', 1.0);
        overlapping.heldout[0].absolute_samples = overlapping.training[0].absolute_samples.clone();
        let mut outside = source("outside", 0, 'c', 1.0);
        outside.training[0].absolute_samples[0] = 9000.0;
        let tiny = source("tiny", 14_000, 'd', 1e-10);
        let prepared = prepare(&[overlapping, outside, tiny]);
        assert!(prepared.report.selected_sources.is_empty());
        assert_eq!(prepared.report.rejected_sources.len(), 3);
        assert!(fit_pool(&metadata(), &[], "legacy-fir512", (12_000, 10_000), 1000).is_err());
        assert!(fit_pool(&metadata(), &[], "legacy-fir512", (10_000, 12_000), 48000).is_err());
    }

    #[test]
    fn one_source_cannot_masquerade_as_pooled_calibration() {
        let fit = fit_pool(
            &metadata(),
            &[source("only", 4000, 'b', 1.0)],
            "legacy-fir512",
            (10_000, 12_000),
            1000,
        )
        .unwrap();
        assert_eq!(fit.selected_sources.len(), 1);
        assert!(fit.variance_fit.is_none());
        assert_eq!(fit.status, "insufficient_sources");
    }

    #[test]
    fn pool_capacity_skips_whole_sources_in_fixed_order() {
        let sources: Vec<_> = (0..20)
            .map(|index| {
                let mut source = source(
                    &format!("source-{index:02}"),
                    14_000 + index * 2500,
                    'b',
                    1.0,
                );
                source.received_frame_sha256 = vec![format!("{:064x}", index + 100)];
                source
            })
            .collect();
        let prepared = prepare(&sources);
        assert_eq!(prepared.report.selected_sources.len(), 16);
        assert_eq!(prepared.training.len() + prepared.heldout.len(), MAX_BLOCKS);
        assert_eq!(prepared.report.rejected_sources.len(), 4);
        assert!(
            prepared
                .report
                .rejected_sources
                .iter()
                .all(|source| source.reason == "deterministic whole-source pool capacity reached")
        );
        assert_eq!(prepared.report.selected_sources[0].id, "source-00");
        assert_eq!(prepared.report.selected_sources[15].id, "source-15");
    }

    #[test]
    fn exact_guard_boundary_is_eligible_and_one_sample_less_is_not() {
        let sources = [
            source("exact", 7000, 'b', 1.0),
            source("inside", 7001, 'c', 1.0),
        ];
        let prepared = prepare(&sources);
        assert_eq!(prepared.report.selected_sources.len(), 1);
        assert_eq!(prepared.report.selected_sources[0].id, "exact");
        assert_eq!(prepared.report.rejected_sources[0].id, "inside");
        assert!(
            prepared.report.rejected_sources[0]
                .reason
                .contains("one-second guard")
        );
    }
}
