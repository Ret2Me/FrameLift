//! Variable-length AX.25 UI recovery from IQ, with an additive soft-sequence lane.
//!
//! The baseline and the sequence detector consume the same physical symbol
//! stream. Only already validated, temporally separate frames train the channel;
//! no expected payload, repaired FCS, or CRC-directed search enters BCJR.
use crate::{
    dsp,
    generic::{self, Demodulator, Signal},
    input, protocol, sequence, soft_sequence,
};
use num_complex::Complex64;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

const MAX_SAMPLES: usize = 1_048_576;
const MAX_STREAMS: usize = 4096;
const MAX_SOFT_VISITS: usize = 16_777_216;
const FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LineCoding {
    Direct,
    Nrzi,
}

/// G3RUH is applied AFTER NRZI decoding, using received bits at delays 12 and 17.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Scrambler {
    None,
    G3ruh,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SequenceConfig {
    /// Earliest validated physical occurrences, never ranked by target CRC gain.
    pub maximum_training_frames: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub waveform: generic::Waveform,
    pub line_coding: LineCoding,
    pub scrambler: Scrambler,
    pub sequence: Option<SequenceConfig>,
    pub workers: usize,
    pub work_budget: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilePlan {
    pub format: generic::InputFormat,
    pub sample_rate_hz: u32,
    pub start_sample: u64,
    pub sample_count: usize,
    pub receiver: Config,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub struct Provenance {
    pub stream: usize,
    pub lane: String,
    /// Physical serialized symbol indices, including stuffed bits and FCS.
    pub start_symbol: usize,
    pub end_symbol: usize,
    /// Conservative source bounds: the entire demodulated input window.
    pub start_sample: u64,
    pub end_sample: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Frame {
    pub hex: String,
    pub validation_layers: Vec<String>,
    pub provenance: Vec<Provenance>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SequenceReceipt {
    pub stream: usize,
    /// Half-open serialized-symbol spans excluded from supplemental acceptance.
    pub training_spans: Vec<(usize, usize)>,
    pub channels: Vec<soft_sequence::Channel>,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Report {
    pub schema: String,
    pub source_sha256: String,
    pub iq_f64_le_sha256: String,
    pub sample_count: usize,
    pub streams: usize,
    pub baseline_frames: Vec<String>,
    pub added_frames: Vec<String>,
    pub frames: Vec<Frame>,
    pub sequence: Vec<SequenceReceipt>,
    pub consumed_work: u64,
}

fn quadrature(config: &Config) -> bool {
    matches!(config.waveform.dsp.mode.as_str(), "qpsk" | "oqpsk")
}

impl Config {
    pub fn validate(&self, rate: u32, count: usize) -> Result<(), String> {
        let w = &self.waveform;
        if !(8192..=MAX_SAMPLES).contains(&count)
            || rate == 0
            || !(1..=64).contains(&self.workers)
            || self.work_budget == 0
            || self.work_budget > 1_000_000_000_000
            || self
                .sequence
                .as_ref()
                .is_some_and(|s| !(1..=32).contains(&s.maximum_training_frames))
            || w.hypothesis_id.trim().is_empty()
            || w.hypothesis_id.len() > 128
            || !(4..=128).contains(&w.dsp.phase_bins)
            || w.dsp.rate_errors_ppm.is_empty()
            || w.dsp.rate_errors_ppm.len() > 32
            || w.dsp
                .rate_errors_ppm
                .iter()
                .any(|x| !x.is_finite() || x.abs() > 10000.)
            || !w.dsp.baud.is_finite()
            || w.dsp.baud <= 0.
            || !(1..=128).contains(&w.decimation)
            || !(1..=4096).contains(&w.dsp.top_timing)
            || !matches!(w.dsp.bank.as_str(), "full" | "global" | "diverse")
        {
            return Err("invalid bounded HDLC IQ profile, sample count or work limit".into());
        }
        match (w.demodulator_id.as_str(), w.dsp.mode.as_str()) {
            ("iq_bpsk", "bpsk") | ("iq_qpsk", "qpsk") | ("iq_oqpsk", "oqpsk") => {
                crate::psk::validate_waveform(rate, w)?;
                if w.psk.as_ref().is_some_and(|p| p.differential_binary) {
                    return Err("HDLC requires explicit line_coding; PSK differential_binary must be false".into());
                }
            },
            ("phase_fsk" | "channel_conditioned_phase_fsk", "fsk") => {
                if w.carrier_hz.is_some() || w.psk.is_some() {
                    return Err("FSK IQ profile cannot contain PSK configuration or explicit carrier".into());
                }
            },
            ("bell202_afsk", "afsk") => {
                if count < 16384 || w.carrier_hz.is_some() || w.cutoff_hz.is_some() || w.psk.is_some() {
                    return Err("Bell-202 needs >=16384 IQ samples and no carrier/cutoff/PSK override".into());
                }
            },
            _ => return Err("HDLC IQ supports phase_fsk, channel_conditioned_phase_fsk, bell202_afsk, iq_bpsk, iq_qpsk, iq_oqpsk with matching DSP modes".into()),
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Span {
    start: usize,
    end: usize,
    frame: Vec<u8>,
}

fn received_residue(frame: &[u8]) -> bool {
    let mut state = 0xffff_u16;
    for byte in frame {
        for bit in 0..8 {
            let feedback = (state ^ u16::from(byte >> bit)) & 1;
            state >>= 1;
            if feedback != 0 {
                state ^= 0x8408;
            }
        }
    }
    state == 0xf0b8
}

fn spans(soft: &[f64], threshold: f64, c: &Config) -> Vec<Span> {
    let levels: Vec<_> = soft.iter().map(|x| u8::from(*x >= threshold)).collect();
    let mut found = BTreeSet::new();
    for initial in 0..if c.line_coding == LineCoding::Nrzi {
        2
    } else {
        1
    } {
        let coded: Vec<_> = levels
            .iter()
            .enumerate()
            .map(|(i, bit)| {
                if c.line_coding == LineCoding::Direct {
                    *bit
                } else {
                    u8::from(*bit == if i == 0 { initial } else { levels[i - 1] })
                }
            })
            .collect();
        let mut bits = coded.clone();
        if c.scrambler == Scrambler::G3ruh {
            for i in 12..bits.len() {
                bits[i] ^= coded[i - 12];
            }
            for i in 17..bits.len() {
                bits[i] ^= coded[i - 17];
            }
        }
        let mut previous = None;
        for (right, window) in bits.windows(8).enumerate() {
            // The recording may start mid-transmission. Before 17 received
            // bits the self-synchronizing descrambler history is unknown;
            // do not treat its temporary zero-initialized output as evidence.
            if window != FLAG || (c.scrambler == Scrambler::G3ruh && right < 17) {
                continue;
            }
            if let Some(left) = previous
                && right > left + 8
                && right - left - 8 <= 9821
                && let Ok(unstuffed) = protocol::hdlc_unstuff(&bits[left + 8..right])
                && let Ok(frame) = protocol::bits_to_bytes(&unstuffed, true)
                && (18..protocol::AX25_MAX_DECODER_FRAME_BYTES).contains(&frame.len())
                && protocol::valid_ax25_fcs(&frame)
                && received_residue(&frame)
                && protocol::valid_ax25_ui(&frame[..frame.len() - 2])
            {
                found.insert(Span {
                    start: left + 8,
                    end: right,
                    frame,
                });
            }
            previous = Some(right);
        }
    }
    found.into_iter().collect()
}

struct Stream {
    soft: Vec<f64>,
    threshold: f64,
    lane: String,
}
struct ResultStream {
    baseline: Vec<Span>,
    supplemental: Vec<Span>,
    receipt: Option<SequenceReceipt>,
}

/// Channel training is restricted to guarded interiors of previously validated
/// physical spans. Its parameters, not transmitted target bits, cross the boundary.
fn sequence_stream(stream: &Stream, anchors: &[Span], c: &Config, index: usize) -> ResultStream {
    let mut result = ResultStream {
        baseline: anchors.to_vec(),
        supplemental: vec![],
        receipt: None,
    };
    let Some(options) = &c.sequence else {
        return result;
    };
    let training: Vec<_> = anchors
        .iter()
        .take(options.maximum_training_frames)
        .map(|x| (x.start, x.end))
        .collect();
    let mut receipt = SequenceReceipt {
        stream: index,
        training_spans: training.clone(),
        channels: vec![],
        reason: "no_validated_training_frames".into(),
    };
    if training.is_empty() {
        result.receipt = Some(receipt);
        return result;
    }
    let arms = if quadrature(c) { 2 } else { 1 };
    let fitted = (|| -> Result<Vec<f64>, String> {
        let mut decoded = vec![0.; stream.soft.len()];
        for arm in 0..arms {
            let samples: Vec<_> = stream
                .soft
                .iter()
                .skip(arm)
                .step_by(arms)
                .copied()
                .collect();
            let levels: Vec<_> = samples
                .iter()
                .map(|v| u8::from(*v >= stream.threshold))
                .collect();
            let arm_spans: Vec<_> = training
                .iter()
                .map(|(a, b)| {
                    (
                        a.saturating_sub(arm).div_ceil(arms),
                        b.saturating_sub(arm).div_ceil(arms),
                    )
                })
                .collect();
            let model = sequence::fit_channel(&samples, &levels, &arm_spans)?;
            let mut squared = 0.;
            let mut count = 0;
            for &(start, end) in &arm_spans {
                if end - start <= 2 * sequence::TRAINING_GUARD_SYMBOLS {
                    continue;
                }
                for i in
                    start + sequence::TRAINING_GUARD_SYMBOLS..end - sequence::TRAINING_GUARD_SYMBOLS
                {
                    let prediction = model.bias
                        + (0..3)
                            .map(|k| model.taps[k] * (2. * f64::from(levels[i + k - 1]) - 1.))
                            .sum::<f64>();
                    squared += (samples[i] - prediction).powi(2);
                    count += 1;
                }
            }
            let power = model.taps.iter().map(|v| v * v).sum::<f64>();
            let channel = soft_sequence::Channel {
                taps: model.taps,
                bias: model.bias,
                noise_variance: (squared / count.max(1) as f64)
                    .max(power * 1e-3)
                    .clamp(1e-12, 1e12),
            };
            let output = soft_sequence::detect_complete(&samples, &channel, &[])?;
            for (i, llr) in output.posterior_llr.into_iter().enumerate() {
                decoded[i * arms + arm] = llr;
            }
            receipt.channels.push(channel);
        }
        Ok(decoded)
    })();
    match fitted {
        Ok(decoded) => {
            let guard = arms * sequence::TRAINING_GUARD_SYMBOLS;
            result.supplemental = spans(&decoded, 0., c)
                .into_iter()
                .filter(|target| {
                    // Flags, NRZI predecessor and G3RUH history belong to the
                    // consumed target context too, not just the unstuffed bytes.
                    let start = target.start.saturating_sub(guard + 8);
                    let end = target.end.saturating_add(8);
                    training.iter().all(|(a, b)| {
                        end <= a.saturating_sub(guard) || start >= b.saturating_add(guard)
                    })
                })
                .collect();
            receipt.reason = "complete_bcjr_disjoint_target_acceptance".into();
        }
        Err(error) => {
            receipt.reason = error;
        }
    }
    result.receipt = Some(receipt);
    result
}

pub fn decode(
    iq: &[Complex64],
    rate: u32,
    c: &Config,
    source_sha256: &str,
) -> Result<Report, String> {
    c.validate(rate, iq.len())?;
    if source_sha256.len() != 64
        || !source_sha256.bytes().all(|x| x.is_ascii_hexdigit())
        || iq
            .iter()
            .any(|z| !z.re.is_finite() || !z.im.is_finite() || z.norm() > 1e6)
    {
        return Err("HDLC input needs a SHA-256 identity and finite bounded IQ".into());
    }
    let w = &c.waveform;
    // Conservative visits: sequence includes sum-product plus framing; this is
    // deterministic admission accounting, not a prediction of elapsed time.
    let mut work = (iq.len() as u64).checked_mul(1024).ok_or("work overflow")?;
    if matches!(w.dsp.mode.as_str(), "bpsk" | "qpsk" | "oqpsk") {
        let (_, frontend) = crate::psk::work_estimate(rate, w, iq.len())?;
        work = u64::try_from(frontend).map_err(|_| "PSK work overflow")?;
    }
    if work > c.work_budget {
        return Err("HDLC frontend work budget exceeded".into());
    }
    let mut visits = 0usize;
    let mut streams = Vec::new();
    let mut collect =
        |soft: &[f64], threshold: f64, _: f64, lane: Option<&str>| -> Result<(), String> {
            visits = visits
                .checked_add(soft.len())
                .ok_or("soft visit overflow")?;
            let additional = soft.len() as u64 * if c.sequence.is_some() { 512 } else { 32 };
            work = work.checked_add(additional).ok_or("HDLC work overflow")?;
            if streams.len() >= MAX_STREAMS
                || visits > MAX_SOFT_VISITS
                || soft.len() > soft_sequence::MAX_SYMBOLS
                || work > c.work_budget
            {
                return Err("HDLC stream/soft-symbol/work budget exceeded".into());
            }
            if !threshold.is_finite() || soft.iter().any(|x| !x.is_finite() || x.abs() > 1e6) {
                return Err("HDLC frontend returned non-finite or excessive symbols".into());
            }
            streams.push(Stream {
                soft: soft.to_vec(),
                threshold,
                lane: lane.unwrap_or("clock_bank").into(),
            });
            Ok(())
        };
    match w.demodulator_id.as_str() {
        "iq_bpsk" | "iq_qpsk" | "iq_oqpsk" => {
            let mode = match w.demodulator_id.as_str() {
                "iq_bpsk" => "bpsk",
                "iq_qpsk" => "qpsk",
                _ => "oqpsk",
            };
            crate::psk::PskDemodulator(mode).visit_soft_symbols(
                Signal::Iq(iq),
                rate,
                w,
                &mut collect,
            )?;
        }
        _ => {
            let frontend = if w.demodulator_id == "bell202_afsk" {
                dsp::bell202_iq_frontend(iq, rate, &w.dsp, w.decimation, w.mark_hz, w.space_hz)?
            } else {
                dsp::iq_frontend(
                    iq,
                    rate,
                    &w.dsp,
                    &w.demodulator_id,
                    w.decimation,
                    w.cutoff_hz,
                )?
            };
            for timing in dsp::timing_bank(&frontend, &w.dsp)? {
                collect(
                    &dsp::soft_symbols(&frontend, &timing)?,
                    timing.threshold,
                    timing.score,
                    None,
                )?;
            }
        }
    }
    let run = |(index, stream): (usize, &Stream)| {
        let anchors = spans(&stream.soft, stream.threshold, c);
        sequence_stream(stream, &anchors, c, index)
    };
    let results: Vec<_> = if c.workers == 1 {
        streams.iter().enumerate().map(run).collect()
    } else {
        rayon::ThreadPoolBuilder::new()
            .num_threads(c.workers)
            .build()
            .map_err(|e| e.to_string())?
            .install(|| streams.par_iter().enumerate().map(run).collect())
    };
    let mut all: BTreeMap<String, Frame> = BTreeMap::new();
    let mut baseline = BTreeSet::new();
    let mut receipts = Vec::new();
    for (stream, result) in results.into_iter().enumerate() {
        for (supplemental, spans) in [(false, result.baseline), (true, result.supplemental)] {
            for span in spans {
                let hex = hex::encode(span.frame);
                if !supplemental {
                    baseline.insert(hex.clone());
                }
                let frame = all.entry(hex.clone()).or_insert_with(|| Frame {
                    hex,
                    validation_layers: vec![
                        "crc16_x25_received_fcs".into(),
                        "independent_received_residue_f0b8".into(),
                        "ax25_ui".into(),
                    ],
                    provenance: vec![],
                });
                frame.provenance.push(Provenance {
                    stream,
                    lane: format!(
                        "{}:{}",
                        if supplemental { "sequence" } else { "baseline" },
                        streams[stream].lane
                    ),
                    start_symbol: span.start,
                    end_symbol: span.end,
                    start_sample: 0,
                    end_sample: iq.len() as u64,
                });
            }
        }
        if let Some(receipt) = result.receipt {
            receipts.push(receipt);
        }
    }
    let mut hash = Sha256::new();
    for z in iq {
        hash.update(z.re.to_bits().to_le_bytes());
        hash.update(z.im.to_bits().to_le_bytes());
    }
    Ok(Report {
        schema: "framelift-recovery-hdlc-v1".into(),
        source_sha256: source_sha256.into(),
        iq_f64_le_sha256: hex::encode(hash.finalize()),
        sample_count: iq.len(),
        streams: streams.len(),
        baseline_frames: baseline.iter().cloned().collect(),
        added_frames: all
            .keys()
            .filter(|x| !baseline.contains(*x))
            .cloned()
            .collect(),
        frames: all.into_values().collect(),
        sequence: receipts,
        consumed_work: work,
    })
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
            "HDLC recovery requires original coherent IQ: ci16_le, cf32_le or cf64_le".into(),
        );
    }
    let end = plan
        .start_sample
        .checked_add(plan.sample_count as u64)
        .ok_or("sample interval overflow")?;
    let out = input::existing_new_dir(output)?;
    let result = (|| {
        let guard = crate::recovery_guard::Guard::open(path)?;
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let runtime = crate::recovery_guard::Guard::open(&exe)?;
        input::write_json_new(
            &out.join("plan.json"),
            &serde_json::json!({"schema":"framelift-recovery-hdlc-plan-v1", "source":guard.identity(),"runtime":runtime.identity(),"compute":crate::compute::current().identity(),"plan":plan,"publication_ready":false}),
        )?;
        let start = usize::try_from(plan.start_sample)
            .map_err(|_| "sample offset exceeds address space")?;
        let iq = guard.read_iq_window(&plan.format, start, plan.sample_count)?;
        let mut report = decode(
            &iq,
            plan.sample_rate_hz,
            &plan.receiver,
            &guard.identity().sha256,
        )?;
        for frame in &mut report.frames {
            for p in &mut frame.provenance {
                p.start_sample = plan.start_sample;
                p.end_sample = end;
            }
        }
        guard.verify()?;
        runtime.verify()?;
        input::write_json_new(&out.join("report.json"), &report)?;
        let summary = serde_json::json!({"status":"complete","experimental":true,"publication_ready":false,"frames":report.frames.len(),"baseline_frames":report.baseline_frames.len(),"added_frames":report.added_frames.len(),"streams":report.streams,"consumed_work":report.consumed_work,"iq_f64_le_sha256":report.iq_f64_le_sha256});
        guard.verify()?;
        runtime.verify()?;
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

#[cfg(test)]
#[path = "tests/recovery_hdlc_tests.rs"]
mod tests;
