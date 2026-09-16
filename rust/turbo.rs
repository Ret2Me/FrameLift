//! Experimental BCJR/LDPC turbo equalization on an explicitly aligned block.
//!
//! This is not a blind IQ receiver. Input samples must be symbol-spaced and
//! the channel, wire interleaver, randomizer and FEC must describe the actual
//! transmitter. Only extrinsic messages cross the iteration boundary. The CRC
//! is an acceptance/early-stop check, never a bit-search or training objective.

use crate::{coded, fec::LdpcConfig, protocol, soft_sequence};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub samples: Vec<f64>,
    pub channel: soft_sequence::Channel,
    pub code: LdpcConfig,
    /// wire index -> LDPC codeword index; empty means identity. Not inferred.
    #[serde(default)]
    pub wire_to_code: Vec<usize>,
    /// Applies in codeword order, after deinterleaving; inverse on feedback.
    #[serde(default)]
    pub randomizer: coded::Randomizer,
    pub validator: coded::FrameValidator,
    pub iterations: usize,
    /// Fraction of new decoder extrinsic information; remaining weight keeps
    /// the preceding decoder extrinsic (never adds both messages in full).
    pub damping: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct Iteration {
    pub index: usize,
    pub ldpc_iterations: usize,
    pub parity_verified: bool,
    pub integrity_verified: bool,
    pub mean_abs_feedback_llr: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub schema: &'static str,
    pub accepted: bool,
    /// Only populated after both FEC and the configured independent CRC pass.
    pub frame_hex: Option<String>,
    pub validation_layers: Vec<String>,
    pub iterations: Vec<Iteration>,
    pub stop_reason: &'static str,
    pub experimental: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub channel_history: Vec<Vec<soft_sequence::Channel>>,
    /// Internal reconstruction witness, populated only after parity AND CRC.
    #[serde(skip)]
    pub(crate) validated_codeword: Option<Vec<u8>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct JointConfig {
    pub segment_symbols: usize,
    pub channel_damping: f64,
}
impl JointConfig {
    pub fn validate(&self) -> Result<(), String> {
        if !(16..=4096).contains(&self.segment_symbols)
            || !self.channel_damping.is_finite()
            || !(0.0..=0.5).contains(&self.channel_damping)
        {
            return Err(
                "joint channel update requires 16..4096 symbols and damping in [0,0.5]".into(),
            );
        }
        Ok(())
    }
}

impl Request {
    fn validate(&self) -> Result<(), String> {
        self.channel.validate()?;
        self.code.validate()?;
        self.validator.validate()?;
        let n = self.code.codeword_bits;
        if self.samples.len() != n
            || !(1..=16).contains(&self.iterations)
            || !self.damping.is_finite()
            || !(0.0..=1.0).contains(&self.damping)
        {
            return Err(
                "turbo requires one sample per coded bit, 1..=16 iterations and damping in [0,1]"
                    .into(),
            );
        }
        let work = self
            .code
            .checks
            .iter()
            .map(Vec::len)
            .sum::<usize>()
            .checked_mul(self.code.max_iterations)
            .and_then(|n| n.checked_mul(self.iterations));
        if work.is_none_or(|n| n > 100_000_000) {
            return Err("turbo LDPC total edge-iteration work exceeds 100000000".into());
        }
        if !self.wire_to_code.is_empty() {
            if self.wire_to_code.len() != n {
                return Err("wire_to_code must be empty or a complete permutation".into());
            }
            let mut seen = vec![false; n];
            for &i in &self.wire_to_code {
                if i >= n || seen[i] {
                    return Err("wire_to_code is not a bijection".into());
                }
                seen[i] = true;
            }
        }
        Ok(())
    }
}

pub fn decode(request: &Request) -> Result<Report, String> {
    decode_inner(request, std::slice::from_ref(&request.channel), None)
}

pub fn decode_joint(request: &Request, joint: &JointConfig) -> Result<Report, String> {
    joint.validate()?;
    if request.samples.len() > 4096 {
        return Err("joint turbo block exceeds 4096 symbols".into());
    }
    decode_inner(request, std::slice::from_ref(&request.channel), Some(joint))
}

/// I/Q observations alternate in wire order, but channel memory stays within
/// each arm. FEC feedback is deinterleaved before either BCJR recursion.
pub fn decode_branched(
    request: &Request,
    anchors: &[soft_sequence::Channel],
    joint: Option<&JointConfig>,
) -> Result<Report, String> {
    if anchors.len() != 2 || !request.samples.len().is_multiple_of(2) {
        return Err("quadrature turbo requires two channels and an even bit count".into());
    }
    if let Some(joint) = joint {
        joint.validate()?;
        if request.samples.len() > 4096 {
            return Err("joint turbo block exceeds 4096 bits".into());
        }
    }
    decode_inner(request, anchors, joint)
}

pub(crate) fn channel_llr(
    samples: &[f64],
    anchors: &[soft_sequence::Channel],
) -> Result<Vec<f64>, String> {
    let mut llr = vec![0.; samples.len()];
    for (arm, channel) in anchors.iter().enumerate() {
        let data: Vec<_> = samples
            .iter()
            .skip(arm)
            .step_by(anchors.len())
            .copied()
            .collect();
        let detected = soft_sequence::detect(&data, channel, &[])?;
        for (i, value) in detected.extrinsic_llr.into_iter().enumerate() {
            llr[i * anchors.len() + arm] = value;
        }
    }
    Ok(llr)
}

fn decode_inner(
    request: &Request,
    anchors: &[soft_sequence::Channel],
    joint: Option<&JointConfig>,
) -> Result<Report, String> {
    request.validate()?;
    let n = request.samples.len();
    for anchor in anchors {
        anchor.validate()?;
    }
    let arms = anchors.len();
    let index = |wire: usize| request.wire_to_code.get(wire).copied().unwrap_or(wire);
    let mut polarity = vec![1.0; n];
    coded::derandomize(&mut polarity, &request.randomizer);
    let mut prior = vec![0.0; n];
    let mut channels: Vec<_> = anchors
        .iter()
        .map(|anchor| vec![anchor.clone(); if joint.is_some() { n / arms } else { 1 }])
        .collect();
    let mut report = Report {
        schema: "framelift-turbo-block-v1",
        accepted: false,
        frame_hex: None,
        validation_layers: Vec::new(),
        iterations: Vec::new(),
        stop_reason: "iteration_budget",
        experimental: true,
        channel_history: Vec::new(),
        validated_codeword: None,
    };
    for round in 0..request.iterations {
        let mut extrinsic = vec![0.; n];
        let mut statistics = Vec::with_capacity(arms);
        for arm in 0..arms {
            let samples: Vec<_> = request
                .samples
                .iter()
                .skip(arm)
                .step_by(arms)
                .copied()
                .collect();
            let feedback: Vec<_> = prior.iter().skip(arm).step_by(arms).copied().collect();
            let (detected, stats) = if let Some(joint) = joint {
                soft_sequence::detect_statistics(
                    &samples,
                    &channels[arm],
                    &feedback,
                    joint.segment_symbols,
                )?
            } else {
                (
                    soft_sequence::detect(&samples, &anchors[arm], &feedback)?,
                    Vec::new(),
                )
            };
            for (i, value) in detected.extrinsic_llr.into_iter().enumerate() {
                extrinsic[i * arms + arm] = value;
            }
            statistics.push(stats);
        }
        let mut code_llr = vec![0.0; n];
        for (wire, &llr) in extrinsic.iter().enumerate() {
            code_llr[index(wire)] = llr * polarity[index(wire)];
        }
        if code_llr.iter().all(|x| x.abs() < 1e-12) {
            report.stop_reason = "no_channel_evidence";
            break;
        }
        let decoded = request.code.decode_soft(&code_llr)?;
        let bytes = protocol::bits_to_bytes(&decoded.output_bits, false)?;
        let layers = if decoded.converged {
            request.validator.decode(&bytes).ok()
        } else {
            None
        };
        for (wire, previous) in prior.iter_mut().enumerate() {
            let fresh = decoded.extrinsic_llr[index(wire)] * polarity[index(wire)];
            *previous = (request.damping * fresh + (1.0 - request.damping) * *previous)
                .clamp(-soft_sequence::LLR_LIMIT, soft_sequence::LLR_LIMIT);
        }
        report.iterations.push(Iteration {
            index: round + 1,
            ldpc_iterations: decoded.iterations,
            parity_verified: decoded.converged,
            integrity_verified: layers.is_some(),
            mean_abs_feedback_llr: prior.iter().map(|x| x.abs()).sum::<f64>() / n as f64,
        });
        if let Some(layers) = layers {
            report.accepted = true;
            report.frame_hex = Some(hex::encode(bytes));
            report.validation_layers = layers;
            report.stop_reason = "validated_frame";
            report.validated_codeword = Some(decoded.codeword_bits);
            break;
        }
        if let Some(joint) = joint {
            for arm in 0..arms {
                for (block, stats) in statistics[arm].iter().enumerate() {
                    let start = block * joint.segment_symbols;
                    let end = (start + joint.segment_symbols).min(n / arms);
                    if let Some(fit) = crate::joint_sequence::estimate(
                        stats,
                        &anchors[arm],
                        &channels[arm][start],
                        joint.channel_damping,
                    ) {
                        channels[arm][start..end].fill(fit);
                    }
                }
            }
            report.channel_history.push(
                channels
                    .iter()
                    .flat_map(|arm| arm.iter().step_by(joint.segment_symbols).cloned())
                    .collect(),
            );
        }
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interleaved_bcjr_keeps_channel_memory_and_priors_within_each_arm() {
        let channels = [
            soft_sequence::Channel {
                taps: [0.2, 1., -0.3],
                bias: 0.1,
                noise_variance: 0.2,
            },
            soft_sequence::Channel {
                taps: [-0.4, 0.8, 0.2],
                bias: -0.1,
                noise_variance: 0.3,
            },
        ];
        let samples: Vec<_> = (0..20).map(|i| (i as f64 * 0.7).sin()).collect();
        let llr = channel_llr(&samples, &channels).unwrap();
        for (arm, channel) in channels.iter().enumerate() {
            let data: Vec<_> = samples.iter().skip(arm).step_by(2).copied().collect();
            let oracle = soft_sequence::detect(&data, channel, &[]).unwrap();
            assert_eq!(
                llr.iter().skip(arm).step_by(2).copied().collect::<Vec<_>>(),
                oracle.extrinsic_llr
            );
        }
    }

    // Literal published TC128 generator circulants, independent of H/decoder.
    pub(crate) fn encode(bytes: &[u8; 8]) -> Vec<u8> {
        let rows = [
            0x0e69166bef4c0bc2u64,
            0x7766137ebb248418,
            0xc480feb9cd53a713,
            0x4eaa22fa465eea11,
        ];
        let information = u64::from_be_bytes(*bytes);
        let mut parity = 0u64;
        for bit in 0..64 {
            if information & (1 << (63 - bit)) != 0 {
                for block in 0..4 {
                    let chunk = (rows[bit / 16] >> (48 - block * 16)) as u16;
                    parity ^= u64::from(chunk.rotate_right((bit % 16) as u32)) << (48 - block * 16);
                }
            }
        }
        [bytes.as_slice(), &parity.to_be_bytes()]
            .concat()
            .iter()
            .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
            .collect()
    }

    fn fixture(bad_crc: bool) -> Request {
        let mut bytes = vec![0, 1, 0, 1];
        bytes.extend_from_slice(&crate::space_link::csp_crc32c(&bytes).to_be_bytes());
        if bad_crc {
            bytes[7] ^= 1;
        }
        let bits = encode(bytes.as_slice().try_into().unwrap());
        Request {
            samples: bits.iter().map(|b| 2.0 * f64::from(*b) - 1.0).collect(),
            channel: soft_sequence::Channel {
                taps: [0.0, 1.0, 0.0],
                bias: 0.0,
                noise_variance: 0.05,
            },
            code: LdpcConfig::ccsds_tc128(),
            wire_to_code: Vec::new(),
            randomizer: coded::Randomizer::None,
            validator: coded::FrameValidator::SpaceLink {
                config: crate::space_link::SpaceLinkConfig::CspV1 {
                    crc32: crate::space_link::CspCrc32Mode::RequiredHeaderAndPayload,
                },
            },
            iterations: 4,
            damping: 0.7,
        }
    }

    #[test]
    fn validates_real_code_and_received_crc() {
        let report = decode(&fixture(false)).unwrap();
        assert!(report.accepted);
        assert!(report.frame_hex.is_some());
        assert_eq!(report.iterations.len(), 1);
    }

    #[test]
    fn valid_fec_with_wrong_crc_is_not_telemetry() {
        let report = decode(&fixture(true)).unwrap();
        assert!(!report.accepted);
        assert!(report.frame_hex.is_none());
        assert!(report.iterations.iter().all(|r| !r.integrity_verified));
    }

    #[test]
    fn permutation_and_randomization_round_trip() {
        let mut r = fixture(false);
        r.randomizer = coded::Randomizer::CcsdsTc255;
        let mut code_order = r.samples.clone();
        coded::derandomize(&mut code_order, &r.randomizer);
        r.wire_to_code = (0..128).map(|i| (i * 37 + 11) % 128).collect();
        r.samples = r.wire_to_code.iter().map(|&i| code_order[i]).collect();
        assert!(decode(&r).unwrap().accepted);
        r.wire_to_code[1] = r.wire_to_code[0];
        assert!(decode(&r).is_err());
    }

    #[test]
    fn zero_evidence_and_malformed_parameters_fail_closed() {
        let mut r = fixture(false);
        r.samples.fill(0.0);
        let out = decode(&r).unwrap();
        assert!(!out.accepted);
        assert_eq!(out.stop_reason, "no_channel_evidence");
        r.iterations = 0;
        assert!(decode(&r).is_err());
        r.iterations = 4;
        r.damping = f64::NAN;
        assert!(decode(&r).is_err());
    }
}
