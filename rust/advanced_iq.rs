//! Opt-in multi-mode IQ recovery with explicit coding, complete-boundary soft
//! detection, optional coherent CPM/PSK refinement and checked cancellation.
//! No transmitter payload or oracle channel enters acquisition or estimation.
use crate::{
    coded, generic, input, interference, joint_sequence, recovery_code::CodeProfile, repetition,
    soft_sequence, turbo,
};
use num_complex::Complex64;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::Path};

#[path = "advanced_frontend.rs"]
mod frontend;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct RecoveryOptions {
    /// Independent candidates execute in parallel; reduction remains ordered.
    pub workers: usize,
    pub coherent_cpm: bool,
    pub tracking: Option<crate::recovery_tracking::Config>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub soft_acquisition: Option<crate::acquisition::Config>,
    /// Reliability-ordered FEC hypotheses after the ordinary decoder fails.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub soft_list: Option<crate::recovery_code::ListConfig>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepeatConfig {
    /// Disable combining while retaining identical on-air header parsing.
    pub combine: bool,
    /// Mission contract: immediately after sync, key followed by CRC32C BE.
    pub key_bytes: usize,
    pub maximum_copies: usize,
    pub minimum_correlation: f64,
    pub maximum_gap_symbols: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mission_header: Option<repetition::HeaderLayout>,
}

impl RepeatConfig {
    pub fn header_bytes(&self) -> usize {
        self.mission_header
            .as_ref()
            .map(|h| h.bytes)
            .unwrap_or_else(|| self.key_bytes.saturating_add(4))
    }
    pub fn key(&self, header: &[u8]) -> Result<Vec<u8>, String> {
        match &self.mission_header {
            Some(layout) => layout.key(header),
            None => repetition::key(header).map(<[u8]>::to_vec),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub modulation: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub waveform: Option<crate::advanced_waveform::Waveform>,
    pub symbol_rate: f64,
    pub carrier_centers_hz: Vec<f64>,
    pub residual_carrier_bound_hz: f64,
    pub clock_errors_ppm: Vec<f64>,
    pub phase_bins: usize,
    pub syncword: Vec<u8>,
    pub maximum_sync_hamming: usize,
    pub code: CodeProfile,
    #[serde(default)]
    pub wire_to_code: Vec<usize>,
    #[serde(default)]
    pub randomizer: coded::Randomizer,
    pub validator: coded::FrameValidator,
    pub turbo_iterations: usize,
    pub damping: f64,
    #[serde(default)]
    pub joint: Option<turbo::JointConfig>,
    #[serde(default)]
    pub repetition: Option<RepeatConfig>,
    #[serde(default)]
    pub cancellation: Option<interference::Config>,
    pub maximum_candidates: usize,
    pub maximum_work: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery: Option<RecoveryOptions>,
}

impl Config {
    pub fn coded_bits(&self) -> usize {
        self.code.encoded_bits().unwrap_or(0)
    }

    pub fn validate(&self, rate: u32, count: usize) -> Result<(), String> {
        self.code.validate()?;
        self.validator.validate()?;
        if let Some(repeat) = &self.repetition {
            if !(4..=32).contains(&repeat.key_bytes)
                || !(2..=16).contains(&repeat.maximum_copies)
                || !repeat.minimum_correlation.is_finite()
                || !(0.1..=0.99).contains(&repeat.minimum_correlation)
                || repeat.maximum_gap_symbols < self.coded_bits()
                || repeat.maximum_gap_symbols > 1_000_000
            {
                return Err("invalid protected-repeat profile".into());
            }
            if let Some(layout) = &repeat.mission_header {
                layout.validate()?;
            }
        }
        if let Some(options) = &self.recovery {
            if let Some(acquisition) = &options.soft_acquisition {
                acquisition.validate(self.syncword.len())?;
            }
            if let Some(list) = &options.soft_list {
                list.validate(&self.code)?;
            }
            if options.workers > 64 {
                return Err("recovery workers must be 0..64 (zero selects serial)".into());
            }
            if options.coherent_cpm && !matches!(self.modulation.as_str(), "fsk" | "gfsk" | "gmsk")
            {
                return Err("coherent CPM requires an explicit FSK/GFSK/GMSK IQ waveform".into());
            }
            if options.coherent_cpm {
                let sps = rate as f64 / self.symbol_rate;
                if std::f64::consts::TAU * self.residual_carrier_bound_hz / rate as f64 > 0.2 {
                    return Err(
                        "coherent CPM residual frequency bound must be <=0.2 radians/sample".into(),
                    );
                }
                if (sps - sps.round()).abs() > 1e-9
                    || self.clock_errors_ppm.iter().any(|v| *v != 0.)
                    || self.syncword.len()
                        + self
                            .repetition
                            .as_ref()
                            .map(|r| r.header_bytes() * 8)
                            .unwrap_or(0)
                        + self.coded_bits()
                        > 4096
                {
                    return Err("coherent CPM currently needs integer samples/symbol, zero clock-bank offsets and <=4096 total burst bits".into());
                }
                cpm_index(self)?;
            }
            if options.tracking.is_some() && !crate::advanced_waveform::psk(self) {
                return Err("decoder-assisted synchronization currently requires PSK IQ".into());
            }
            if let Some(tracking) = &options.tracking {
                tracking.validate()?;
            }
        }
        let sps = rate as f64 / self.symbol_rate;
        if !self.symbol_rate.is_finite()
            || !(2.0..=128.0).contains(&sps)
            || !(512..=1_048_576).contains(&count)
            || self.coded_bits() > 4096
        {
            return Err("advanced IQ requires 2..128 samples/symbol, 512..1048576 samples and <=4096 coded bits".into());
        }
        if !(4..=32).contains(&self.phase_bins)
            || self.clock_errors_ppm.is_empty()
            || self.clock_errors_ppm.len() > 8
            || self
                .clock_errors_ppm
                .iter()
                .any(|v| !v.is_finite() || v.abs() > 10000.)
            || self.carrier_centers_hz.is_empty()
            || self.carrier_centers_hz.len() > 8
            || !self.residual_carrier_bound_hz.is_finite()
            || self.residual_carrier_bound_hz <= 0.
            || self.residual_carrier_bound_hz >= rate as f64 / 4.
            || self.carrier_centers_hz.iter().any(|v| {
                !v.is_finite() || v.abs() + self.residual_carrier_bound_hz >= rate as f64 / 2.
            })
        {
            return Err("invalid bounded carrier/timing search bank".into());
        }
        if self
            .clock_errors_ppm
            .iter()
            .any(|p| !(2.0..=128.0).contains(&(sps * (1. + p * 1e-6))))
        {
            return Err("clock bank leaves supported samples/symbol range".into());
        }
        if !(32..=128).contains(&self.syncword.len())
            || self.syncword.iter().any(|v| *v > 1)
            || self.syncword.iter().filter(|b| **b == 1).count() < 8
            || self.syncword.iter().filter(|b| **b == 0).count() < 8
            || self.maximum_sync_hamming > 2
            || !(1..=16).contains(&self.turbo_iterations)
            || !self.damping.is_finite()
            || !(0.0..=1.0).contains(&self.damping)
            || !(1..=128).contains(&self.maximum_candidates)
            || !(1..=5_000_000_000).contains(&self.maximum_work)
        {
            return Err("invalid sync, turbo or aggregate work budget".into());
        }
        crate::advanced_waveform::validate(self, rate)?;
        if !self.wire_to_code.is_empty() {
            let mut mapping = self.wire_to_code.clone();
            mapping.sort_unstable();
            if mapping != (0..self.coded_bits()).collect::<Vec<_>>() {
                return Err("wire_to_code must be a complete permutation".into());
            }
        }
        if let Some(joint) = &self.joint {
            joint.validate()?;
        }
        if let Some(sic) = &self.cancellation {
            sic.validate()?;
        }
        if let Some(repeat) = &self.repetition
            && (!(4..=32).contains(&repeat.key_bytes)
                || !(2..=16).contains(&repeat.maximum_copies)
                || !repeat.minimum_correlation.is_finite()
                || !(0.1..=0.99).contains(&repeat.minimum_correlation)
                || repeat.maximum_gap_symbols < self.coded_bits()
                || repeat.maximum_gap_symbols > 1_000_000)
        {
            return Err("invalid protected-repeat profile".into());
        }
        if let Some(layout) = self
            .repetition
            .as_ref()
            .and_then(|r| r.mission_header.as_ref())
        {
            layout.validate()?;
        }
        let rounds = 1 + self.cancellation.as_ref().map(|c| c.rounds).unwrap_or(0);
        let bank = self.phase_bins * self.clock_errors_ppm.len() * self.carrier_centers_hz.len();
        let frontend_weight = if crate::advanced_waveform::quadrature(self) {
            16
        } else {
            2
        };
        let pulse_weight = match crate::advanced_waveform::resolved(self) {
            crate::advanced_waveform::Waveform::Psk {
                pulse: crate::psk::MatchedFilter::RootRaisedCosine { span_symbols, .. },
            } => (sps * span_symbols as f64).ceil() as u128 + 1,
            _ => 1,
        };
        let visits = if self.modulation == "bpsk_rectangular" {
            bank as u128
        } else {
            bank as u128 * frontend_weight + pulse_weight
        };
        if count as u128 * visits * rounds as u128 > 200_000_000 {
            return Err("IQ frontend bank exceeds 200 million sample visits".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilePlan {
    pub format: generic::InputFormat,
    pub sample_rate_hz: u32,
    pub start_sample: usize,
    pub sample_count: usize,
    pub receiver: Config,
}

#[derive(Clone, Debug, Serialize)]
pub struct Provenance {
    pub round: usize,
    pub lane: String,
    pub start_sample: usize,
    pub end_sample: usize,
    pub carrier_hz: f64,
    pub copies: usize,
}
#[derive(Debug, Serialize)]
pub struct Frame {
    pub frame_hex: String,
    pub validation_layers: Vec<String>,
    pub provenance: Vec<Provenance>,
}
#[derive(Debug, Serialize)]
pub struct Report {
    pub schema: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub modulation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observation_model: Option<&'static str>,
    pub experimental: bool,
    pub frames: Vec<Frame>,
    pub baseline_frames: Vec<String>,
    pub added_frames: Vec<String>,
    pub candidates: usize,
    pub rejected_candidates: usize,
    pub combined_groups: usize,
    pub rejected_groups: usize,
    pub cancellation: Vec<interference::Receipt>,
    pub consumed_work: u64,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub refinement: Vec<crate::recovery_tracking::Report>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub coherent: Vec<serde_json::Value>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub rejected_lanes: Vec<serde_json::Value>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub soft_acquisition: Vec<crate::acquisition::Receipt>,
}

#[derive(Clone)]
struct Candidate {
    soft_sync: Option<crate::acquisition::Score>,
    samples: Vec<f64>,
    channel: soft_sequence::Channel,
    header: Vec<u8>,
    start: f64,
    step: f64,
    carrier: f64,
    score: f64,
    /// Empty retains the byte-compatible legacy single-arm path.
    branch_channels: Vec<soft_sequence::Channel>,
    geometry: Option<crate::advanced_waveform::Geometry>,
    channel_cache: std::sync::OnceLock<Result<Vec<f64>, String>>,
}
impl Candidate {
    fn bounds(&self, c: &Config) -> (usize, usize) {
        if let Some(g) = &self.geometry {
            return crate::advanced_waveform::bounds(
                c,
                g,
                c.syncword.len() + self.header.len() * 8 + c.coded_bits(),
            );
        }
        (
            self.start.floor() as usize,
            (self.start
                + (c.syncword.len() + self.header.len() * 8 + c.coded_bits()) as f64 * self.step)
                .ceil() as usize,
        )
    }
    fn decode(
        &self,
        request: &turbo::Request,
        joint: Option<&turbo::JointConfig>,
    ) -> Result<turbo::Report, String> {
        if self.branch_channels.len() == 2 {
            turbo::decode_branched(request, &self.branch_channels, joint)
        } else if let Some(joint) = joint {
            turbo::decode_joint(request, joint)
        } else {
            turbo::decode(request)
        }
    }
    fn request(&self, c: &Config) -> turbo::Request {
        turbo::Request {
            samples: self.samples.clone(),
            channel: self.channel.clone(),
            code: c.code.as_ldpc().expect("LDPC request only").clone(),
            wire_to_code: c.wire_to_code.clone(),
            randomizer: c.randomizer.clone(),
            validator: c.validator.clone(),
            iterations: c.turbo_iterations,
            damping: c.damping,
        }
    }

    fn run(
        &self,
        c: &Config,
        iterations: usize,
        joint: Option<&turbo::JointConfig>,
    ) -> Result<turbo::Report, String> {
        if c.code.as_ldpc().is_some() {
            let mut request = self.request(c);
            request.iterations = iterations;
            let mut report = self.decode(&request, joint)?;
            if !report.accepted
                && let Some(config) = c.recovery.as_ref().and_then(|r| r.soft_list.as_ref())
            {
                let input = self.code_channel_llr(c)?;
                let decoded = c.code.decode_list(&input, &c.validator, config)?;
                apply_recovery_output(&mut report, decoded);
            }
            return Ok(report);
        }
        self.run_general(c, iterations, joint)
    }

    fn channels(&self) -> &[soft_sequence::Channel] {
        if self.branch_channels.is_empty() {
            std::slice::from_ref(&self.channel)
        } else {
            &self.branch_channels
        }
    }

    fn channel_llr(&self, c: &Config) -> Result<&[f64], String> {
        self.channel_cache
            .get_or_init(|| {
                if c.code.as_ldpc().is_some() {
                    return turbo::channel_llr(&self.samples, self.channels());
                }
                let channels = self.channels();
                let mut llr = vec![0.; self.samples.len()];
                for (arm, channel) in channels.iter().enumerate() {
                    let data: Vec<_> = self
                        .samples
                        .iter()
                        .skip(arm)
                        .step_by(channels.len())
                        .copied()
                        .collect();
                    let output = soft_sequence::detect_complete(&data, channel, &[])?;
                    for (i, value) in output.extrinsic_llr.into_iter().enumerate() {
                        llr[i * channels.len() + arm] = value;
                    }
                }
                Ok(llr)
            })
            .as_deref()
            .map_err(Clone::clone)
    }

    fn code_channel_llr(&self, c: &Config) -> Result<Vec<f64>, String> {
        let mut code_llr = vec![0.; c.coded_bits()];
        for (wire, &llr) in self.channel_llr(c)?.iter().enumerate() {
            code_llr[c.wire_to_code.get(wire).copied().unwrap_or(wire)] = llr;
        }
        coded::derandomize(&mut code_llr, &c.randomizer);
        Ok(code_llr)
    }

    fn refine(
        &self,
        iq: &[Complex64],
        rate: u32,
        c: &Config,
        config: &crate::recovery_tracking::Config,
    ) -> Result<Option<(Candidate, crate::recovery_tracking::Report)>, String> {
        let code_llr = self.code_channel_llr(c)?;
        let output = decode_code(c, &code_llr)?;
        let extrinsic = if let Some(accepted) = output.accepted {
            // Independent received integrity has already passed; these hard
            // bits may guide a data-aided fit, but are never repeat evidence.
            accepted
                .validated_codeword
                .iter()
                .map(|b| if *b == 1 { 100. } else { -100. })
                .collect()
        } else if let Some(extrinsic) = output.extrinsic_llr {
            extrinsic
        } else {
            return Ok(None);
        };
        let mut polarity = vec![1.; c.coded_bits()];
        coded::derandomize(&mut polarity, &c.randomizer);
        let mut means: Vec<_> = c.syncword.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
        means.extend(
            self.header
                .iter()
                .flat_map(|b| (0..8).rev().map(move |i| 2. * f64::from((b >> i) & 1) - 1.)),
        );
        means.extend((0..c.coded_bits()).map(|wire| {
            let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
            (0.5 * extrinsic[index] * polarity[index]).tanh()
        }));
        let g = crate::recovery_tracking::Geometry {
            start_sample: self.start,
            samples_per_symbol: self.step,
            carrier_hz: self.carrier,
            carrier_drift_hz_per_s: 0.,
            carrier_reference_sample: self.start,
            delayed_q: self.geometry.as_ref().is_none_or(|g| g.delayed_q),
            conjugated: self.geometry.as_ref().is_some_and(|g| g.conjugated),
        };
        let crate::advanced_waveform::Waveform::Psk { pulse } =
            crate::advanced_waveform::resolved(c)
        else {
            return Err("tracking needs PSK".into());
        };
        let mode = if c.modulation == "bpsk_rectangular" {
            "bpsk"
        } else {
            &c.modulation
        };
        let mut receipt =
            crate::recovery_tracking::refine_psk(iq, rate, mode, &pulse, &g, &means, config)?;
        if !receipt.accepted {
            return Ok(Some((self.clone(), receipt)));
        }
        let corrected = crate::recovery_tracking::corrected_iq(iq, rate, &receipt)?;
        let geometry = crate::advanced_waveform::Geometry {
            start: receipt.geometry.start_sample,
            step: receipt.geometry.samples_per_symbol,
            carrier: receipt.geometry.carrier_hz,
            delayed_q: receipt.geometry.delayed_q,
            conjugated: receipt.geometry.conjugated,
        };
        // corrected_iq already removes CFO/drift/gain; geometry's carrier is
        // provenance only here and must not be removed twice.
        match frontend::psk_at(&corrected, rate, c, &geometry) {
            Ok(refined) => Ok(Some((refined, receipt))),
            Err(_) => {
                receipt.accepted = false;
                receipt.reason = "refined_observation_rejected";
                Ok(Some((self.clone(), receipt)))
            }
        }
    }

    fn run_general(
        &self,
        c: &Config,
        iterations: usize,
        joint: Option<&turbo::JointConfig>,
    ) -> Result<turbo::Report, String> {
        let n = c.coded_bits();
        let anchors = self.channels();
        let arms = anchors.len();
        let mut channels: Vec<_> = anchors.iter().map(|h| vec![h.clone(); n / arms]).collect();
        let mut prior = vec![0.; n];
        let mut polarity = vec![1.; n];
        coded::derandomize(&mut polarity, &c.randomizer);
        let mut report = empty_attempt();
        for pass in 0..iterations {
            let mut wire = vec![0.; n];
            let mut statistics = Vec::new();
            for arm in 0..arms {
                let data: Vec<_> = self
                    .samples
                    .iter()
                    .skip(arm)
                    .step_by(arms)
                    .copied()
                    .collect();
                let feedback: Vec<_> = prior.iter().skip(arm).step_by(arms).copied().collect();
                let (output, stats) = if let Some(joint) = joint {
                    soft_sequence::detect_complete_statistics(
                        &data,
                        &channels[arm],
                        &feedback,
                        joint.segment_symbols,
                    )?
                } else {
                    (
                        soft_sequence::detect_complete(&data, &anchors[arm], &feedback)?,
                        Vec::new(),
                    )
                };
                for (i, v) in output.extrinsic_llr.into_iter().enumerate() {
                    wire[i * arms + arm] = v;
                }
                statistics.push(stats);
            }
            let mut input = vec![0.; n];
            for (wire, value) in wire.into_iter().enumerate() {
                let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
                input[index] = value * polarity[index];
            }
            let decoded = decode_code(c, &input)?;
            report.iterations.push(turbo::Iteration {
                index: pass + 1,
                ldpc_iterations: 0,
                parity_verified: decoded.parity_verified,
                integrity_verified: decoded.accepted.is_some(),
                mean_abs_feedback_llr: decoded
                    .extrinsic_llr
                    .as_ref()
                    .map(|x| x.iter().map(|v| v.abs()).sum::<f64>() / n as f64)
                    .unwrap_or(0.),
            });
            if let Some(accepted) = decoded.accepted {
                report.accepted = true;
                report.frame_hex = Some(hex::encode(accepted.bytes));
                report.validation_layers = accepted.validation_layers;
                report.validated_codeword = Some(accepted.validated_codeword);
                report.stop_reason = "validated_frame";
                break;
            }
            let Some(extrinsic) = decoded.extrinsic_llr else {
                report.stop_reason = "code_has_no_soft_feedback";
                break;
            };
            for (wire, old) in prior.iter_mut().enumerate() {
                let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
                *old = (c.damping * extrinsic[index] * polarity[index] + (1. - c.damping) * *old)
                    .clamp(-100., 100.);
            }
            if let Some(joint) = joint {
                for arm in 0..arms {
                    for (block, stats) in statistics[arm].iter().enumerate() {
                        let start = block * joint.segment_symbols;
                        let end = (start + joint.segment_symbols).min(n / arms);
                        if let Some(fit) = joint_sequence::estimate(
                            stats,
                            &anchors[arm],
                            &channels[arm][start],
                            joint.channel_damping,
                        ) {
                            channels[arm][start..end].fill(fit);
                        }
                    }
                }
            }
        }
        Ok(report)
    }

    fn coherent(
        &self,
        iq: &[Complex64],
        rate: u32,
        c: &Config,
    ) -> Result<(Option<turbo::Report>, serde_json::Value), String> {
        let crate::advanced_waveform::Waveform::Fsk { gaussian_bt, .. } =
            crate::advanced_waveform::resolved(c)
        else {
            return Err("CPM waveform mismatch".into());
        };
        let (numerator, denominator) = cpm_index(c)?;
        let mut known: Vec<_> = c.syncword.iter().copied().map(Some).collect();
        known.extend(
            self.header
                .iter()
                .flat_map(|b| (0..8).rev().map(move |i| Some((b >> i) & 1))),
        );
        let prefix = known.len();
        known.resize(prefix + c.coded_bits(), None);
        let sps = self.step.round() as usize;
        let mut received = Vec::with_capacity(known.len() * sps);
        for n in 0..known.len() * sps {
            let t = self.start + n as f64;
            let i = t.floor() as usize;
            if i >= iq.len() || (i + 1 == iq.len() && t != i as f64) {
                return Err("CPM window extends past input".into());
            }
            let z = if i + 1 == iq.len() {
                iq[i]
            } else {
                iq[i] + (iq[i + 1] - iq[i]) * (t - i as f64)
            };
            received.push(
                z * Complex64::from_polar(
                    1.,
                    -std::f64::consts::TAU * self.carrier * t / rate as f64,
                ),
            );
        }
        let config = crate::coherent_cpm::CoherentCpmConfig {
            samples_per_symbol: sps,
            modulation_index_numerator: numerator,
            modulation_index_denominator: denominator,
            gaussian_bt,
            noise_variance: 0.1,
            max_carrier_offset_radians_per_sample: std::f64::consts::TAU
                * c.residual_carrier_bound_hz
                / rate as f64,
            ..Default::default()
        };
        // Fit the received pilot once; build metrics only with its final noise
        // estimate. This preserves the former two-prepare arithmetic exactly.
        let prepared = match crate::coherent_cpm::prepare_with_training_noise(
            &received, &known, &config, 1e-6,
        ) {
            Ok(value) => value,
            Err(error) => {
                return Ok((
                    None,
                    serde_json::json!({"start_sample":self.start,"accepted":false,"reason":error}),
                ));
            }
        };
        let mut prior = vec![0.; known.len()];
        let mut polarity = vec![1.; c.coded_bits()];
        coded::derandomize(&mut polarity, &c.randomizer);
        let mut report = empty_attempt();
        let mut diagnostic = serde_json::Value::Null;
        for pass in 0..c.turbo_iterations {
            let detected = prepared.detect(&prior)?;
            diagnostic = serde_json::to_value(&detected.diagnostics).map_err(|e| e.to_string())?;
            let mut input = vec![0.; c.coded_bits()];
            for (wire, value) in detected.extrinsic_llr[prefix..].iter().enumerate() {
                let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
                input[index] = value * polarity[index];
            }
            let decoded = decode_code(c, &input)?;
            if let Some(accepted) = decoded.accepted {
                report.accepted = true;
                report.frame_hex = Some(hex::encode(accepted.bytes));
                report.validation_layers = accepted.validation_layers;
                report.validated_codeword = Some(accepted.validated_codeword);
                report.stop_reason = "validated_frame";
                break;
            }
            let Some(extrinsic) = decoded.extrinsic_llr else {
                break;
            };
            for wire in 0..c.coded_bits() {
                let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
                prior[prefix + wire] = (c.damping * extrinsic[index] * polarity[index]
                    + (1. - c.damping) * prior[prefix + wire])
                    .clamp(-100., 100.);
            }
            let _ = pass;
        }
        let receipt = serde_json::json!({"start_sample":self.start,"accepted":report.accepted,"diagnostics":diagnostic});
        Ok((Some(report), receipt))
    }
}

fn cpm_index(c: &Config) -> Result<(u8, u8), String> {
    let crate::advanced_waveform::Waveform::Fsk { deviation_hz, .. } =
        crate::advanced_waveform::resolved(c)
    else {
        return Err("CPM requires FSK waveform".into());
    };
    let h = 2. * deviation_hz / c.symbol_rate;
    for q in 1..=16u8 {
        let p = (h * f64::from(q)).round();
        if (1. ..=16.).contains(&p) && (p / f64::from(q) - h).abs() < 1e-9 && h <= 2. {
            return Ok((p as u8, q));
        }
    }
    Err("coherent CPM requires rational modulation index p/q with p,q in 1..16 and h<=2".into())
}

fn candidate_cpm_sps(rate: u32, c: &Config) -> u64 {
    (rate as f64 / c.symbol_rate).ceil() as u64
}

fn empty_attempt() -> turbo::Report {
    turbo::Report {
        schema: "framelift-recovery-attempt-v1",
        accepted: false,
        frame_hex: None,
        validation_layers: Vec::new(),
        iterations: Vec::new(),
        stop_reason: "iteration_budget",
        experimental: true,
        channel_history: Vec::new(),
        validated_codeword: None,
    }
}

fn decode_code(c: &Config, input: &[f64]) -> Result<crate::recovery_code::RecoveryOutput, String> {
    if let Some(list) = c.recovery.as_ref().and_then(|r| r.soft_list.as_ref()) {
        c.code.decode_list(input, &c.validator, list)
    } else {
        c.code.decode(input, &c.validator)
    }
}

fn apply_recovery_output(report: &mut turbo::Report, output: crate::recovery_code::RecoveryOutput) {
    let list_accepted = output.accepted_hypothesis.is_some();
    if let Some(accepted) = output.accepted {
        report.accepted = true;
        report.frame_hex = Some(hex::encode(accepted.bytes));
        report.validation_layers = accepted.validation_layers;
        report.validated_codeword = Some(accepted.validated_codeword);
        report.stop_reason = if list_accepted {
            "validated_soft_list_frame"
        } else {
            "validated_fec_fallback_frame"
        };
    }
}

fn acquire(
    iq: &[Complex64],
    rate: u32,
    c: &Config,
    search: &mut Option<crate::acquisition::Search<'_>>,
) -> Result<Vec<Candidate>, String> {
    if c.modulation != "bpsk_rectangular" {
        return frontend::acquire(iq, rate, c, search);
    }
    let mut found = Vec::new();
    let header_bits = c
        .repetition
        .as_ref()
        .map(|r| r.header_bytes() * 8)
        .unwrap_or(0);
    let width = c.syncword.len() + header_bits + c.coded_bits();
    for center in &c.carrier_centers_hz {
        let mixed: Vec<_> = iq
            .iter()
            .enumerate()
            .map(|(i, z)| {
                z * Complex64::from_polar(
                    1.,
                    -std::f64::consts::TAU * center * i as f64 / rate as f64,
                )
            })
            .collect();
        let (corrected, residual, _) = crate::physical::carrier_fft(
            &mixed,
            rate as f64,
            2,
            Some(c.residual_carrier_bound_hz),
        )?;
        let carrier = center + residual;
        let mut prefix = Vec::with_capacity(corrected.len() + 1);
        prefix.push(Complex64::new(0., 0.));
        for z in &corrected {
            prefix.push(*prefix.last().unwrap() + z);
        }
        for ppm in &c.clock_errors_ppm {
            let step = rate as f64 / c.symbol_rate * (1. + ppm * 1e-6);
            for phase in 0..c.phase_bins {
                let offset = phase as f64 * step / c.phase_bins as f64;
                let count = ((iq.len() as f64 - offset) / step).floor().max(0.) as usize;
                if count < width {
                    continue;
                }
                let symbols: Vec<_> = (0..count)
                    .map(|i| {
                        let begin = (offset + i as f64 * step).ceil() as usize;
                        let end = ((offset + (i + 1) as f64 * step).ceil() as usize).min(iq.len());
                        (prefix[end] - prefix[begin]) / (end - begin) as f64
                    })
                    .collect();
                let pattern = c
                    .syncword
                    .iter()
                    .fold(0u128, |word, bit| (word << 1) | u128::from(*bit));
                let mask = if c.syncword.len() == 128 {
                    u128::MAX
                } else {
                    (1u128 << c.syncword.len()) - 1
                };
                let mut observed = symbols[..c.syncword.len()]
                    .iter()
                    .fold(0u128, |word, z| (word << 1) | u128::from(z.re > 0.));
                for start in 0..=count - width {
                    // Both BPSK polarity ambiguities, without expected payload.
                    if start > 0 {
                        observed = ((observed << 1) & mask)
                            | u128::from(symbols[start + c.syncword.len() - 1].re > 0.);
                    }
                    let distance = (observed ^ pattern).count_ones() as usize;
                    let errors = distance.min(c.syncword.len() - distance);
                    let soft_sync = if errors <= c.maximum_sync_hamming {
                        None
                    } else if let Some(search) = search {
                        let Some(score) =
                            search.complex(&symbols[start..start + c.syncword.len()], errors)?
                        else {
                            continue;
                        };
                        Some(score)
                    } else {
                        continue;
                    };
                    let correlation = c
                        .syncword
                        .iter()
                        .enumerate()
                        .map(|(j, b)| symbols[start + j] * (2. * f64::from(*b) - 1.))
                        .sum::<Complex64>();
                    if correlation.norm_sqr() < 1e-12 {
                        continue;
                    }
                    let rotate = Complex64::from_polar(1., -correlation.arg());
                    let pilot: Vec<_> = symbols[start..start + c.syncword.len()]
                        .iter()
                        .map(|z| (z * rotate).re)
                        .collect();
                    let Ok(channel) = joint_sequence::pilot(&pilot, &c.syncword) else {
                        continue;
                    };
                    if channel.taps[1] <= 0. {
                        continue;
                    }
                    let score = channel.taps[1] * channel.taps[1] / channel.noise_variance;
                    if score < 1.0 {
                        continue;
                    }
                    let payload = start + c.syncword.len() + header_bits;
                    let header: Vec<u8> = symbols[start + c.syncword.len()..payload]
                        .as_chunks::<8>()
                        .0
                        .iter()
                        .map(|chunk| {
                            chunk
                                .iter()
                                .fold(0u8, |byte, z| (byte << 1) | u8::from((z * rotate).re > 0.))
                        })
                        .collect();
                    if header_bits > 0 && c.repetition.as_ref().unwrap().key(&header).is_err() {
                        continue;
                    }
                    found.push(Candidate {
                        soft_sync: soft_sync.clone(),
                        samples: symbols[payload..payload + c.coded_bits()]
                            .iter()
                            .map(|z| (z * rotate).re)
                            .collect(),
                        channel,
                        header,
                        start: offset + start as f64 * step,
                        step,
                        carrier,
                        score,
                        branch_channels: Vec::new(),
                        geometry: None,
                        channel_cache: std::sync::OnceLock::new(),
                    });
                    if soft_sync.is_some() {
                        search.as_mut().unwrap().admit_candidate()?;
                    }
                    if found.len()
                        - search
                            .as_ref()
                            .map(|s| s.receipt.raw_candidates)
                            .unwrap_or(0)
                        > c.maximum_candidates * 256
                    {
                        return Err("raw acquisition candidate budget exceeded".into());
                    }
                }
            }
        }
    }
    select_candidates(found, c, search)
}

fn select_candidates(
    found: Vec<Candidate>,
    c: &Config,
    search: &mut Option<crate::acquisition::Search<'_>>,
) -> Result<Vec<Candidate>, String> {
    let (mut found, mut soft): (Vec<_>, Vec<_>) = found
        .into_iter()
        .partition(|candidate| candidate.soft_sync.is_none());
    found.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| a.start.total_cmp(&b.start))
            .then_with(|| a.carrier.total_cmp(&b.carrier))
    });
    let mut selected: Vec<Candidate> = Vec::new();
    for candidate in found {
        // Signal-only representative of one on-air burst. Never combine phase
        // variants. Preserve distinct carriers for simultaneous transmissions.
        if selected.iter().any(|old| {
            (old.start - candidate.start).abs() < candidate.step * 2.0
                && (old.carrier - candidate.carrier).abs() < c.symbol_rate * 0.01
        }) {
            continue;
        }
        selected.push(candidate);
        if selected.len() > c.maximum_candidates {
            return Err("acquired burst budget exceeded".into());
        }
    }
    // The legacy candidate set is frozen first. Soft alternatives neither
    // replace its representatives nor participate in repeat/SIC decisions.
    soft.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| a.start.total_cmp(&b.start))
            .then_with(|| a.carrier.total_cmp(&b.carrier))
    });
    let mut supplemental: Vec<Candidate> = Vec::new();
    for candidate in soft {
        // Soft acquisition supplements missing bursts, not weaker timing
        // duplicates of a burst already admitted by the hard marker. Apply the
        // same signal-only identity guard across both sets, before any CRC/FEC.
        if selected.iter().chain(&supplemental).any(|old| {
            (old.start - candidate.start).abs() < candidate.step * 2.
                && (old.carrier - candidate.carrier).abs() < c.symbol_rate * 0.01
        }) {
            continue;
        }
        search.as_mut().unwrap().select(
            candidate.start,
            candidate.carrier,
            candidate.soft_sync.clone().unwrap(),
        )?;
        supplemental.push(candidate);
    }
    selected.extend(supplemental);
    selected.sort_by(|a, b| {
        a.start
            .total_cmp(&b.start)
            .then_with(|| a.carrier.total_cmp(&b.carrier))
    });
    Ok(selected)
}

fn charge(work: &mut u64, c: &Config, passes: usize) -> Result<(), String> {
    let cost = if let Some(code) = c.code.as_ldpc() {
        let edges = code.checks.iter().map(Vec::len).sum::<usize>() as u64;
        (edges * 6 + code.codeword_bits as u64 * 64) * code.max_iterations as u64
    } else {
        // Bounded 64-state K7 forward/backward recursion, including outer RS.
        c.coded_bits() as u64 * 4096
    } * passes as u64;
    *work = work
        .checked_add(cost)
        .ok_or("aggregate IQ decoder work overflow")?;
    if *work > c.maximum_work {
        return Err("aggregate IQ decoder work exhausted; no partial success".into());
    }
    Ok(())
}

fn charge_list(work: &mut u64, c: &Config, invocations: usize) -> Result<(), String> {
    let Some(list) = c.recovery.as_ref().and_then(|r| r.soft_list.as_ref()) else {
        return Ok(());
    };
    let cost = list
        .work_per_call(&c.code)?
        .checked_mul(invocations as u64)
        .ok_or("aggregate soft-list work overflow")?;
    *work = work
        .checked_add(cost)
        .ok_or("aggregate soft-list work overflow")?;
    if *work > c.maximum_work {
        return Err("aggregate soft-list work exhausted; no partial success".into());
    }
    Ok(())
}

fn record(
    frames: &mut BTreeMap<String, Frame>,
    report: &turbo::Report,
    provenance: Provenance,
    c: &Config,
) {
    if let Some(hex) = &report.frame_hex {
        let frame = frames.entry(hex.clone()).or_insert_with(|| Frame {
            frame_hex: hex.clone(),
            validation_layers: report
                .validation_layers
                .iter()
                .cloned()
                .chain(c.code.as_ldpc().map(|_| "ldpc_syndrome_verified".into()))
                .collect(),
            provenance: Vec::new(),
        });
        frame.provenance.push(provenance);
    }
}

pub fn decode(
    iq: &[Complex64],
    rate: u32,
    c: &Config,
    source_sha256: &str,
) -> Result<Report, String> {
    c.validate(rate, iq.len())?;
    if source_sha256.len() != 64
        || !source_sha256.bytes().all(|v| v.is_ascii_hexdigit())
        || iq
            .iter()
            .any(|z| !z.re.is_finite() || !z.im.is_finite() || z.norm() > 1e6)
    {
        return Err("invalid IQ or source identity".into());
    }
    let scale = (iq.iter().map(|z| z.norm_sqr()).sum::<f64>() / iq.len() as f64).sqrt();
    let mut residual: Vec<_> = iq.iter().map(|z| z / scale.max(1e-12)).collect();
    let extended = c.code.as_ldpc().is_none()
        || c.recovery.is_some()
        || c.repetition
            .as_ref()
            .is_some_and(|r| r.mission_header.is_some());
    let mut report = Report {
        schema: if extended {
            "framelift-advanced-iq-v3"
        } else if c.modulation == "bpsk_rectangular" {
            "framelift-advanced-iq-v1"
        } else {
            "framelift-advanced-iq-v2"
        },
        modulation: (extended || c.modulation != "bpsk_rectangular").then(|| c.modulation.clone()),
        observation_model: match c.modulation.as_str() {
            "bpsk_rectangular" if !extended => None,
            "bpsk_rectangular" => Some("real_three_tap_gaussian_surrogate"),
            "bpsk" => Some("real_three_tap_gaussian_surrogate"),
            "qpsk" | "oqpsk" => Some("independent_iq_three_tap_gaussian_surrogate"),
            "afsk" => Some("tone_energy_three_tap_gaussian_surrogate"),
            _ => Some("frequency_discriminator_three_tap_gaussian_surrogate"),
        },
        experimental: true,
        frames: Vec::new(),
        baseline_frames: Vec::new(),
        added_frames: Vec::new(),
        candidates: 0,
        rejected_candidates: 0,
        combined_groups: 0,
        rejected_groups: 0,
        cancellation: Vec::new(),
        consumed_work: 0,
        refinement: Vec::new(),
        coherent: Vec::new(),
        rejected_lanes: Vec::new(),
        soft_acquisition: Vec::new(),
    };
    let mut frames = BTreeMap::new();
    let mut cancelled = Vec::<(usize, String)>::new();
    let rounds = c.cancellation.as_ref().map(|s| s.rounds).unwrap_or(0);
    let workers = c.recovery.as_ref().map(|r| r.workers.max(1)).unwrap_or(1);
    let pool = (workers > 1)
        .then(|| rayon::ThreadPoolBuilder::new().num_threads(workers).build())
        .transpose()
        .map_err(|e| e.to_string())?;
    for round in 0..=rounds {
        let mut acquisition_config = c
            .recovery
            .as_ref()
            .and_then(|r| r.soft_acquisition.as_ref())
            .cloned();
        if let Some(config) = &mut acquisition_config {
            let remaining = c.maximum_work.saturating_sub(report.consumed_work);
            if remaining == 0 {
                return Err("aggregate soft acquisition work budget exhausted".into());
            }
            config.maximum_work = config.maximum_work.min(remaining);
        }
        let mut search = acquisition_config
            .as_ref()
            .map(|config| crate::acquisition::Search::new(config, &c.syncword, round))
            .transpose()?;
        let candidates = acquire(&residual, rate, c, &mut search)?;
        if let Some(search) = search {
            report.consumed_work = report
                .consumed_work
                .checked_add(search.receipt.consumed_work)
                .ok_or("soft acquisition work overflow")?;
            if report.consumed_work > c.maximum_work {
                return Err("aggregate soft acquisition work budget exceeded".into());
            }
            report.soft_acquisition.push(search.receipt);
        }
        report.candidates += candidates.len();
        for _ in &candidates {
            charge(&mut report.consumed_work, c, 1)?;
            let mut list_invocations = 1;
            if c.turbo_iterations > 1 {
                charge(&mut report.consumed_work, c, c.turbo_iterations)?;
                list_invocations += c.turbo_iterations;
            }
            if c.joint.is_some() {
                charge(&mut report.consumed_work, c, c.turbo_iterations)?;
                list_invocations += c.turbo_iterations;
            }
            if let Some(tracking) = c.recovery.as_ref().and_then(|r| r.tracking.as_ref()) {
                charge(&mut report.consumed_work, c, 1 + c.turbo_iterations)?;
                list_invocations += 1 + c.turbo_iterations;
                report.consumed_work = report
                    .consumed_work
                    .checked_add(tracking.maximum_work)
                    .ok_or("tracking work overflow")?;
                if report.consumed_work > c.maximum_work {
                    return Err("aggregate tracking work exhausted".into());
                }
            }
            if c.recovery.as_ref().is_some_and(|r| r.coherent_cpm) {
                charge(&mut report.consumed_work, c, c.turbo_iterations)?;
                list_invocations += c.turbo_iterations;
                let cost = (c.syncword.len()
                    + c.coded_bits()
                    + c.repetition
                        .as_ref()
                        .map(|r| r.header_bytes() * 8)
                        .unwrap_or(0)) as u64
                    * 512
                    * (candidate_cpm_sps(rate, c) * 8 + (c.turbo_iterations as u64 + 1) * 32);
                report.consumed_work = report
                    .consumed_work
                    .checked_add(cost)
                    .ok_or("CPM work overflow")?;
                if report.consumed_work > c.maximum_work {
                    return Err("aggregate coherent CPM work exhausted".into());
                }
            }
            charge_list(&mut report.consumed_work, c, list_invocations)?;
        }
        let evaluate = |candidate: &Candidate| -> Result<_, String> {
            let mut rejected = Vec::new();
            let mut optional = |result: Result<_, String>, lane: &str| -> Result<_, String> {
                match result {
                    Ok(value) => Ok(Some(value)),
                    Err(error)
                        if error.contains("work")
                            || error.contains("budget")
                            || error.contains("overflow") =>
                    {
                        Err(error)
                    }
                    Err(error) => {
                        rejected.push(serde_json::json!({"lane":lane,"reason":error,"start_sample":candidate.start,"round":round}));
                        Ok(None)
                    }
                }
            };
            let single = candidate.run(c, 1, None)?;
            let fixed = if c.turbo_iterations > 1 {
                // Successful single-pass fixed-channel decoding would stop at
                // exactly the same first iteration. Reuse that exact report.
                Some(if single.accepted {
                    single.clone()
                } else {
                    candidate.run(c, c.turbo_iterations, None)?
                })
            } else {
                None
            };
            let joint = c
                .joint
                .as_ref()
                .map(|joint| candidate.run(c, c.turbo_iterations, Some(joint)))
                .transpose()?;
            let refined =
                if let Some(config) = c.recovery.as_ref().and_then(|r| r.tracking.as_ref()) {
                    optional(
                        candidate.refine(&residual, rate, c, config),
                        "decoder_assisted_sync",
                    )?
                    .flatten()
                } else {
                    None
                };
            let tracking = refined
                .map(|(candidate, receipt)| -> Result<_, String> {
                    let bounds = candidate.bounds(c);
                    let result = if receipt.accepted {
                        match candidate.run(c, c.turbo_iterations, c.joint.as_ref()) {
                            Ok(value) => Some(value),
                            Err(error) if error.contains("work") || error.contains("budget") || error.contains("overflow") => return Err(error),
                            Err(error) => {
                                rejected.push(serde_json::json!({"lane":"refined_decode","reason":error,"start_sample":candidate.start,"round":round}));
                                None
                            }
                        }
                    } else {
                        None
                    };
                    Ok((result, receipt, bounds))
                })
                .transpose()?;
            let coherent = if c.recovery.as_ref().is_some_and(|r| r.coherent_cpm) {
                match candidate.coherent(&residual, rate, c) {
                    Ok(value) => Some(value),
                    Err(error)
                        if error.contains("work")
                            || error.contains("budget")
                            || error.contains("overflow") =>
                    {
                        return Err(error);
                    }
                    Err(error) => {
                        rejected.push(serde_json::json!({"lane":"coherent_cpm","reason":error,"start_sample":candidate.start,"round":round}));
                        None
                    }
                }
            } else {
                None
            };
            Ok((single, fixed, joint, tracking, coherent, rejected))
        };
        let evaluations: Vec<_> = if let Some(pool) = &pool {
            pool.install(|| {
                candidates
                    .par_iter()
                    .map(evaluate)
                    .collect::<Result<Vec<_>, _>>()
            })?
        } else {
            candidates
                .iter()
                .map(evaluate)
                .collect::<Result<Vec<_>, _>>()?
        };
        let mut reconstructions = Vec::new();
        let mut groups: BTreeMap<Vec<u8>, Vec<(repetition::Copy, Candidate)>> = BTreeMap::new();
        for (candidate, (mut best, fixed, updated, tracking, coherent, rejected)) in
            candidates.iter().zip(evaluations)
        {
            report.rejected_lanes.extend(rejected);
            let (start, end) = candidate.bounds(c);
            let provenance = |lane: &str, copies| Provenance {
                round,
                lane: if candidate.soft_sync.is_some() {
                    format!("soft_acquisition/{lane}")
                } else {
                    lane.into()
                },
                start_sample: start,
                end_sample: end,
                carrier_hz: candidate.carrier,
                copies,
            };
            if round == 0
                && candidate.soft_sync.is_none()
                && let Some(hex) = &best.frame_hex
            {
                report.baseline_frames.push(hex.clone());
            }
            record(&mut frames, &best, provenance("single_pass", 1), c);
            if let Some(fixed) = fixed {
                record(&mut frames, &fixed, provenance("turbo_fixed", 1), c);
                if !best.accepted && fixed.accepted {
                    best = fixed;
                }
            }
            if let Some(updated) = updated {
                record(
                    &mut frames,
                    &updated,
                    provenance("turbo_varying_channel", 1),
                    c,
                );
                if !best.accepted && updated.accepted {
                    best = updated;
                }
            }
            if let Some((tracked, receipt, bounds)) = tracking {
                if let Some(tracked) = tracked {
                    record(
                        &mut frames,
                        &tracked,
                        Provenance {
                            start_sample: bounds.0,
                            end_sample: bounds.1,
                            carrier_hz: receipt.geometry.carrier_hz,
                            ..provenance("decoder_assisted_sync", 1)
                        },
                        c,
                    );
                    // The original acquisition geometry is not a reconstruction
                    // witness for refined samples: never feed this lane to SIC.
                }
                report.refinement.push(receipt);
            }
            if let Some((coherent, receipt)) = coherent {
                if let Some(coherent) = coherent {
                    record(&mut frames, &coherent, provenance("coherent_cpm", 1), c);
                }
                report.coherent.push(receipt);
            }
            if !best.accepted {
                report.rejected_candidates += 1;
            }
            if candidate.soft_sync.is_none()
                && let Some(bits) = best.validated_codeword
            {
                reconstructions.push((candidate.clone(), best.frame_hex.unwrap(), bits));
            }
            // Repetition uses raw-channel extrinsic only, never decoder APP or
            // turbo feedback; otherwise the same parity evidence is counted twice.
            if c.repetition.as_ref().is_some_and(|r| r.combine)
                && round == 0
                && candidate.soft_sync.is_none()
            {
                let llr = candidate.channel_llr(c)?;
                let mut code_llr = vec![0.; llr.len()];
                for (wire, &value) in llr.iter().enumerate() {
                    code_llr[c.wire_to_code.get(wire).copied().unwrap_or(wire)] = value;
                }
                coded::derandomize(&mut code_llr, &c.randomizer);
                let key = c.repetition.as_ref().unwrap().key(&candidate.header)?;
                groups.entry(key).or_default().push((
                    repetition::Copy {
                        source_sha256: source_sha256.to_ascii_lowercase(),
                        start_sample: start,
                        end_sample: end,
                        header: candidate.header.clone(),
                        llr: code_llr,
                    },
                    candidate.clone(),
                ));
            }
        }
        if let Some(config) = &c.repetition {
            for group in groups.values() {
                if group.len() < 2 {
                    continue;
                }
                let mut copies = Vec::new();
                // Stable, disjoint grouping; overlapping alternatives cannot
                // reinforce one another. Do not select using successful CRCs.
                for (copy, _) in group {
                    if copies
                        .last()
                        .is_none_or(|last: &repetition::Copy| copy.start_sample >= last.end_sample)
                    {
                        copies.push(copy.clone());
                    }
                }
                if copies.len() < 2 {
                    continue;
                }
                if copies.len() > config.maximum_copies
                    || (copies.last().unwrap().end_sample - copies[0].start_sample) as f64
                        > config.maximum_gap_symbols as f64 * rate as f64 / c.symbol_rate
                {
                    report.rejected_groups += 1;
                    continue;
                }
                let Ok(llr) = repetition::combine_with_layout(
                    &copies,
                    config.minimum_correlation,
                    config.mission_header.as_ref(),
                ) else {
                    report.rejected_groups += 1;
                    continue;
                };
                charge(&mut report.consumed_work, c, 1)?;
                charge_list(&mut report.consumed_work, c, 1)?;
                report.combined_groups += 1;
                let Some(accepted) = decode_code(c, &llr)?.accepted else {
                    continue;
                };
                let (bytes, layers) = (accepted.bytes, accepted.validation_layers);
                let hex = hex::encode(bytes);
                let frame = frames.entry(hex.clone()).or_insert_with(|| Frame {
                    frame_hex: hex,
                    validation_layers: layers,
                    provenance: Vec::new(),
                });
                for copy in &copies {
                    frame.provenance.push(Provenance {
                        round,
                        lane: "independent_repeats".into(),
                        start_sample: copy.start_sample,
                        end_sample: copy.end_sample,
                        carrier_hz: group
                            .iter()
                            .find(|(item, _)| {
                                item.start_sample == copy.start_sample
                                    && item.end_sample == copy.end_sample
                            })
                            .ok_or("missing combined-copy provenance")?
                            .1
                            .carrier,
                        copies: copies.len(),
                    });
                }
                // Combined-only evidence is not used for cancellation in this version.
            }
        }
        if round == rounds {
            break;
        }
        let mut removed = false;
        for (candidate, hex, word) in reconstructions {
            let (start, _) = candidate.bounds(c);
            if cancelled
                .iter()
                .any(|(at, old)| *old == hex && at.abs_diff(start) < (candidate.step * 2.) as usize)
            {
                continue;
            }
            let mut polarity = vec![1.; word.len()];
            coded::derandomize(&mut polarity, &c.randomizer);
            let mut bits = c.syncword.clone();
            bits.extend(
                candidate
                    .header
                    .iter()
                    .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1)),
            );
            bits.extend((0..word.len()).map(|wire| {
                let index = c.wire_to_code.get(wire).copied().unwrap_or(wire);
                word[index] ^ u8::from(polarity[index] < 0.)
            }));
            let mut receipt = if let Some(geometry) = &candidate.geometry {
                let sample_visits = ((bits.len() as f64 * candidate.step).ceil() as u64)
                    * match crate::advanced_waveform::resolved(c) {
                        crate::advanced_waveform::Waveform::Afsk { .. } => 9 * 128,
                        crate::advanced_waveform::Waveform::Fsk {
                            gaussian_bt: Some(_),
                            ..
                        } => (candidate.step * 4.).ceil() as u64 + 128,
                        crate::advanced_waveform::Waveform::Psk {
                            pulse: crate::psk::MatchedFilter::RootRaisedCosine { span_symbols, .. },
                        } => span_symbols as u64 * 32 + 128,
                        _ => 128,
                    };
                report.consumed_work = report
                    .consumed_work
                    .checked_add(sample_visits)
                    .ok_or("SIC work overflow")?;
                if report.consumed_work > c.maximum_work {
                    return Err(
                        "aggregate IQ reconstruction work exhausted; no partial success".into(),
                    );
                }
                match crate::advanced_waveform::refine_reconstruction(
                    &residual, &bits, rate, c, geometry,
                ) {
                    Ok((begin, model)) => interference::subtract_model(
                        &mut residual,
                        &model,
                        begin,
                        candidate.step,
                        c.cancellation.as_ref().unwrap().minimum_holdout_reduction,
                        {
                            let center = c
                                .carrier_centers_hz
                                .iter()
                                .min_by(|a, b| {
                                    (**a - candidate.carrier)
                                        .abs()
                                        .total_cmp(&(**b - candidate.carrier).abs())
                                })
                                .unwrap();
                            [
                                center - c.residual_carrier_bound_hz - candidate.carrier,
                                center + c.residual_carrier_bound_hz - candidate.carrier,
                            ]
                            .map(|v| std::f64::consts::TAU * v / rate as f64)
                        },
                    )?,
                    Err(_) => interference::Receipt {
                        round,
                        frame_sha256: None,
                        start_sample: start,
                        end_sample: candidate.bounds(c).1,
                        holdout_reduction: 0.,
                        accepted: false,
                        reason: "waveform_reconstruction_rejected",
                    },
                }
            } else {
                interference::subtract(
                    &mut residual,
                    &bits,
                    candidate.start,
                    candidate.step,
                    candidate.carrier,
                    rate,
                    c.cancellation.as_ref().unwrap().minimum_holdout_reduction,
                )?
            };
            receipt.round = round;
            receipt.frame_sha256 = Some(hex::encode(Sha256::digest(
                hex::decode(&hex).map_err(|e| e.to_string())?,
            )));
            if receipt.accepted {
                removed = true;
                cancelled.push((start, hex));
            }
            report.cancellation.push(receipt);
        }
        if !removed {
            break;
        }
    }
    report.baseline_frames.sort();
    report.baseline_frames.dedup();
    report.added_frames = frames
        .keys()
        .filter(|hex| report.baseline_frames.binary_search(hex).is_err())
        .cloned()
        .collect();
    report.frames = frames.into_values().collect();
    Ok(report)
}

pub fn decode_file(
    path: &Path,
    plan: &FilePlan,
    output: &Path,
) -> Result<serde_json::Value, String> {
    plan.receiver
        .validate(plan.sample_rate_hz, plan.sample_count)?;
    if !matches!(
        plan.format,
        generic::InputFormat::Ci16Le | generic::InputFormat::Cf32Le | generic::InputFormat::Cf64Le
    ) {
        return Err(
            "advanced IQ file path requires ci16_le, cf32_le or cf64_le; OGG lacks coherent IQ"
                .into(),
        );
    }
    let out = input::existing_new_dir(output)?;
    let result = (|| {
        let source_guard = crate::recovery_guard::Guard::open(path)?;
        let source = source_guard.identity();
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let runtime_guard = crate::recovery_guard::Guard::open(&exe)?;
        let runtime = runtime_guard.identity();
        source_guard.verify()?;
        runtime_guard.verify()?;
        input::write_json_new(
            &out.join("plan.json"),
            &serde_json::json!({"schema":"framelift-advanced-iq-plan-v1","source":source,"runtime":runtime,"compute":crate::compute::current().identity(),"plan":plan,"publication_ready":false}),
        )?;
        let samples =
            source_guard.read_iq_window(&plan.format, plan.start_sample, plan.sample_count)?;
        let mut hash = Sha256::new();
        for z in &samples {
            hash.update(z.re.to_bits().to_le_bytes());
            hash.update(z.im.to_bits().to_le_bytes());
        }
        let mut report = decode(
            &samples,
            plan.sample_rate_hz,
            &plan.receiver,
            &source.sha256,
        )?;
        offset_report(&mut report, plan.start_sample)?;
        source_guard.verify()?;
        runtime_guard.verify()?;
        input::write_json_new(&out.join("report.json"), &report)?;
        let summary = serde_json::json!({"status":"complete","experimental":true,"publication_ready":false,"frames":report.frames.len(),"baseline_frames":report.baseline_frames.len(),"added_frames":report.added_frames.len(),"candidates":report.candidates,"combined_groups":report.combined_groups,"accepted_cancellations":report.cancellation.iter().filter(|r|r.accepted).count(),"iq_f64_le_sha256":hex::encode(hash.finalize()),"consumed_work":report.consumed_work});
        source_guard.verify()?;
        runtime_guard.verify()?;
        input::write_json_new(&out.join("summary.json"), &summary)?;
        Ok(summary)
    })();
    if let Err(error) = &result {
        input::write_json_new(
            &out.join("failure.json"),
            &serde_json::json!({"status":"failed","error":error}),
        )?;
    }
    result
}

pub(crate) fn offset_report(report: &mut Report, offset: usize) -> Result<(), String> {
    for receipt in &mut report.soft_acquisition {
        for candidate in &mut receipt.selected {
            candidate.start_sample += offset as f64;
        }
    }
    let add = |x: &mut usize| -> Result<(), String> {
        *x = x
            .checked_add(offset)
            .ok_or("source sample coordinate overflow")?;
        Ok(())
    };
    for frame in &mut report.frames {
        for p in &mut frame.provenance {
            add(&mut p.start_sample)?;
            add(&mut p.end_sample)?;
        }
    }
    for receipt in &mut report.cancellation {
        add(&mut receipt.start_sample)?;
        add(&mut receipt.end_sample)?;
    }
    for receipt in &mut report.refinement {
        add(&mut receipt.start_sample)?;
        add(&mut receipt.end_sample)?;
        receipt.geometry.start_sample += offset as f64;
        receipt.geometry.carrier_reference_sample += offset as f64;
    }
    for receipt in report
        .coherent
        .iter_mut()
        .chain(report.rejected_lanes.iter_mut())
    {
        if let Some(start) = receipt
            .get("start_sample")
            .and_then(serde_json::Value::as_f64)
        {
            receipt["start_sample"] = serde_json::json!(start + offset as f64);
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "tests/advanced_iq_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "tests/multimode_iq_tests.rs"]
mod multimode_tests;
