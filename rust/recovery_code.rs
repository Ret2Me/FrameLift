//! Bounded fixed-frame FEC adapters for iterative IQ recovery.
//!
//! Positive LLRs mean one; frame bytes and convolutional input bits are MSB
//! first. These are complete, already aligned code blocks, not an HDLC flag,
//! NRZI, bit-stuffing or continuous convolutional stream synchronizer. A
//! reconstructed codeword is exposed only after a received CRC/FECF passes.
//! RS decisions are never returned as soft or independent evidence.

use crate::{
    coded::FrameValidator,
    fec::{LdpcConfig, ReedSolomonConfig},
};
use serde::{Deserialize, Deserializer, Serialize, Serializer};

pub const MAX_CODE_BITS: usize = 65_536;
const LLR_LIMIT: f64 = 64.0;
const STATES: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConvolutionalTermination {
    /// A known initial state and unrestricted end state; no tail bits removed.
    Unconstrained,
    /// Exactly six transmitted zero input bits after the last frame bit.
    ZeroTail,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConvolutionalConfig {
    /// register = (previous_state << 1) | input; output = parity(register & g).
    /// The order of these two polynomials is the transmitted output order.
    pub generators: [u8; 2],
    pub invert: [bool; 2],
    pub initial_state: u8,
    pub termination: ConvolutionalTermination,
}

impl ConvolutionalConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.initial_state >= 64
            || self.generators.iter().any(|&g| g == 0 || g > 127)
            || (self.generators[0] | self.generators[1]) & 64 == 0
            || polynomial_gcd(self.generators[0], self.generators[1]) != 1
        {
            return Err("convolutional K7 requires state 0..63, nonzero seven-bit generators spanning degree six and relatively prime polynomials".into());
        }
        Ok(())
    }

    fn tail_bits(&self) -> usize {
        usize::from(self.termination == ConvolutionalTermination::ZeroTail) * 6
    }

    fn encode(&self, bits: &[u8]) -> Vec<u8> {
        let mut state = self.initial_state as usize;
        let mut encoded = Vec::with_capacity((bits.len() + self.tail_bits()) * 2);
        for bit in bits
            .iter()
            .copied()
            .chain(std::iter::repeat_n(0, self.tail_bits()))
        {
            let register = (state << 1) | bit as usize;
            encoded.extend(self.output(register));
            state = register & 63;
        }
        encoded
    }

    fn output(&self, register: usize) -> [u8; 2] {
        std::array::from_fn(|i| {
            (((register as u8 & self.generators[i]).count_ones() & 1) as u8)
                ^ u8::from(self.invert[i])
        })
    }
}

fn polynomial_gcd(mut a: u8, mut b: u8) -> u8 {
    while b != 0 {
        while a != 0 && a.leading_zeros() <= b.leading_zeros() {
            a ^= b << (b.leading_zeros() - a.leading_zeros());
        }
        (a, b) = (b, a);
    }
    a
}

#[derive(Clone, Debug)]
pub enum CodeProfile {
    Ldpc {
        config: LdpcConfig,
    },
    Uncoded {
        frame_bytes: usize,
    },
    ConvolutionalK7 {
        frame_bytes: usize,
        config: ConvolutionalConfig,
    },
    ReedSolomon {
        config: ReedSolomonConfig,
    },
    ConvolutionalReedSolomon {
        convolutional: ConvolutionalConfig,
        reed_solomon: ReedSolomonConfig,
        /// Transmitter order: RS -> randomizer -> convolutional. Resets once
        /// per full interleaved RS block, never once per interleaving lane.
        interstage_randomizer: crate::coded::Randomizer,
    },
}

// Existing LDPC plans retain their exact raw-object JSON representation. New
// families carry an explicit tag; neither field guessing nor default codes.
#[derive(Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum TaggedProfile {
    Ldpc {
        config: LdpcConfig,
    },
    Uncoded {
        frame_bytes: usize,
    },
    ConvolutionalK7 {
        frame_bytes: usize,
        config: ConvolutionalConfig,
    },
    ReedSolomon {
        config: ReedSolomonConfig,
    },
    ConvolutionalReedSolomon {
        convolutional: ConvolutionalConfig,
        reed_solomon: ReedSolomonConfig,
        #[serde(default)]
        interstage_randomizer: crate::coded::Randomizer,
    },
}

impl Serialize for CodeProfile {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let tagged = match self {
            Self::Ldpc { config } => return config.serialize(serializer),
            Self::Uncoded { frame_bytes } => TaggedProfile::Uncoded {
                frame_bytes: *frame_bytes,
            },
            Self::ConvolutionalK7 {
                frame_bytes,
                config,
            } => TaggedProfile::ConvolutionalK7 {
                frame_bytes: *frame_bytes,
                config: config.clone(),
            },
            Self::ReedSolomon { config } => TaggedProfile::ReedSolomon {
                config: config.clone(),
            },
            Self::ConvolutionalReedSolomon {
                convolutional,
                reed_solomon,
                interstage_randomizer,
            } => TaggedProfile::ConvolutionalReedSolomon {
                convolutional: convolutional.clone(),
                reed_solomon: reed_solomon.clone(),
                interstage_randomizer: interstage_randomizer.clone(),
            },
        };
        tagged.serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for CodeProfile {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Wire {
            Tagged(TaggedProfile),
            Legacy(LdpcConfig),
        }
        Ok(match Wire::deserialize(deserializer)? {
            Wire::Legacy(config) | Wire::Tagged(TaggedProfile::Ldpc { config }) => {
                Self::Ldpc { config }
            }
            Wire::Tagged(TaggedProfile::Uncoded { frame_bytes }) => Self::Uncoded { frame_bytes },
            Wire::Tagged(TaggedProfile::ConvolutionalK7 {
                frame_bytes,
                config,
            }) => Self::ConvolutionalK7 {
                frame_bytes,
                config,
            },
            Wire::Tagged(TaggedProfile::ReedSolomon { config }) => Self::ReedSolomon { config },
            Wire::Tagged(TaggedProfile::ConvolutionalReedSolomon {
                convolutional,
                reed_solomon,
                interstage_randomizer,
            }) => Self::ConvolutionalReedSolomon {
                convolutional,
                reed_solomon,
                interstage_randomizer,
            },
        })
    }
}

impl From<LdpcConfig> for CodeProfile {
    fn from(config: LdpcConfig) -> Self {
        Self::Ldpc { config }
    }
}

#[derive(Clone, Debug)]
pub struct AcceptedFrame {
    /// Complete decoded frame, including its received integrity trailer.
    pub bytes: Vec<u8>,
    /// Complete encoded bits, excluding outer randomization/permutation/sync.
    pub validated_codeword: Vec<u8>,
    pub validation_layers: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct RecoveryOutput {
    pub accepted: Option<AcceptedFrame>,
    /// Coded-bit APP, not an additional observation. None for hard-only codes.
    pub posterior_llr: Option<Vec<f64>>,
    /// Genuine coded-bit extrinsic; never RS hard or received-bit duplication.
    pub extrinsic_llr: Option<Vec<f64>>,
    pub parity_verified: bool,
    pub corrected_symbols: Option<usize>,
    pub iterations: Option<usize>,
    pub rejection_reason: Option<String>,
}

impl RecoveryOutput {
    fn rejected(reason: impl Into<String>) -> Self {
        Self {
            accepted: None,
            posterior_llr: None,
            extrinsic_llr: None,
            parity_verified: false,
            corrected_symbols: None,
            iterations: None,
            rejection_reason: Some(reason.into()),
        }
    }
}

impl CodeProfile {
    pub fn as_ldpc(&self) -> Option<&LdpcConfig> {
        if let Self::Ldpc { config } = self {
            Some(config)
        } else {
            None
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        match self {
            Self::Ldpc { config } => config.validate()?,
            Self::Uncoded { .. } => (),
            Self::ConvolutionalK7 { config, .. } => config.validate()?,
            Self::ReedSolomon { config } => config.validate()?,
            Self::ConvolutionalReedSolomon {
                convolutional,
                reed_solomon,
                ..
            } => {
                convolutional.validate()?;
                reed_solomon.validate()?;
            }
        }
        let bytes = self.raw_frame_bytes();
        if bytes == 0 || bytes > MAX_CODE_BITS / 8 {
            return Err("recovery decoded frame requires 1..8192 bytes".into());
        }
        if self
            .raw_encoded_bits()
            .is_none_or(|n| n == 0 || n > MAX_CODE_BITS)
        {
            return Err("recovery codeword requires 1..65536 bits".into());
        }
        Ok(())
    }

    fn raw_frame_bytes(&self) -> usize {
        match self {
            Self::Ldpc { config } => config.output_bits.len() / 8,
            Self::Uncoded { frame_bytes } | Self::ConvolutionalK7 { frame_bytes, .. } => {
                *frame_bytes
            }
            Self::ReedSolomon { config }
            | Self::ConvolutionalReedSolomon {
                reed_solomon: config,
                ..
            } => config.data_symbols().saturating_mul(config.interleaving),
        }
    }

    fn raw_encoded_bits(&self) -> Option<usize> {
        match self {
            Self::Ldpc { config } => Some(config.codeword_bits),
            Self::Uncoded { frame_bytes } => frame_bytes.checked_mul(8),
            Self::ConvolutionalK7 {
                frame_bytes,
                config,
            } => frame_bytes
                .checked_mul(8)?
                .checked_add(config.tail_bits())?
                .checked_mul(2),
            Self::ReedSolomon { config } => config
                .codeword_symbols()
                .checked_mul(config.interleaving)?
                .checked_mul(8),
            Self::ConvolutionalReedSolomon {
                convolutional,
                reed_solomon,
                ..
            } => reed_solomon
                .codeword_symbols()
                .checked_mul(reed_solomon.interleaving)?
                .checked_mul(8)?
                .checked_add(convolutional.tail_bits())?
                .checked_mul(2),
        }
    }

    pub fn frame_bytes(&self) -> Result<usize, String> {
        self.validate()?;
        Ok(self.raw_frame_bytes())
    }

    pub fn encoded_bits(&self) -> Result<usize, String> {
        self.validate()?;
        self.raw_encoded_bits()
            .ok_or_else(|| "recovery codeword dimensions overflow".into())
    }

    /// Conservative arithmetic work proxy, not elapsed time or a GPU promise.
    pub fn work_per_pass(&self) -> Result<u64, String> {
        let bits = self.encoded_bits()? as u64;
        let rs_work = |r: &ReedSolomonConfig| (r.interleaving as u64) * 255 * 128 * 128;
        Ok(match self {
            Self::Ldpc { config } => {
                config.checks.iter().map(|r| r.len() as u64).sum::<u64>()
                    * config.max_iterations as u64
                    * 8
            }
            Self::Uncoded { .. } => bits,
            Self::ReedSolomon { config } => rs_work(config),
            Self::ConvolutionalK7 { .. } => bits * 64 * 16,
            Self::ConvolutionalReedSolomon { reed_solomon, .. } => {
                bits * 64 * 16 + rs_work(reed_solomon)
            }
        })
    }

    pub fn decode(
        &self,
        soft: &[f64],
        validator: &FrameValidator,
    ) -> Result<RecoveryOutput, String> {
        if soft.len() != self.encoded_bits()? || soft.iter().any(|x| !x.is_finite()) {
            return Err("recovery input must contain exactly one finite LLR per coded bit".into());
        }
        validator.validate()?;
        if soft.iter().all(|&x| x == 0.0) {
            return Ok(RecoveryOutput::rejected("no nonzero channel evidence"));
        }
        let mut output = RecoveryOutput::rejected("frame integrity not verified");
        let (bytes, word, layer) = match self {
            Self::Ldpc { config } => {
                let decoded = config.decode_soft(soft)?;
                output.posterior_llr = Some(decoded.posterior_llr);
                output.extrinsic_llr = Some(decoded.extrinsic_llr);
                output.iterations = Some(decoded.iterations);
                output.parity_verified = decoded.converged;
                if !decoded.converged {
                    output.rejection_reason = Some("LDPC parity checks did not converge".into());
                    return Ok(output);
                }
                (
                    bytes_from_bits(&decoded.output_bits),
                    decoded.codeword_bits,
                    Some("ldpc_parity"),
                )
            }
            Self::Uncoded { .. } => (hard_bytes(soft), hard_bits(soft), None),
            Self::ConvolutionalK7 {
                frame_bytes,
                config,
            } => {
                let decoded = bcjr(soft, frame_bytes * 8, config);
                output.posterior_llr = Some(decoded.posterior);
                output.extrinsic_llr = Some(decoded.extrinsic);
                output.iterations = Some(1);
                // No parity syndrome exists here. The reconstructed path is a
                // valid convolutional path; independent acceptance is CRC-only.
                (
                    bytes_from_bits(&decoded.bits),
                    Vec::new(),
                    Some("convolutional_k7_log_map"),
                )
            }
            Self::ReedSolomon { config } => {
                let (bytes, corrected) = match config.decode(&hard_bytes(soft)) {
                    Ok(value) => value,
                    Err(reason) => return Ok(RecoveryOutput::rejected(reason)),
                };
                output.corrected_symbols = Some(corrected);
                output.parity_verified = true;
                (bytes, Vec::new(), Some("reed_solomon_parity"))
            }
            Self::ConvolutionalReedSolomon {
                convolutional,
                reed_solomon,
                interstage_randomizer,
            } => {
                let rs_bits = reed_solomon.codeword_symbols() * reed_solomon.interleaving * 8;
                let mut decoded = bcjr(soft, rs_bits, convolutional);
                crate::coded::derandomize(&mut decoded.information_llr, interstage_randomizer);
                output.posterior_llr = Some(decoded.posterior);
                output.extrinsic_llr = Some(decoded.extrinsic);
                output.iterations = Some(1);
                let (bytes, corrected) =
                    match reed_solomon.decode(&hard_bytes(&decoded.information_llr)) {
                        Ok(value) => value,
                        Err(reason) => {
                            output.rejection_reason = Some(reason);
                            return Ok(output);
                        }
                    };
                output.corrected_symbols = Some(corrected);
                output.parity_verified = true;
                (
                    bytes,
                    Vec::new(),
                    Some("convolutional_k7_log_map_and_reed_solomon_parity"),
                )
            }
        };
        match validator.decode(&bytes) {
            Ok(mut validation_layers) => {
                // Re-encoding for interference cancellation is gated by the
                // received integrity check, not merely an FEC decision.
                let validated_codeword = match self {
                    Self::Ldpc { .. } | Self::Uncoded { .. } => word,
                    Self::ConvolutionalK7 { config, .. } => config.encode(&bits_from_bytes(&bytes)),
                    Self::ReedSolomon { config } => bits_from_bytes(&config.encode(&bytes)?),
                    Self::ConvolutionalReedSolomon {
                        convolutional,
                        reed_solomon,
                        interstage_randomizer,
                    } => {
                        let mut bits: Vec<f64> = bits_from_bytes(&reed_solomon.encode(&bytes)?)
                            .iter()
                            .map(|&b| 2.0 * b as f64 - 1.0)
                            .collect();
                        crate::coded::derandomize(&mut bits, interstage_randomizer);
                        convolutional.encode(&hard_bits(&bits))
                    }
                };
                if let Some(layer) = layer {
                    validation_layers.insert(0, layer.into());
                }
                output.accepted = Some(AcceptedFrame {
                    bytes,
                    validated_codeword,
                    validation_layers,
                });
                output.rejection_reason = None;
            }
            Err(reason) => output.rejection_reason = Some(reason),
        }
        Ok(output)
    }
}

fn hard_bits(soft: &[f64]) -> Vec<u8> {
    soft.iter().map(|&x| u8::from(x >= 0.0)).collect()
}
fn hard_bytes(soft: &[f64]) -> Vec<u8> {
    bytes_from_bits(&hard_bits(soft))
}
fn bytes_from_bits(bits: &[u8]) -> Vec<u8> {
    bits.as_chunks::<8>()
        .0
        .iter()
        .map(|chunk| chunk.iter().fold(0, |byte, &b| (byte << 1) | b))
        .collect()
}
fn bits_from_bytes(bytes: &[u8]) -> Vec<u8> {
    bytes
        .iter()
        .flat_map(|&b| (0..8).rev().map(move |i| (b >> i) & 1))
        .collect()
}

struct ConvOutput {
    bits: Vec<u8>,
    information_llr: Vec<f64>,
    posterior: Vec<f64>,
    extrinsic: Vec<f64>,
}

fn log_add(a: f64, b: f64) -> f64 {
    if a == f64::NEG_INFINITY {
        return b;
    }
    if b == f64::NEG_INFINITY {
        return a;
    }
    a.max(b) + (-((a - b).abs())).exp().ln_1p()
}

fn normalize(values: &mut [f64; STATES]) {
    let maximum = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    for v in values {
        *v -= maximum;
    }
}

/// Exact log-MAP over the explicit 64-state convolutional trellis. Channel
/// LLRs are clipped before the recursion; extrinsic code-bit messages exclude
/// their own input likelihood directly, avoiding APP clipping/subtraction.
fn bcjr(soft: &[f64], data_bits: usize, config: &ConvolutionalConfig) -> ConvOutput {
    let steps = soft.len() / 2;
    let channel: Vec<[f64; 2]> = soft
        .as_chunks::<2>()
        .0
        .iter()
        .map(|x| {
            [
                x[0].clamp(-LLR_LIMIT, LLR_LIMIT),
                x[1].clamp(-LLR_LIMIT, LLR_LIMIT),
            ]
        })
        .collect();
    let signs: [[f64; 2]; 128] =
        std::array::from_fn(|r| config.output(r).map(|b| 2.0 * b as f64 - 1.0));
    let mut alpha = vec![[f64::NEG_INFINITY; STATES]; steps + 1];
    alpha[0][config.initial_state as usize] = 0.0;
    for (t, pair) in channel.iter().enumerate() {
        let mut next = [f64::NEG_INFINITY; STATES];
        for (state, &previous) in alpha[t].iter().enumerate() {
            for bit in 0..=usize::from(t < data_bits) {
                let r = (state << 1) | bit;
                let branch = 0.5 * (pair[0] * signs[r][0] + pair[1] * signs[r][1]);
                next[r & 63] = log_add(next[r & 63], previous + branch);
            }
        }
        normalize(&mut next);
        alpha[t + 1] = next;
    }
    let mut beta = if config.termination == ConvolutionalTermination::ZeroTail {
        let mut values = [f64::NEG_INFINITY; STATES];
        values[0] = 0.0;
        values
    } else {
        [0.0; STATES]
    };
    let mut bits = vec![0; data_bits];
    let mut information_llr = vec![0.0; data_bits];
    let mut posterior = vec![0.0; soft.len()];
    let mut extrinsic = vec![0.0; soft.len()];
    for t in (0..steps).rev() {
        let pair = channel[t];
        let mut next_beta = [f64::NEG_INFINITY; STATES];
        let mut data_mass = [f64::NEG_INFINITY; 2];
        let mut extrinsic_mass = [[f64::NEG_INFINITY; 2]; 2];
        for state in 0..STATES {
            for (bit, mass) in data_mass
                .iter_mut()
                .enumerate()
                .take(1 + usize::from(t < data_bits))
            {
                let r = (state << 1) | bit;
                let sign = signs[r];
                let likelihood = [0.5 * pair[0] * sign[0], 0.5 * pair[1] * sign[1]];
                let branch = likelihood[0] + likelihood[1];
                let future = beta[r & 63];
                next_beta[state] = log_add(next_beta[state], branch + future);
                let context = alpha[t][state] + future;
                *mass = log_add(*mass, context + branch);
                for j in 0..2 {
                    let b = usize::from(sign[j] > 0.0);
                    extrinsic_mass[j][b] =
                        log_add(extrinsic_mass[j][b], context + likelihood[1 - j]);
                }
            }
        }
        if t < data_bits {
            bits[t] = u8::from(data_mass[1] >= data_mass[0]);
            information_llr[t] = (data_mass[1] - data_mass[0]).clamp(-LLR_LIMIT, LLR_LIMIT);
        }
        for j in 0..2 {
            let value = extrinsic_mass[j][1] - extrinsic_mass[j][0];
            extrinsic[2 * t + j] = value.clamp(-LLR_LIMIT, LLR_LIMIT);
            posterior[2 * t + j] = (value + pair[j]).clamp(-LLR_LIMIT, LLR_LIMIT);
        }
        normalize(&mut next_beta);
        beta = next_beta;
    }
    ConvOutput {
        bits,
        information_llr,
        posterior,
        extrinsic,
    }
}

#[cfg(test)]
#[path = "recovery_code_tests.rs"]
mod tests;
