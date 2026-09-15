//! Explicit sync -> derandomization -> FEC -> integrity-aware frame validation.
//! FEC convergence alone never creates a trusted telemetry frame. Supported
//! configurations require a received packet CRC or transfer-frame FECF as well.
use crate::{fec, protocol, space_link};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Randomizer {
    #[default]
    None,
    CcsdsTm255,
    CcsdsTm131071,
    CcsdsTc255,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum FrameValidator {
    Ax25,
    CcsdsTm {
        config: protocol::TmTransferFrameConfig,
    },
    SpaceLink {
        config: space_link::SpaceLinkConfig,
    },
}
impl FrameValidator {
    pub fn validate(&self) -> Result<(), String> {
        match self {
            Self::Ax25 => Ok(()),
            Self::CcsdsTm { config } => {
                config.validate()?;
                if !config.fecf_present {
                    return Err("coded TM requires received FECF".into());
                }
                Ok(())
            }
            Self::SpaceLink { config } => {
                config.validate()?;
                if !config.has_integrity_check() {
                    return Err("coded space link requires CRC/FECF; structure or FEC convergence is not integrity".into());
                }
                Ok(())
            }
        }
    }
    pub fn decode(&self, bytes: &[u8]) -> Result<Vec<String>, String> {
        self.validate()?;
        match self {
            Self::Ax25 => {
                if bytes.len() < 2
                    || !protocol::valid_ax25_fcs(bytes)
                    || !protocol::valid_ax25_ui(&bytes[..bytes.len() - 2])
                {
                    return Err("invalid AX.25 UI/FCS".into());
                }
                Ok(vec!["crc16_x25".into(), "ax25_ui".into()])
            }
            Self::CcsdsTm { config } => {
                let v = protocol::validate_tm_transfer_frame(bytes, config, None)?;
                if !v.accepted {
                    return Err(v
                        .rejection_reason
                        .unwrap_or_else(|| "TM integrity rejected".into()));
                }
                Ok(v.validation_layers)
            }
            Self::SpaceLink { config } => Ok(config.decode(bytes)?.validation_layers),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CodedSyncConfig {
    /// Binary bits, MSB first. Sync is outside the coded/randomized region.
    pub syncword: Vec<u8>,
    pub maximum_sync_hamming: usize,
    /// Decoded frame length, including received CRC/FECF, excluding sync/parity.
    pub frame_bytes: usize,
    pub code: fec::FrameCode,
    #[serde(default)]
    pub randomizer: Randomizer,
    pub validator: FrameValidator,
    /// Exceeding this explicit resource budget fails the attempt, never prunes.
    #[serde(default = "default_candidates")]
    pub maximum_candidates: usize,
}
fn default_candidates() -> usize {
    4096
}
impl CodedSyncConfig {
    pub fn validate(&self) -> Result<(), String> {
        if !(32..=128).contains(&self.syncword.len())
            || self.syncword.iter().any(|&b| b > 1)
            || self.maximum_sync_hamming > 2
            || !(1..=65536).contains(&self.frame_bytes)
            || !(1..=65536).contains(&self.maximum_candidates)
        {
            return Err("invalid sync, frame length, Hamming or candidate budget".into());
        }
        self.code.validate()?;
        let encoded = self.code.encoded_bits(self.frame_bytes)?;
        if encoded == 0 || encoded > 4_194_304 {
            return Err("coded frame exceeds 4194304 bits".into());
        }
        self.validator.validate()?;
        match &self.validator {
            FrameValidator::CcsdsTm { config } if config.frame_length_bytes != self.frame_bytes => {
                Err("coded frame length contradicts TM validator".into())
            }
            FrameValidator::SpaceLink {
                config:
                    space_link::SpaceLinkConfig::Aos {
                        frame_length_bytes, ..
                    },
            } if *frame_length_bytes != self.frame_bytes => {
                Err("coded frame length contradicts AOS validator".into())
            }
            FrameValidator::SpaceLink {
                config:
                    space_link::SpaceLinkConfig::Uslp {
                        expected_frame_length_bytes: Some(n),
                        ..
                    },
            } if *n != self.frame_bytes => {
                Err("coded frame length contradicts USLP validator".into())
            }
            _ => Ok(()),
        }
    }
}

/// CCSDS 131.0-B-5 §10.4. Initialization resets at each codeblock, NOT at
/// individual RS interleaving lanes. Soft sign inversion preserves reliability.
pub fn derandomize(soft: &mut [f64], kind: &Randomizer) {
    let (mut state, degree) = match kind {
        Randomizer::None => return,
        Randomizer::CcsdsTm255 => (255_u32, 8),
        Randomizer::CcsdsTc255 => (255_u32, 8),
        Randomizer::CcsdsTm131071 => (0b11000111000111000, 17),
    };
    for value in soft {
        if state & 1 != 0 {
            *value = -*value;
        }
        let feedback = if matches!(kind, Randomizer::CcsdsTc255) {
            (state ^ (state >> 1) ^ (state >> 2) ^ (state >> 3) ^ (state >> 4) ^ (state >> 6)) & 1
        } else if degree == 8 {
            (state ^ (state >> 3) ^ (state >> 5) ^ (state >> 7)) & 1
        } else {
            (state ^ (state >> 14)) & 1
        };
        state = (state >> 1) | (feedback << (degree - 1));
    }
}

/// Sliding integer sync matcher. Bits enter MSB-first, matching the serialized
/// `syncword` order. Hamming distance is bounded to 0..=2 by the validated
/// configuration, so clearing at most two low set bits avoids a full popcount.
/// Candidate starts are yielded lazily and strictly in ascending order.
struct SyncStarts<'a> {
    soft: &'a [f64],
    threshold: f64,
    pattern: u128,
    mask: u128,
    observed: u128,
    width: usize,
    maximum_hamming: usize,
    next_start: usize,
    end_exclusive: usize,
}
impl<'a> SyncStarts<'a> {
    fn new(
        soft: &'a [f64],
        threshold: f64,
        syncword: &[u8],
        maximum_hamming: usize,
        end_exclusive: usize,
    ) -> Self {
        debug_assert!((32..=128).contains(&syncword.len()));
        debug_assert!(maximum_hamming <= 2);
        debug_assert!(end_exclusive == 0 || end_exclusive <= soft.len() - syncword.len() + 1);
        let pattern = syncword
            .iter()
            .fold(0u128, |register, &bit| (register << 1) | bit as u128);
        let mask = if syncword.len() == 128 {
            u128::MAX
        } else {
            (1u128 << syncword.len()) - 1
        };
        let observed = if end_exclusive == 0 {
            0
        } else {
            soft[..syncword.len()]
                .iter()
                .fold(0u128, |register, value| {
                    (register << 1) | u128::from(*value >= threshold)
                })
        };
        Self {
            soft,
            threshold,
            pattern,
            mask,
            observed,
            width: syncword.len(),
            maximum_hamming,
            next_start: 0,
            end_exclusive,
        }
    }
}
impl Iterator for SyncStarts<'_> {
    type Item = usize;

    fn next(&mut self) -> Option<Self::Item> {
        while self.next_start < self.end_exclusive {
            let start = self.next_start;
            if start != 0 {
                self.observed = ((self.observed << 1) & self.mask)
                    | u128::from(self.soft[start + self.width - 1] >= self.threshold);
            }
            self.next_start += 1;
            let mut difference = self.observed ^ self.pattern;
            if difference == 0 {
                return Some(start);
            }
            difference &= difference - 1;
            if difference == 0 {
                if self.maximum_hamming >= 1 {
                    return Some(start);
                }
                continue;
            }
            difference &= difference - 1;
            if difference == 0 && self.maximum_hamming >= 2 {
                return Some(start);
            }
        }
        None
    }
}

pub fn decode_sync(
    soft: &[f64],
    threshold: f64,
    config: &CodedSyncConfig,
) -> Result<Vec<protocol::ProtocolFrame>, String> {
    config.validate()?;
    if soft.len() > 8_388_608
        || !threshold.is_finite()
        || threshold.abs() > 1e100
        || soft.iter().any(|v| !v.is_finite() || v.abs() > 1e100)
    {
        return Err("coded sync requires bounded finite soft bits/threshold".into());
    }
    let encoded = config.code.encoded_bits(config.frame_bytes)?;
    // Bound the product of candidate count and decoder work, not just each
    // dimension separately. These conservative units are not CPU seconds.
    let candidate_work: u128 = match &config.code {
        fec::FrameCode::None => encoded as u128,
        fec::FrameCode::ReedSolomon { config: c } => {
            let parity = c.parity_symbols as u128;
            (8 * c.codeword_symbols() as u128 * parity + 2 * (parity / 2).pow(3))
                * c.interleaving as u128
        }
        fec::FrameCode::Ldpc { config: c } => {
            let edges: u128 = c.checks.iter().map(|row| row.len() as u128).sum();
            (6 * edges + 4 * c.codeword_bits as u128) * c.max_iterations as u128
        }
    };
    let mut accumulated_work = 0u128;
    let width = config.syncword.len() + encoded;
    let mut candidates = 0;
    let mut seen = HashSet::new();
    let mut frames = Vec::new();
    let start_count = soft
        .len()
        .saturating_sub(width)
        .saturating_add(usize::from(soft.len() >= width));
    for start in SyncStarts::new(
        soft,
        threshold,
        &config.syncword,
        config.maximum_sync_hamming,
        start_count,
    ) {
        candidates += 1;
        if candidates > config.maximum_candidates {
            return Err("sync candidate budget exceeded; no partial result accepted".into());
        }
        accumulated_work += candidate_work;
        if accumulated_work > 1_000_000_000 {
            return Err(
                "aggregate coded-candidate work budget exceeded; no partial result accepted".into(),
            );
        }
        let mut word: Vec<_> = soft[start + config.syncword.len()..start + width]
            .iter()
            .map(|v| v - threshold)
            .collect();
        derandomize(&mut word, &config.randomizer);
        // An uncorrectable codeword is a normal rejected candidate. Configuration
        // and length errors were checked above, before scanning or accepting any.
        let Ok(decoded) = config.code.decode(&word, config.frame_bytes) else {
            continue;
        };
        if seen.contains(&decoded.bytes) {
            continue;
        }
        let Ok(mut layers) = config.validator.decode(&decoded.bytes) else {
            continue;
        };
        if decoded.parity_verified {
            layers.push(
                match &config.code {
                    fec::FrameCode::ReedSolomon { .. } => "reed_solomon_syndrome_verified",
                    fec::FrameCode::Ldpc { .. } => "ldpc_syndrome_verified",
                    fec::FrameCode::None => {
                        return Err("uncoded result falsely claimed FEC verification".into());
                    }
                }
                .into(),
            );
        }
        seen.insert(decoded.bytes.clone());
        frames.push(protocol::ProtocolFrame {
            frame: decoded.bytes,
            validation_layers: layers,
        });
    }
    Ok(frames)
}

#[cfg(test)]
#[path = "tests/coded_tests.rs"]
mod tests;
