//! Bounded acquisition when a present, damaged marker fails hard ASM matching.
//!
//! Candidate ranking uses only channel evidence and a parity-verified codeword.
//! A separate received CRC/FECF and structural profile check admits telemetry.
//! This is not markerless acquisition, a new FEC code, or a CRC-guided search.
use crate::{coded, fec, protocol};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    /// Relaxed hard marker gate; must exceed the baseline threshold.
    pub maximum_marker_hamming: usize,
    /// Signed correlation divided by total absolute marker evidence, in 0..=1.
    pub minimum_marker_correlation: f64,
    /// Fraction of absolute channel evidence contradicted by the decoded word.
    pub maximum_codeword_distance: f64,
    /// Every complete marker+codeword offset is counted, including rejections.
    pub maximum_offsets: usize,
    /// Every relaxed-marker candidate must be decoded; no silent top-K pruning.
    pub maximum_fec_trials: usize,
    /// Conservative additional work, separate from the unchanged baseline cap.
    pub work_budget: u64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            maximum_marker_hamming: 6,
            minimum_marker_correlation: 0.6,
            maximum_codeword_distance: 0.25,
            maximum_offsets: 1_048_576,
            maximum_fec_trials: 128,
            work_budget: 1_000_000_000,
        }
    }
}

impl Config {
    pub fn validate(&self, profile: &coded::CodedSyncConfig) -> Result<(), String> {
        profile.validate()?;
        if matches!(profile.code, fec::FrameCode::None) {
            return Err(
                "FEC-assisted sync requires an explicit RS or LDPC code; uncoded is unsupported"
                    .into(),
            );
        }
        if self.maximum_marker_hamming <= profile.maximum_sync_hamming
            || self.maximum_marker_hamming > profile.syncword.len() / 2
            || !self.minimum_marker_correlation.is_finite()
            || !(0.0..=1.0).contains(&self.minimum_marker_correlation)
            || !self.maximum_codeword_distance.is_finite()
            || !(0.0..=0.5).contains(&self.maximum_codeword_distance)
            || !(1..=8_388_608).contains(&self.maximum_offsets)
            || !(1..=4096).contains(&self.maximum_fec_trials)
            || !(1..=1_000_000_000).contains(&self.work_budget)
        {
            return Err(
                "invalid FEC-assisted marker gate, distance, offset/trial or work budget".into(),
            );
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    FecRejected,
    DistanceRejected,
    IntegrityRejected,
    Duplicate,
    Accepted,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CandidateEvidence {
    pub marker_start_bit: usize,
    pub marker_hamming: usize,
    pub marker_correlation: f64,
    pub codeword_distance: Option<f64>,
    pub fec_converged: bool,
    pub corrected_symbols: Option<usize>,
    pub iterations: Option<usize>,
    /// Zero-based position fixed before any CRC/FECF or profile validation.
    pub rank: Option<usize>,
    pub outcome: Outcome,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub schema: &'static str,
    pub frames: Vec<protocol::ProtocolFrame>,
    pub baseline_frames: usize,
    pub extra_frames: usize,
    pub scanned_offsets: usize,
    pub fec_trials: usize,
    pub additional_work_units: u64,
    /// Complete relaxed-marker candidate ledger, sorted by bit offset.
    pub candidates: Vec<CandidateEvidence>,
}

struct Candidate {
    evidence: CandidateEvidence,
    bytes: Option<Vec<u8>>,
}

struct Reconstructed {
    bytes: Vec<u8>,
    bits: Vec<u8>,
    corrected_symbols: Option<usize>,
    iterations: Option<usize>,
}

fn pack(bits: &[u8]) -> Vec<u8> {
    bits.as_chunks::<8>()
        .0
        .iter()
        .map(|byte| byte.iter().fold(0, |word, bit| (word << 1) | bit))
        .collect()
}

fn reconstruct(soft: &[f64], profile: &coded::CodedSyncConfig) -> Result<Reconstructed, String> {
    match &profile.code {
        fec::FrameCode::Ldpc { config } => {
            let decoded = config.decode_soft(soft)?;
            if !decoded.converged {
                return Err("LDPC parity did not converge".into());
            }
            Ok(Reconstructed {
                bytes: pack(&decoded.output_bits),
                bits: decoded.codeword_bits,
                corrected_symbols: None,
                iterations: Some(decoded.iterations),
            })
        }
        fec::FrameCode::ReedSolomon { config } => {
            let decoded = profile.code.decode(soft, profile.frame_bytes)?;
            let bits = config
                .encode(&decoded.bytes)?
                .iter()
                .flat_map(|byte| (0..8).rev().map(move |bit| (byte >> bit) & 1))
                .collect();
            Ok(Reconstructed {
                bytes: decoded.bytes,
                bits,
                corrected_symbols: decoded.corrected_symbols,
                iterations: None,
            })
        }
        fec::FrameCode::None => Err("uncoded FEC-assisted sync is unsupported".into()),
    }
}

/// Reliability-weighted disagreement; a zero-evidence word has no score.
fn distance(soft: &[f64], bits: &[u8]) -> Option<f64> {
    let mut total = 0.0;
    let mut disagreement = 0.0;
    for (&value, &bit) in soft.iter().zip(bits) {
        total += value.abs();
        if u8::from(value >= 0.0) != bit {
            disagreement += value.abs();
        }
    }
    (total > 0.0).then(|| disagreement / total)
}

/// Decode the unchanged hard-ASM baseline and add bounded relaxed-marker results.
///
/// Positive soft values favour ONE. Baseline frames retain their exact ordering
/// and validation layers. Resource exhaustion returns an error, never a partial
/// union. No prior decoded payload, CRC residual, or reference packet is used to
/// choose offsets, correct bits, rank candidates, or stop the search.
pub fn decode(
    soft: &[f64],
    threshold: f64,
    profile: &coded::CodedSyncConfig,
    recovery: &Config,
) -> Result<Report, String> {
    recovery.validate(profile)?;
    // The existing entry point owns the common finite-input bounds and baseline.
    let mut frames = coded::decode_sync(soft, threshold, profile)?;
    let baseline_frames = frames.len();
    let encoded = profile.code.encoded_bits(profile.frame_bytes)?;
    let marker_length = profile.syncword.len();
    let width = marker_length + encoded;
    let offsets = soft.len().checked_sub(width).map_or(0, |n| n + 1);
    if offsets > recovery.maximum_offsets {
        return Err("FEC-assisted offset budget exceeded; no partial result accepted".into());
    }
    // Include scan, allocation/pass costs and a conservative reconstruction cost.
    let mut work = offsets as u128 * marker_length as u128;
    let trial_work = 2 * coded::candidate_work(profile, encoded) + 8 * encoded as u128;
    if work > recovery.work_budget as u128 {
        return Err("FEC-assisted scan work budget exceeded; no partial result accepted".into());
    }
    let mut candidates = Vec::new();
    for start in 0..offsets {
        let mut hamming = 0;
        let mut signed = 0.0;
        let mut magnitude = 0.0;
        for (&value, &bit) in soft[start..start + marker_length]
            .iter()
            .zip(&profile.syncword)
        {
            let centered = value - threshold;
            hamming += usize::from(u8::from(centered >= 0.0) != bit);
            signed += centered * (2.0 * bit as f64 - 1.0);
            magnitude += centered.abs();
        }
        // Hard-ASM candidates were already handled, including any FEC failure.
        if hamming <= profile.maximum_sync_hamming
            || hamming > recovery.maximum_marker_hamming
            || magnitude == 0.0
        {
            continue;
        }
        let correlation = signed / magnitude;
        if correlation < recovery.minimum_marker_correlation {
            continue;
        }
        if candidates.len() >= recovery.maximum_fec_trials {
            return Err("FEC-assisted trial budget exceeded; no partial result accepted".into());
        }
        work += trial_work;
        if work > recovery.work_budget as u128 {
            return Err(
                "FEC-assisted decoder work budget exceeded; no partial result accepted".into(),
            );
        }
        let mut evidence = CandidateEvidence {
            marker_start_bit: start,
            marker_hamming: hamming,
            marker_correlation: correlation,
            codeword_distance: None,
            fec_converged: false,
            corrected_symbols: None,
            iterations: None,
            rank: None,
            outcome: Outcome::FecRejected,
        };
        let mut word: Vec<_> = soft[start + marker_length..start + width]
            .iter()
            .map(|value| value - threshold)
            .collect();
        coded::derandomize(&mut word, &profile.randomizer);
        let mut bytes = None;
        if let Ok(decoded) = reconstruct(&word, profile) {
            evidence.fec_converged = true;
            evidence.corrected_symbols = decoded.corrected_symbols;
            evidence.iterations = decoded.iterations;
            evidence.codeword_distance = distance(&word, &decoded.bits);
            evidence.outcome = Outcome::DistanceRejected;
            if evidence
                .codeword_distance
                .is_some_and(|d| d <= recovery.maximum_codeword_distance)
            {
                bytes = Some(decoded.bytes);
            }
        }
        candidates.push(Candidate { evidence, bytes });
    }
    // Freeze the entire ranking without reading even one received CRC/FECF.
    let mut ranked: Vec<_> = (0..candidates.len())
        .filter(|&i| candidates[i].bytes.is_some())
        .collect();
    ranked.sort_unstable_by(|&a, &b| {
        let (a, b) = (&candidates[a].evidence, &candidates[b].evidence);
        a.codeword_distance
            .unwrap()
            .total_cmp(&b.codeword_distance.unwrap())
            .then_with(|| b.marker_correlation.total_cmp(&a.marker_correlation))
            .then_with(|| a.marker_start_bit.cmp(&b.marker_start_bit))
    });
    for (rank, &i) in ranked.iter().enumerate() {
        candidates[i].evidence.rank = Some(rank);
    }
    let mut seen: HashSet<_> = frames.iter().map(|f| f.frame.clone()).collect();
    for i in ranked {
        let candidate = &mut candidates[i];
        let bytes = candidate.bytes.take().unwrap();
        let Ok(mut layers) = profile.validator.decode(&bytes) else {
            candidate.evidence.outcome = Outcome::IntegrityRejected;
            continue;
        };
        if !seen.insert(bytes.clone()) {
            candidate.evidence.outcome = Outcome::Duplicate;
            continue;
        }
        layers.push(
            match profile.code {
                fec::FrameCode::ReedSolomon { .. } => "reed_solomon_syndrome_verified",
                fec::FrameCode::Ldpc { .. } => "ldpc_syndrome_verified",
                fec::FrameCode::None => unreachable!("validated FEC-assisted profile"),
            }
            .into(),
        );
        layers.push("fec_assisted_sync".into());
        frames.push(protocol::ProtocolFrame {
            frame: bytes,
            validation_layers: layers,
        });
        candidate.evidence.outcome = Outcome::Accepted;
    }
    Ok(Report {
        schema: "framelift-fec-assisted-sync-v1",
        extra_frames: frames.len() - baseline_frames,
        frames,
        baseline_frames,
        scanned_offsets: offsets,
        fec_trials: candidates.len(),
        additional_work_units: work as u64,
        candidates: candidates.into_iter().map(|c| c.evidence).collect(),
    })
}

#[cfg(test)]
#[path = "tests/fec_sync_tests.rs"]
mod tests;
