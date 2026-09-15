//! Deterministic parallel window execution; no search pruning or early exit.
use crate::{dsp, input, protocol};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fs::OpenOptions;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::time::Instant;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecodeConfig {
    pub dsp: dsp::DspConfig,
    pub window_seconds: f64,
    pub hop_seconds: f64,
    pub threads: usize,
}
impl Default for DecodeConfig {
    fn default() -> Self {
        Self {
            dsp: dsp::DspConfig::default(),
            window_seconds: 6.0,
            hop_seconds: 3.0,
            threads: 1,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Provenance {
    pub window_start_seconds: f64,
    pub waveform: String,
    pub timing_rank: usize,
    pub timing_score: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Frame {
    pub payload_hex: String,
    pub frame_with_fcs_hex: String,
    pub payload_sha256: String,
    pub payload_bytes: usize,
    pub validation: String,
    pub independent_bitwise_crc_passed: bool,
    pub provenance: Vec<Provenance>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowRow {
    pub offset_seconds: f64,
    pub samples: usize,
    pub frame_instances: usize,
    pub unique_total: usize,
    pub failures: Vec<String>,
    pub timing_attempts: usize,
    /// No symbol variation is a completed empty frontend, not a failed window.
    /// Absent in legacy receipts; malformed/numeric failures remain explicit.
    #[serde(default)]
    pub frontend_outcome: Option<String>,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct StageTimes {
    pub frontend_seconds: f64,
    pub timing_seconds: f64,
    pub protocol_seconds: f64,
}
impl StageTimes {
    fn add(&mut self, other: &Self) {
        self.frontend_seconds += other.frontend_seconds;
        self.timing_seconds += other.timing_seconds;
        self.protocol_seconds += other.protocol_seconds;
    }
}
struct WindowOutput {
    row: WindowRow,
    frames: Vec<Frame>,
    times: StageTimes,
}

/// Deliberately separate bitwise implementation from protocol's table CRC.
fn independent_received_fcs(frame: &[u8]) -> bool {
    if frame.len() < 18 {
        return false;
    }
    let mut remainder = 0xffffu16;
    for byte in &frame[..frame.len() - 2] {
        remainder ^= *byte as u16;
        for _ in 0..8 {
            remainder = if remainder & 1 == 1 {
                (remainder >> 1) ^ 0x8408
            } else {
                remainder >> 1
            };
        }
    }
    (remainder ^ 0xffff).to_le_bytes() == frame[frame.len() - 2..]
}

pub fn window_bounds(
    samples: usize,
    rate: u32,
    window: f64,
    hop: f64,
) -> Result<Vec<(usize, usize)>, String> {
    if rate == 0
        || !window.is_finite()
        || !hop.is_finite()
        || window <= 0.0
        || hop <= 0.0
        || hop > window
        || window > input::MAX_AUDIO_SECONDS
    {
        return Err("invalid window/hop/sample rate".into());
    }
    let width = (rate as f64 * window) as usize;
    let stride = (rate as f64 * hop) as usize;
    if width < 8192 || stride == 0 {
        return Err("window requires >=8192 samples and positive hop".into());
    }
    if width > dsp::MAX_PCM_WINDOW_SAMPLES {
        return Err("window exceeds DSP sample bound".into());
    }
    let expected = if samples <= width {
        1
    } else {
        (samples - width)
            .div_ceil(stride)
            .checked_add(1)
            .ok_or("window count overflow")?
    };
    if expected > 100_000 {
        return Err("window budget exceeds 100000 windows; no work was pruned".into());
    }
    let mut output = Vec::new();
    let mut offset = 0;
    while offset < samples {
        let end = offset.saturating_add(width).min(samples);
        if end - offset < 8192 {
            break;
        }
        output.push((offset, end));
        if end >= samples {
            break;
        }
        offset = offset.checked_add(stride).ok_or("window offset overflow")?;
    }
    if output.is_empty() {
        return Err("no complete eligible analysis window".into());
    }
    Ok(output)
}

fn decode_window(samples: &[f64], rate: u32, offset: usize, config: &DecodeConfig) -> WindowOutput {
    let mut row = WindowRow {
        offset_seconds: offset as f64 / rate as f64,
        samples: samples.len(),
        frame_instances: 0,
        unique_total: 0,
        failures: vec![],
        timing_attempts: 0,
        frontend_outcome: None,
    };
    let mut times = StageTimes::default();
    let mut frames = Vec::new();
    let attempt = (|| -> Result<(), String> {
        let timer = Instant::now();
        let front = dsp::frontend(samples, rate, &config.dsp)?;
        row.frontend_outcome = Some(
            if front.samples.is_empty() {
                "empty_no_usable_symbol_variation"
            } else {
                "conditioned_nonempty"
            }
            .into(),
        );
        times.frontend_seconds = timer.elapsed().as_secs_f64();
        let timer = Instant::now();
        let bank = dsp::timing_bank(&front, &config.dsp)?;
        times.timing_seconds = timer.elapsed().as_secs_f64();
        row.timing_attempts = bank.len();
        let timer = Instant::now();
        let mut seen = HashSet::new();
        let modes = if config.dsp.mode == "fsk" {
            vec![false, true]
        } else {
            vec![false]
        };
        let waveform = format!(
            "fm_demodulated:positive-audio-pilot-{}-{}-v1",
            config.dsp.mode, config.dsp.baud
        );
        for (rank, timing) in bank.iter().enumerate() {
            let soft = dsp::soft_symbols(&front, timing)?;
            for frame in protocol::decode_ax25(&soft, timing.threshold, &modes)? {
                if !seen.insert(frame.clone()) {
                    continue;
                }
                if !independent_received_fcs(&frame)
                    || !protocol::valid_ax25_ui(&frame[..frame.len() - 2])
                {
                    return Err("decoded frame failed independent validation boundary".into());
                }
                let payload = &frame[..frame.len() - 2];
                frames.push(Frame {
                    payload_hex: hex::encode(payload),
                    frame_with_fcs_hex: hex::encode(&frame),
                    payload_sha256: hex::encode(Sha256::digest(payload)),
                    payload_bytes: payload.len(),
                    validation: "crc16_x25+ax25_ui".into(),
                    independent_bitwise_crc_passed: true,
                    provenance: vec![Provenance {
                        window_start_seconds: row.offset_seconds,
                        waveform: waveform.clone(),
                        timing_rank: rank,
                        timing_score: timing.score,
                    }],
                });
            }
        }
        times.protocol_seconds = timer.elapsed().as_secs_f64();
        Ok(())
    })();
    if let Err(error) = attempt {
        if row.frontend_outcome.is_none() {
            row.frontend_outcome = Some("failed".into());
        }
        row.failures.push(error);
    }
    row.frame_instances = frames.len();
    WindowOutput { row, frames, times }
}

pub fn decode_samples(
    samples: &[f64],
    rate: u32,
    config: &DecodeConfig,
) -> Result<(Vec<Frame>, Vec<WindowRow>, StageTimes), String> {
    if config.threads == 0 || config.threads > 64 {
        return Err("threads must be in 1..=64".into());
    }
    if samples.iter().any(|x| !x.is_finite()) {
        return Err("non-finite audio".into());
    }
    let windows = window_bounds(
        samples.len(),
        rate,
        config.window_seconds,
        config.hop_seconds,
    )?;
    let largest = windows.iter().map(|&(a, b)| b - a).max().unwrap_or(0);
    let estimate =
        (samples.len() as u128) * 8 + (largest as u128) * 8 * 24 * (config.threads as u128);
    if estimate > 6u128 * 1024 * 1024 * 1024 {
        return Err("estimated audio+worker working set exceeds 6 GiB".into());
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(config.threads)
        .build()
        .map_err(|e| e.to_string())?;
    // Indexed collect restores original window order regardless of completion order.
    let outputs: Vec<WindowOutput> = pool.install(|| {
        windows
            .par_iter()
            .map(|&(start, end)| decode_window(&samples[start..end], rate, start, config))
            .collect()
    });
    let mut frames: BTreeMap<String, Frame> = BTreeMap::new();
    let mut rows = Vec::new();
    let mut total = StageTimes::default();
    for mut output in outputs {
        for frame in output.frames {
            frames
                .entry(frame.payload_hex.clone())
                .and_modify(|old| old.provenance.extend(frame.provenance.clone()))
                .or_insert(frame);
        }
        total.add(&output.times);
        output.row.unique_total = frames.len();
        rows.push(output.row);
    }
    Ok((frames.into_values().collect(), rows, total))
}

pub fn decode_file(
    path: &Path,
    output: &Path,
    observation_id: Option<u64>,
    config: &DecodeConfig,
) -> Result<serde_json::Value, String> {
    let started = Instant::now();
    let out = input::existing_new_dir(output)?;
    let execution = (|| -> Result<serde_json::Value, String> {
        let audio = input::load_audio(path, &out)?;
        let code = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
        input::write_json_new(
            &out.join("plan.json"),
            &serde_json::json!({"schema":"rust-receiver-plan-v1","config":config,
            "input":audio.wav_identity,"source":audio.source_identity,"executable":code,"reference_bytes_used_for_search":false,
            "network_submission":false,"representation":"fm_demodulated","conversion_command":audio.conversion_command}),
        )?;
        let decode_started = Instant::now();
        let (frames, rows, stages) = decode_samples(&audio.samples, audio.sample_rate, config)?;
        let elapsed = decode_started.elapsed().as_secs_f64();
        let mut journal = BufWriter::new(
            OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(out.join("windows.jsonl"))
                .map_err(|e| e.to_string())?,
        );
        for row in &rows {
            serde_json::to_writer(&mut journal, row).map_err(|e| e.to_string())?;
            journal.write_all(b"\n").map_err(|e| e.to_string())?;
        }
        journal.flush().map_err(|e| e.to_string())?;
        journal.get_ref().sync_all().map_err(|e| e.to_string())?;
        let failures = rows.iter().filter(|r| !r.failures.is_empty()).count();
        Ok(
            serde_json::json!({"schema":"rust-native-audio-result-v1","status":if failures==0 {"complete"}else{"failed"},
            "observation_id":observation_id,"input":audio.wav_identity,"source_input":audio.source_identity,
            "sample_rate_hz":audio.sample_rate,"input_samples":audio.samples.len(),"duration_seconds":audio.samples.len() as f64/audio.sample_rate as f64,
            "config":config,"window_count":rows.len(),"failed_window_count":failures,"frames":frames,
            "unique_pdu_count":frames.len(),"unique_pdu_bytes":frames.iter().map(|f|f.payload_bytes).sum::<usize>(),
            "elapsed_seconds":elapsed,"total_wall_seconds":started.elapsed().as_secs_f64(),
            "stage_elapsed_sum_seconds":stages,"stage_timing_note":"Sums of per-window worker wall times; overlap under parallelism, not CPU measurements",
            "controls":[],"control_note":"Synthetic controls run separately; not included in decoder timing",
            "publication_ready":false,"deployment_ready":false,"reference_bytes_used_for_search":false,
            "runtime_language":"Rust","python_runtime_required":false}),
        )
    })();
    match execution {
        Ok(result) => {
            input::write_json_new(&out.join("result.json"), &result)?;
            Ok(result)
        }
        Err(error) => {
            input::write_json_new(
                &out.join("result.json"),
                &serde_json::json!({"schema":"rust-native-audio-result-v1","status":"failed","observation_id":observation_id,"error":error,"total_wall_seconds":started.elapsed().as_secs_f64()}),
            )?;
            Err(error)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn boundaries_match_reference_overlap_and_tail() {
        assert_eq!(
            window_bounds(48000 * 10, 48000, 6.0, 3.0).unwrap(),
            vec![(0, 288000), (144000, 432000), (288000, 480000)]
        );
        assert_eq!(
            window_bounds(48000 * 6, 48000, 6.0, 3.0).unwrap(),
            vec![(0, 288000)]
        );
        assert!(window_bounds(8000, 48000, 6.0, 3.0).is_err());
        assert!(window_bounds(48000, 48000, 0.1, 0.05).is_err());
    }
    #[test]
    fn silence_is_identical_one_and_four_workers() {
        let samples = vec![0.0; 48000 * 10];
        let mut config = DecodeConfig::default();
        let a = decode_samples(&samples, 48000, &config).unwrap();
        config.threads = 4;
        let b = decode_samples(&samples, 48000, &config).unwrap();
        assert_eq!(
            serde_json::to_value(a.0).unwrap(),
            serde_json::to_value(b.0).unwrap()
        );
        assert_eq!(
            serde_json::to_value(a.1).unwrap(),
            serde_json::to_value(b.1).unwrap()
        );
    }
    #[test]
    fn microscopic_hops_fail_before_allocation() {
        assert!(window_bounds(48000 * 1800, 48000, 6.0, 1.0 / 48000.0).is_err());
    }
    #[test]
    fn silence_does_not_bypass_invalid_dsp_configuration() {
        let samples = vec![0.0; 8192];
        let mut config = DecodeConfig::default();
        config.dsp.baud = -1.0;
        let (_, rows, _) = decode_samples(&samples, 48000, &config).unwrap();
        assert_eq!(rows.len(), 1);
        assert!(!rows[0].failures.is_empty());
    }
}
