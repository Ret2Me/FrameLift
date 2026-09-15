//! Satellite-neutral demodulator/protocol registry and explicit IQ/audio plans.
use crate::{afsk_legacy, dsp, formats, input, protocol, receiver};
use num_complex::Complex64;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};
use std::time::Instant;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Integrity {
    Crc16X25,
    Crc16CcittFalse,
}
impl Integrity {
    fn label(&self) -> &'static str {
        match self {
            Self::Crc16X25 => "crc16_x25",
            Self::Crc16CcittFalse => "crc16_ccitt_false",
        }
    }
    fn valid(&self, bytes: &[u8]) -> bool {
        match self {
            Self::Crc16X25 => protocol::valid_ax25_fcs(bytes),
            Self::Crc16CcittFalse => protocol::validate_tm_fecf(bytes),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ProtocolConfig {
    Geoscan {
        maximum_sync_hamming: usize,
    },
    Ax25 {
        g3ruh_modes: Vec<bool>,
    },
    Ax25Ccsds {
        g3ruh_modes: Vec<bool>,
        allowed_apids: Option<Vec<u16>>,
    },
    CcsdsTm {
        config: protocol::CcsdsTmDecoderConfig,
    },
    RawCcsds {
        config: protocol::RawCcsdsConfig,
        integrity: Integrity,
    },
    FixedSync {
        config: protocol::FixedSyncConfig,
        integrity: Integrity,
    },
    /// Sync, optional derandomizer and native FEC, then an independent CRC/FECF.
    CodedSync {
        config: crate::coded::CodedSyncConfig,
    },
}

pub trait ProtocolDecoder: Send + Sync {
    fn accepted_symbol_kind(&self) -> &str {
        "binary_soft"
    }
    fn required_demodulator_features(&self) -> Vec<String> {
        vec![]
    }
    /// Bind all opaque configuration for checkpoint reuse. None permits fresh
    /// runs, but prevents reuse after the caller changes an instance's state.
    fn resume_identity(&self) -> Option<String> {
        None
    }
    fn decode(&self, soft: &[f64], threshold: f64) -> Result<Vec<protocol::ProtocolFrame>, String>;
    fn validate(&self) -> Result<(), String>;
}
impl ProtocolDecoder for ProtocolConfig {
    fn resume_identity(&self) -> Option<String> {
        serde_json::to_value(self)
            .ok()
            .and_then(|v| fingerprint(&v).ok())
    }
    fn decode(&self, soft: &[f64], threshold: f64) -> Result<Vec<protocol::ProtocolFrame>, String> {
        match self {
            Self::Geoscan {
                maximum_sync_hamming,
            } => crate::geoscan::decode(soft, threshold, *maximum_sync_hamming),
            Self::Ax25 { g3ruh_modes } => Ok(protocol::decode_ax25(soft, threshold, g3ruh_modes)?
                .into_iter()
                .map(|frame| protocol::ProtocolFrame {
                    frame,
                    validation_layers: vec!["crc16_x25".into(), "ax25_ui".into()],
                })
                .collect()),
            Self::Ax25Ccsds {
                g3ruh_modes,
                allowed_apids,
            } => protocol::decode_ax25_ccsds(
                soft,
                threshold,
                g3ruh_modes,
                allowed_apids.as_deref(),
                None,
            ),
            Self::CcsdsTm { config } => protocol::decode_ccsds_tm(soft, threshold, config, None),
            Self::RawCcsds { config, integrity } => {
                let mut config = config.clone();
                config.validation_name = integrity.label().into();
                protocol::decode_raw_ccsds(soft, threshold, &config, &|packet, _| {
                    integrity.valid(packet)
                })
            }
            Self::FixedSync { config, integrity } => {
                let mut config = config.clone();
                config.validation_name = integrity.label().into();
                protocol::decode_fixed_sync(soft, threshold, &config, None, &|frame| {
                    integrity.valid(frame)
                })
            }
            Self::CodedSync { config } => crate::coded::decode_sync(soft, threshold, config),
        }
    }
    fn validate(&self) -> Result<(), String> {
        match self {
            Self::Ax25 { g3ruh_modes } | Self::Ax25Ccsds { g3ruh_modes, .. }
                if g3ruh_modes.is_empty() =>
            {
                Err("at least one explicit G3RUH mode is required".into())
            }
            Self::CcsdsTm { config } if !config.frame.fecf_present => Err(
                "CLI CCSDS TM requires FECF; custom integrity uses Rust library callback".into(),
            ),
            _ => {
                self.decode(&[], 0.0)?;
                Ok(())
            }
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Waveform {
    pub hypothesis_id: String,
    pub demodulator_id: String,
    pub dsp: dsp::DspConfig,
    pub decimation: usize,
    #[serde(default)]
    pub cutoff_hz: Option<f64>,
    #[serde(default)]
    pub carrier_hz: Option<f64>,
    #[serde(default = "mark")]
    pub mark_hz: f64,
    #[serde(default = "space")]
    pub space_hz: f64,
    /// Coherent IQ PSK only. Omitted legacy plans keep their serialized identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub psk: Option<crate::psk::PskConfig>,
}
fn mark() -> f64 {
    1200.0
}
fn space() -> f64 {
    2200.0
}
pub enum Signal<'a> {
    Pcm(&'a [f64]),
    Iq(&'a [Complex64]),
}

/// Consumes a temporary soft stream, threshold, timing score and optional lane label.
/// The stream is borrowed only for the duration of the callback.
pub type SoftSymbolVisitor<'a> =
    dyn FnMut(&[f64], f64, f64, Option<&str>) -> Result<(), String> + 'a;

pub trait Demodulator: Send + Sync {
    /// Visit one soft stream at a time, retaining the legacy frontend/timing
    /// arithmetic by default. PSK overrides this to preserve I/Q and ambiguity.
    fn visit_soft_symbols<'visit>(
        &self,
        signal: Signal<'_>,
        rate: u32,
        waveform: &Waveform,
        visit: &'visit mut SoftSymbolVisitor<'visit>,
    ) -> Result<usize, String> {
        if waveform.psk.is_some() {
            return Err("PSK configuration supplied to a non-PSK demodulator".into());
        }
        let frontend = self.demodulate(signal, rate, waveform)?;
        let bank = self.timing_bank(&frontend, &waveform.dsp)?;
        for timing in &bank {
            let soft = self.soft_symbols(&frontend, timing)?;
            visit(&soft, timing.threshold, timing.score, None)?;
        }
        Ok(bank.len())
    }
    fn timing_bank(
        &self,
        frontend: &dsp::Frontend,
        config: &dsp::DspConfig,
    ) -> Result<Vec<dsp::TimingHypothesis>, String> {
        dsp::timing_bank(frontend, config)
    }
    fn soft_symbols(
        &self,
        frontend: &dsp::Frontend,
        timing: &dsp::TimingHypothesis,
    ) -> Result<Vec<f64>, String> {
        dsp::soft_symbols(frontend, timing)
    }
    fn output_symbol_kind(&self) -> &str {
        "binary_soft"
    }
    /// Empty means unspecified, preserving existing custom plugin APIs.
    fn modulation_families(&self) -> Vec<String> {
        vec![]
    }
    fn features(&self) -> Vec<String> {
        vec![]
    }
    fn resume_identity(&self) -> Option<String> {
        None
    }
    fn accepts(&self, iq: bool) -> bool;
    fn demodulate(
        &self,
        signal: Signal<'_>,
        rate: u32,
        waveform: &Waveform,
    ) -> Result<dsp::Frontend, String>;
}
struct BuiltinDemodulator(&'static str);
impl Demodulator for BuiltinDemodulator {
    fn timing_bank(
        &self,
        frontend: &dsp::Frontend,
        config: &dsp::DspConfig,
    ) -> Result<Vec<dsp::TimingHypothesis>, String> {
        if self.0 == "bell202_afsk_legacy" {
            afsk_legacy::timing_bank(frontend, config)
        } else {
            dsp::timing_bank(frontend, config)
        }
    }
    fn soft_symbols(
        &self,
        frontend: &dsp::Frontend,
        timing: &dsp::TimingHypothesis,
    ) -> Result<Vec<f64>, String> {
        if self.0 == "bell202_afsk_legacy" {
            afsk_legacy::legacy_soft_symbols(frontend, timing)
        } else {
            dsp::soft_symbols(frontend, timing)
        }
    }
    fn modulation_families(&self) -> Vec<String> {
        match self.0 {
            "pcm_bell202" | "bell202_afsk" | "bell202_afsk_legacy" => vec!["afsk".into()],
            "phase_fsk" | "pcm_fsk" => vec!["fsk".into(), "gfsk".into(), "gmsk".into()],
            _ => vec!["fsk".into()],
        }
    }
    fn features(&self) -> Vec<String> {
        let mut features = vec![
            "binary_threshold_timing_bank",
            "configurable_sample_rate",
            "configurable_symbol_rate",
        ];
        match self.0 {
            "phase_fsk" => features.extend([
                "phase_discriminator_before_real_filter",
                "configurable_decimation",
                "configurable_post_discriminator_cutoff",
            ]),
            "channel_conditioned_phase_fsk" => features.extend([
                "carrier_offset_estimation",
                "complex_channel_filter",
                "phase_discriminator_after_channel_filter",
                "configurable_decimation",
            ]),
            "bell202_afsk" | "bell202_afsk_legacy" => features.extend([
                "fm_audio_discriminator",
                "noncoherent_mark_space_energy",
                "configurable_bell202_tones",
            ]),
            "pcm_bell202" => features.extend([
                "already_fm_demodulated",
                "noncoherent_mark_space_energy",
                "configurable_bell202_tones",
                "configurable_decimation",
            ]),
            "pcm_fsk" => features.extend(["already_fm_demodulated", "configurable_decimation"]),
            "usb_real_passband" => features.extend([
                "explicit_carrier_analytic_audio",
                "phase_discriminator_before_real_filter",
                "configurable_decimation",
            ]),
            _ => (),
        }
        if self.0 == "bell202_afsk_legacy" {
            features.push("legacy_afsk_per_hypothesis_count_16_guard_symbols");
        }
        features.into_iter().map(str::to_owned).collect()
    }
    fn resume_identity(&self) -> Option<String> {
        Some(format!("rust-builtin-demodulator-v1:{}", self.0))
    }
    fn accepts(&self, iq: bool) -> bool {
        matches!(
            self.0,
            "phase_fsk" | "channel_conditioned_phase_fsk" | "bell202_afsk" | "bell202_afsk_legacy"
        ) == iq
    }
    fn demodulate(
        &self,
        signal: Signal<'_>,
        rate: u32,
        w: &Waveform,
    ) -> Result<dsp::Frontend, String> {
        if w.hypothesis_id.trim().is_empty() || w.decimation == 0 {
            return Err("invalid waveform identifier/decimation".into());
        }
        match (self.0, signal) {
            ("pcm_fsk", Signal::Pcm(pcm)) | ("pcm_bell202", Signal::Pcm(pcm)) => {
                let expected = if self.0 == "pcm_fsk" { "fsk" } else { "afsk" };
                if w.dsp.mode != expected || w.carrier_hz.is_some() {
                    return Err("PCM waveform mode/carrier contradicts representation".into());
                }
                dsp::frontend_configured(
                    pcm,
                    rate,
                    &w.dsp,
                    &dsp::PcmFrontendConfig {
                        decimation: w.decimation,
                        cutoff_hz: w.cutoff_hz,
                        mark_hz: w.mark_hz,
                        space_hz: w.space_hz,
                    },
                )
            }
            ("phase_fsk" | "channel_conditioned_phase_fsk", Signal::Iq(iq)) => {
                if w.dsp.mode != "fsk" || w.carrier_hz.is_some() {
                    return Err(
                        "IQ FSK requires FSK family and internally estimated carrier".into(),
                    );
                }
                dsp::iq_frontend(iq, rate, &w.dsp, self.0, w.decimation, w.cutoff_hz)
            }
            ("bell202_afsk" | "bell202_afsk_legacy", Signal::Iq(iq)) => {
                if w.dsp.mode != "afsk" || w.carrier_hz.is_some() || w.cutoff_hz.is_some() {
                    return Err("IQ Bell202 mode/parameters incompatible".into());
                }
                dsp::bell202_iq_frontend(iq, rate, &w.dsp, w.decimation, w.mark_hz, w.space_hz)
            }
            ("usb_real_passband", Signal::Pcm(pcm)) => {
                if w.dsp.mode != "fsk" {
                    return Err(
                        "USB helper currently routes to phase FSK, not arbitrary waveform families"
                            .into(),
                    );
                }
                let iq = dsp::analytic_audio(
                    pcm,
                    rate,
                    w.carrier_hz
                        .ok_or("USB real passband requires explicit carrier_hz")?,
                )?;
                dsp::iq_frontend(&iq, rate, &w.dsp, "phase_fsk", w.decimation, w.cutoff_hz)
            }
            _ => Err("input representation is incompatible with demodulator".into()),
        }
    }
}

#[derive(Default, Clone)]
pub struct GenericReceiver {
    demodulators: HashMap<String, Arc<dyn Demodulator>>,
    protocols: HashMap<String, Arc<dyn ProtocolDecoder>>,
}
impl GenericReceiver {
    pub fn with_builtin_demodulators() -> Self {
        let mut receiver = Self::default();
        for name in [
            "pcm_fsk",
            "pcm_bell202",
            "phase_fsk",
            "channel_conditioned_phase_fsk",
            "bell202_afsk",
            "bell202_afsk_legacy",
            "usb_real_passband",
        ] {
            receiver
                .register_demodulator(name, Arc::new(BuiltinDemodulator(name)))
                .expect("unique builtins");
        }
        for (id, modulation) in [
            ("iq_bpsk", "bpsk"),
            ("iq_qpsk", "qpsk"),
            ("iq_oqpsk", "oqpsk"),
        ] {
            receiver
                .register_demodulator(id, Arc::new(crate::psk::PskDemodulator(modulation)))
                .expect("unique PSK builtins");
        }
        receiver
    }
    pub fn register_demodulator(
        &mut self,
        id: &str,
        decoder: Arc<dyn Demodulator>,
    ) -> Result<(), String> {
        if id.trim().is_empty() || self.demodulators.contains_key(id) {
            return Err("empty or duplicate demodulator ID".into());
        }
        self.demodulators.insert(id.into(), decoder);
        Ok(())
    }
    pub fn register_protocol(
        &mut self,
        id: &str,
        decoder: Arc<dyn ProtocolDecoder>,
    ) -> Result<(), String> {
        if id.trim().is_empty() || self.protocols.contains_key(id) {
            return Err("empty or duplicate protocol ID".into());
        }
        decoder.validate()?;
        self.protocols.insert(id.into(), decoder);
        Ok(())
    }
    fn configured(&self, plan: &Plan) -> Result<Self, String> {
        let mut receiver = self.clone();
        for (id, config) in &plan.protocols {
            receiver.register_protocol(id, Arc::new(config.clone()))?;
        }
        Ok(receiver)
    }
    pub fn validate(&self, plan: &Plan) -> Result<(), String> {
        self.configured(plan)?.validate_resolved(plan)
    }
    fn validate_resolved(&self, plan: &Plan) -> Result<(), String> {
        if plan.sample_rate_hz == 0 || plan.hypotheses.is_empty() || plan.hypotheses.len() > 256 {
            return Err("positive rate and 1..256 explicit hypotheses required".into());
        }
        let mut ids = HashSet::new();
        let mut psk_work = (0u128, 0u128);
        for hypothesis in &plan.hypotheses {
            let w = &hypothesis.waveform;
            if matches!(
                w.demodulator_id.as_str(),
                "iq_bpsk" | "iq_qpsk" | "iq_oqpsk"
            ) {
                crate::psk::validate_waveform(plan.sample_rate_hz, w)?;
                if !plan.window_seconds.is_finite() || plan.window_seconds <= 0.0 {
                    return Err("PSK plan requires a finite positive window".into());
                }
                let samples = (plan.window_seconds * plan.sample_rate_hz as f64).ceil();
                if samples > dsp::MAX_PCM_WINDOW_SAMPLES as f64 {
                    return Err("PSK planned window exceeds sample bound".into());
                }
                let work = crate::psk::work_estimate(plan.sample_rate_hz, w, samples as usize)?;
                psk_work.0 += work.0;
                psk_work.1 += work.1;
                if psk_work.0 > 1_000_000_000 || psk_work.1 > 1_000_000_000 {
                    return Err("PSK aggregate per-window plan exceeds one billion soft-bit visits or frontend work units".into());
                }
            } else if w.psk.is_some() {
                return Err("PSK configuration requires a PSK demodulator".into());
            }
            if w.dsp
                .phase_bins
                .checked_mul(w.dsp.rate_errors_ppm.len())
                .is_none_or(|n| n > 65_536)
            {
                return Err(
                    "generic timing grid exceeds 65536 hypotheses; nothing was pruned".into(),
                );
            }
            if !ids.insert(&w.hypothesis_id)
                || w.hypothesis_id.trim().is_empty()
                || w.decimation == 0
            {
                return Err("unique waveform ID and positive decimation required".into());
            }
            let d = self
                .demodulators
                .get(&w.demodulator_id)
                .ok_or_else(|| format!("unsupported demodulator: {}", w.demodulator_id))?;
            let p = self
                .protocols
                .get(&hypothesis.protocol_id)
                .ok_or("unknown protocol ID")?;
            if !d.accepts(plan.format.is_iq()) || d.output_symbol_kind() != p.accepted_symbol_kind()
            {
                return Err("incompatible input/demodulator/protocol contract".into());
            }
            let families = d.modulation_families();
            if !families.is_empty()
                && !families
                    .iter()
                    .any(|family| family.eq_ignore_ascii_case(&w.dsp.mode))
            {
                return Err(
                    "waveform modulation family contradicts registered demodulator capabilities"
                        .into(),
                );
            }
            let features = d.features();
            if p.required_demodulator_features()
                .iter()
                .any(|feature| !features.contains(feature))
            {
                return Err("registered demodulator lacks required protocol features".into());
            }
        }
        Ok(())
    }
    pub fn capabilities(&self) -> Value {
        let demodulators: BTreeMap<_,_> = self.demodulators.iter().map(|(id,d)|(id.clone(),json!({
            "modulation_families":d.modulation_families(),"features":d.features(),
            "output_symbol_kind":d.output_symbol_kind(),"accepts_iq":d.accepts(true),"accepts_pcm":d.accepts(false),
            "resume_identity":d.resume_identity()
        }))).collect();
        let protocols: BTreeMap<_,_> = self.protocols.iter().map(|(id,p)|(id.clone(),json!({
            "accepted_symbol_kind":p.accepted_symbol_kind(),"required_demodulator_features":p.required_demodulator_features(),
            "resume_identity":p.resume_identity()
        }))).collect();
        json!({"demodulators":demodulators,"protocols":protocols})
    }
    pub fn decode_file(
        &self,
        path: &Path,
        output: &Path,
        plan: &Plan,
        threads: usize,
    ) -> Result<Value, String> {
        self.decode_file_resumable(path, output, plan, threads, false)
    }
    pub fn decode_file_resumable(
        &self,
        path: &Path,
        output: &Path,
        plan: &Plan,
        threads: usize,
        resume: bool,
    ) -> Result<Value, String> {
        decode_entry(self, path, output, plan, threads, None, resume)
    }
    pub fn decode_metadata(
        &self,
        metadata: &formats::RawIqMetadata,
        output: &Path,
        plan: &Plan,
        threads: usize,
    ) -> Result<Value, String> {
        self.decode_metadata_resumable(metadata, output, plan, threads, false)
    }
    pub fn decode_metadata_resumable(
        &self,
        metadata: &formats::RawIqMetadata,
        output: &Path,
        plan: &Plan,
        threads: usize,
        resume: bool,
    ) -> Result<Value, String> {
        decode_entry(
            self,
            &metadata.data_path,
            output,
            plan,
            threads,
            Some(metadata),
            resume,
        )
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputFormat {
    Audio,
    Ci16Le,
    Cf32Le,
    Cf64Le,
    /// Encoding/order/scale must come from an independently validated manifest.
    Metadata,
}
impl InputFormat {
    fn is_iq(&self) -> bool {
        !matches!(self, Self::Audio)
    }
    fn width(&self) -> usize {
        match self {
            Self::Audio | Self::Metadata => 0,
            Self::Ci16Le => 4,
            Self::Cf32Le => 8,
            Self::Cf64Le => 16,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedHypothesis {
    pub waveform: Waveform,
    pub protocol_id: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    pub format: InputFormat,
    pub sample_rate_hz: u32,
    pub window_seconds: f64,
    pub hop_seconds: f64,
    #[serde(default)]
    pub segment_start_sample: usize,
    #[serde(default)]
    pub segment_sample_count: Option<usize>,
    pub protocols: BTreeMap<String, ProtocolConfig>,
    pub hypotheses: Vec<PlannedHypothesis>,
}

pub fn read_iq_window(
    path: &Path,
    format: &InputFormat,
    start: usize,
    count: usize,
) -> Result<Vec<Complex64>, String> {
    let width = format.width();
    if width == 0 || count > dsp::MAX_PCM_WINDOW_SAMPLES {
        return Err("invalid IQ format or window size".into());
    }
    let offset = start.checked_mul(width).ok_or("IQ byte offset overflow")?;
    let size = count.checked_mul(width).ok_or("IQ byte length overflow")?;
    let file = input::open_regular(path)?;
    let file_len = file.metadata().map_err(|e| e.to_string())?.len();
    if file_len % width as u64 != 0 || (offset as u128 + size as u128) > file_len as u128 {
        return Err("IQ file shape or requested range is invalid".into());
    }
    let mut reader = BufReader::new(file);
    reader
        .seek(SeekFrom::Start(offset as u64))
        .map_err(|e| e.to_string())?;
    let mut raw = vec![0u8; size];
    reader.read_exact(&mut raw).map_err(|e| e.to_string())?;
    let values = raw
        .chunks_exact(width)
        .map(|s| match format {
            InputFormat::Ci16Le => Complex64::new(
                i16::from_le_bytes(s[..2].try_into().unwrap()) as f64,
                i16::from_le_bytes(s[2..].try_into().unwrap()) as f64,
            ),
            InputFormat::Cf32Le => Complex64::new(
                f32::from_le_bytes(s[..4].try_into().unwrap()) as f64,
                f32::from_le_bytes(s[4..].try_into().unwrap()) as f64,
            ),
            InputFormat::Cf64Le => Complex64::new(
                f64::from_le_bytes(s[..8].try_into().unwrap()),
                f64::from_le_bytes(s[8..].try_into().unwrap()),
            ),
            InputFormat::Audio | InputFormat::Metadata => unreachable!(),
        })
        .collect::<Vec<_>>();
    if values
        .iter()
        .any(|v| !v.re.is_finite() || !v.im.is_finite())
    {
        return Err("non-finite IQ sample".into());
    }
    Ok(values)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GenericProvenance {
    pub window_start_seconds: f64,
    pub waveform: String,
    pub timing_rank: usize,
    pub timing_score: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub symbol_variant: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GenericFrame {
    pub frame_hex: String,
    pub frame_sha256: String,
    pub frame_bytes: usize,
    pub protocol_id: String,
    pub validation_layers: Vec<String>,
    pub provenance: Vec<GenericProvenance>,
}

const MAX_CHECKPOINT_BYTES: u64 = 512 * 1024 * 1024;
const MAX_WINDOW_CHECKPOINT_BYTES: u64 = 64 * 1024 * 1024;

struct SourceGuard {
    path: PathBuf,
    canonical: PathBuf,
    file: File,
    before: fs::Metadata,
}
impl SourceGuard {
    fn open(path: &Path) -> Result<Self, String> {
        let canonical = path.canonicalize().map_err(|e| e.to_string())?;
        let file = input::open_regular(path)?;
        let before = file.metadata().map_err(|e| e.to_string())?;
        let guard = Self {
            path: path.to_path_buf(),
            canonical,
            file,
            before,
        };
        guard.unchanged()?;
        Ok(guard)
    }
    fn unchanged(&self) -> Result<(), String> {
        if self.path.canonicalize().map_err(|e| e.to_string())? != self.canonical {
            return Err("input pathname identity changed".into());
        }
        let after = self.file.metadata().map_err(|e| e.to_string())?;
        let current = fs::metadata(&self.path).map_err(|e| e.to_string())?;
        if after.len() != self.before.len()
            || current.len() != self.before.len()
            || after.modified().ok() != self.before.modified().ok()
            || current.modified().ok() != self.before.modified().ok()
        {
            return Err("source input changed before window commit".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if after.dev() != self.before.dev()
                || after.ino() != self.before.ino()
                || current.dev() != self.before.dev()
                || current.ino() != self.before.ino()
                || after.ctime() != self.before.ctime()
                || after.ctime_nsec() != self.before.ctime_nsec()
                || current.ctime() != self.before.ctime()
                || current.ctime_nsec() != self.before.ctime_nsec()
            {
                return Err("source input inode/ctime changed before window commit".into());
            }
        }
        Ok(())
    }
}

fn fingerprint(value: &Value) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        crate::ledger::canonical_json(value)?.as_bytes(),
    )))
}
fn owned_directory(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(format!("owned directory required: {}", path.display()));
    }
    Ok(())
}
fn owned_regular(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(format!(
            "owned regular artifact required: {}",
            path.display()
        ));
    }
    Ok(())
}
fn present(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.to_string()),
    }
}
fn checkpoint_disk_bytes(root: &Path) -> Result<u64, String> {
    let mut pending = vec![(root.to_path_buf(), 0usize)];
    let (mut bytes, mut count) = (0u64, 0usize);
    while let Some((directory, depth)) = pending.pop() {
        if depth > 64 {
            return Err("checkpoint directory depth exceeds 64".into());
        }
        owned_directory(&directory)?;
        for entry in fs::read_dir(directory).map_err(|e| e.to_string())? {
            let path = entry.map_err(|e| e.to_string())?.path();
            count += 1;
            if count > 300_000 {
                return Err("checkpoint tree exceeds 300000 artifacts".into());
            }
            let metadata = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
            if metadata.is_dir() && !metadata.file_type().is_symlink() {
                pending.push((path, depth + 1));
            } else {
                owned_regular(&path)?;
                bytes = bytes
                    .checked_add(metadata.len())
                    .ok_or("checkpoint byte count overflow")?;
                if bytes > MAX_CHECKPOINT_BYTES {
                    return Err("checkpoint tree exceeds 512 MiB".into());
                }
            }
        }
    }
    Ok(bytes)
}
struct RunStore {
    root: PathBuf,
    lock: File,
    root_metadata: fs::Metadata,
    bytes: AtomicU64,
}
impl RunStore {
    fn open(output: &Path, resume: bool) -> Result<Self, String> {
        let root = if resume {
            owned_directory(output)?;
            output.canonicalize().map_err(|e| e.to_string())?
        } else {
            input::existing_new_dir(output)?
        };
        let root_metadata = fs::metadata(&root).map_err(|e| e.to_string())?;
        let lock_path = root.join(".generic-lock");
        if resume {
            owned_regular(&lock_path)?;
        }
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(!resume);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let lock = options.open(&lock_path).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return Err(
                    "generic output has a live lock owner; no process was signalled".into(),
                );
            }
        }
        #[cfg(not(unix))]
        return Err("durable generic output locking currently requires Unix".into());
        let store = Self {
            bytes: AtomicU64::new(checkpoint_disk_bytes(&root)?),
            root,
            lock,
            root_metadata,
        };
        store.owned()?;
        Ok(store)
    }
    fn owned(&self) -> Result<(), String> {
        owned_directory(&self.root)?;
        let lock_path = self.root.join(".generic-lock");
        owned_regular(&lock_path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let root = fs::metadata(&self.root).map_err(|e| e.to_string())?;
            let current = fs::metadata(lock_path).map_err(|e| e.to_string())?;
            let opened = self.lock.metadata().map_err(|e| e.to_string())?;
            if root.dev() != self.root_metadata.dev()
                || root.ino() != self.root_metadata.ino()
                || current.dev() != opened.dev()
                || current.ino() != opened.ino()
            {
                return Err("owned generic root/lock pathname was replaced".into());
            }
        }
        Ok(())
    }
    fn path(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }
    fn directory(&self, name: &str, create: bool) -> Result<PathBuf, String> {
        self.owned()?;
        let path = self.path(name);
        if create {
            fs::create_dir(&path).map_err(|e| e.to_string())?;
        }
        owned_directory(&path)?;
        Ok(path)
    }
    fn read(&self, path: &Path) -> Result<Value, String> {
        self.owned()?;
        owned_regular(path)?;
        if !path
            .canonicalize()
            .map_err(|e| e.to_string())?
            .starts_with(&self.root)
        {
            return Err("checkpoint artifact escapes output".into());
        }
        let before = input::identity(path)?;
        let value = input::read_json(path)?;
        if input::identity(path)?.sha256 != before.sha256 {
            return Err("checkpoint changed while reading".into());
        }
        Ok(value)
    }
    fn reserve(&self, amount: u64) -> Result<(), String> {
        self.bytes
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |used| {
                used.checked_add(amount)
                    .filter(|n| *n <= MAX_CHECKPOINT_BYTES)
            })
            .map_err(|_| {
                "generic checkpoint output exceeds 512 MiB; no window was pruned".to_owned()
            })?;
        Ok(())
    }
    fn write_bytes(&self, path: &Path, bytes: &[u8]) -> Result<(), String> {
        self.owned()?;
        let parent = path.parent().ok_or("checkpoint parent missing")?;
        owned_directory(parent)?;
        if !parent
            .canonicalize()
            .map_err(|e| e.to_string())?
            .starts_with(&self.root)
        {
            return Err("checkpoint parent escapes output".into());
        }
        if bytes.len() as u64 > MAX_WINDOW_CHECKPOINT_BYTES {
            return Err("single checkpoint artifact exceeds 64 MiB".into());
        }
        self.reserve(bytes.len() as u64)?;
        let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        file.write_all(bytes).map_err(|e| e.to_string())?;
        file.as_file().sync_all().map_err(|e| e.to_string())?;
        self.owned()?;
        file.persist_noclobber(path).map_err(|e| e.to_string())?;
        File::open(parent)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())
    }
    fn write(&self, path: &Path, value: &Value) -> Result<(), String> {
        let mut bytes = serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?;
        bytes.push(b'\n');
        self.write_bytes(path, &bytes)
    }
    fn attempt(&self) -> Result<PathBuf, String> {
        let parent = self.directory("attempts", false)?;
        let path = tempfile::Builder::new()
            .prefix("attempt-")
            .tempdir_in(parent)
            .map_err(|e| e.to_string())?
            .keep();
        File::open(&self.root)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        Ok(path)
    }
}
impl Drop for RunStore {
    fn drop(&mut self) {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            unsafe {
                libc::flock(self.lock.as_raw_fd(), libc::LOCK_UN);
            }
        }
    }
}

// Inputs are required to remain immutable while a run executes. Metadata
// checks catch ordinary concurrent writes; full boundary hashes catch content
// changes even on filesystems with coarsely updated metadata. Neither is an
// authentication scheme against deliberate coherent concurrent rewriting.
fn invalidate_source(store: &RunStore, error: &str) -> Result<(), String> {
    store.owned()?;
    let path = store.path("source-invalidated.json");
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    match options.open(&path) {
        Ok(mut file) => {
            // A small emergency marker must remain possible even when the
            // ordinary checkpoint budget has been consumed. Its existence
            // alone invalidates reuse, including an interrupted partial write.
            let reason: String = error.chars().take(512).collect();
            serde_json::to_writer(
                &mut file,
                &json!({
                    "schema":"rust-generic-source-invalidated-v1",
                    "reason":reason,"checkpoint_reuse_allowed":false
                }),
            )
            .map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            File::open(&store.root)
                .and_then(|f| f.sync_all())
                .map_err(|e| e.to_string())
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => owned_regular(&path),
        Err(e) => Err(e.to_string()),
    }
}

fn check_runtime_guards(
    store: &RunStore,
    source: &SourceGuard,
    executable: &SourceGuard,
) -> Result<(), String> {
    if let Err(error) = source.unchanged().and_then(|()| executable.unchanged()) {
        invalidate_source(store, &error)?;
        return Err(error);
    }
    Ok(())
}

fn check_source_final(
    store: &RunStore,
    source: &SourceGuard,
    executable: &SourceGuard,
    expected_sha256: &str,
) -> Result<(), String> {
    let checked = (|| {
        let after = input::identity(&source.path)?;
        source.unchanged()?;
        executable.unchanged()?;
        if after.sha256 != expected_sha256 {
            return Err("input changed during generic processing".to_string());
        }
        Ok(())
    })();
    if let Err(error) = checked {
        invalidate_source(store, &error)?;
        return Err(error);
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CompletedWindow {
    row: Value,
    frames: Vec<GenericFrame>,
}
fn window_record(
    contract: &str,
    index: usize,
    start: usize,
    stop: usize,
    window: &CompletedWindow,
) -> Result<Value, String> {
    let mut value = json!({"schema":"rust-generic-window-commit-v1","contract_sha256":contract,
        "index":index,"start_sample":start,"stop_sample":stop,"window":window});
    value["content_sha256"] = json!(fingerprint(&value)?);
    Ok(value)
}
fn verify_window(
    value: Value,
    contract: &str,
    index: usize,
    start: usize,
    stop: usize,
    plan: &Plan,
) -> Result<CompletedWindow, String> {
    let mut unhashed = value.clone();
    let hash = unhashed
        .as_object_mut()
        .ok_or("checkpoint must be object")?
        .remove("content_sha256")
        .ok_or("checkpoint hash missing")?;
    if hash != fingerprint(&unhashed)?
        || value["schema"] != "rust-generic-window-commit-v1"
        || value["contract_sha256"] != contract
        || value["index"] != index
        || value["start_sample"] != start
        || value["stop_sample"] != stop
    {
        return Err("window checkpoint contract/index/content mismatch".into());
    }
    let window: CompletedWindow =
        serde_json::from_value(value["window"].clone()).map_err(|e| e.to_string())?;
    let offset = plan.segment_start_sample + start;
    let time = offset as f64 / plan.sample_rate_hz as f64;
    if window.row["offset_seconds"] != time
        || window.row["samples"] != stop - start
        || window.row["frame_instances"] != window.frames.len()
        || !window.row["failures"].as_array().is_some_and(Vec::is_empty)
        || window.row["timing_attempts"].as_u64().is_none()
        || window.row.get("unique_total").is_some()
    {
        return Err("cached window is failed or has invalid geometry/counts".into());
    }
    let mut keys = HashSet::new();
    for frame in &window.frames {
        let bytes = hex::decode(&frame.frame_hex).map_err(|e| e.to_string())?;
        if bytes.is_empty()
            || bytes.len() != frame.frame_bytes
            || hex::encode(Sha256::digest(&bytes)) != frame.frame_sha256
            || frame.validation_layers.is_empty()
            || frame.validation_layers.iter().any(|s| s.trim().is_empty())
            || frame.provenance.is_empty()
            || !keys.insert((&frame.protocol_id, &frame.frame_hex))
        {
            return Err("cached frame identity/validation/provenance invalid".into());
        }
        if matches!(
            plan.protocols.get(&frame.protocol_id),
            Some(ProtocolConfig::Ax25 { .. } | ProtocolConfig::Ax25Ccsds { .. })
        ) && (!protocol::valid_ax25_fcs(&bytes)
            || !protocol::valid_ax25_ui(&bytes[..bytes.len() - 2]))
        {
            return Err("cached AX25 frame fails independent boundary replay".into());
        }
        if let Some(ProtocolConfig::CodedSync { config }) = plan.protocols.get(&frame.protocol_id) {
            if bytes.len() != config.frame_bytes {
                return Err("cached coded frame has wrong decoded length".into());
            }
            let layers = config.validator.decode(&bytes)?;
            if layers
                .iter()
                .any(|layer| !frame.validation_layers.contains(layer))
            {
                return Err("cached coded frame lost required integrity validation".into());
            }
            let required = match &config.code {
                crate::fec::FrameCode::None => None,
                crate::fec::FrameCode::ReedSolomon { .. } => Some("reed_solomon_syndrome_verified"),
                crate::fec::FrameCode::Ldpc { .. } => Some("ldpc_syndrome_verified"),
            };
            if required.is_some_and(|label| !frame.validation_layers.iter().any(|s| s == label)) {
                return Err("cached coded frame lost required FEC provenance".into());
            }
        }
        for origin in &frame.provenance {
            if !origin.timing_score.is_finite()
                || origin.window_start_seconds != time
                || !plan.hypotheses.iter().any(|h| {
                    h.protocol_id == frame.protocol_id
                        && h.waveform.hypothesis_id == origin.waveform
                })
                || origin.timing_rank as u64 >= window.row["timing_attempts"].as_u64().unwrap()
            {
                return Err("cached frame origin is outside executed hypothesis/window".into());
            }
        }
    }
    Ok(window)
}

pub fn decode_file(
    path: &Path,
    output: &Path,
    plan: &Plan,
    threads: usize,
) -> Result<Value, String> {
    decode_file_resumable(path, output, plan, threads, false)
}

pub fn decode_file_resumable(
    path: &Path,
    output: &Path,
    plan: &Plan,
    threads: usize,
    resume: bool,
) -> Result<Value, String> {
    GenericReceiver::with_builtin_demodulators()
        .decode_file_resumable(path, output, plan, threads, resume)
}

pub fn decode_metadata(
    metadata: &formats::RawIqMetadata,
    output: &Path,
    plan: &Plan,
    threads: usize,
) -> Result<Value, String> {
    decode_metadata_resumable(metadata, output, plan, threads, false)
}

pub fn decode_metadata_resumable(
    metadata: &formats::RawIqMetadata,
    output: &Path,
    plan: &Plan,
    threads: usize,
    resume: bool,
) -> Result<Value, String> {
    GenericReceiver::with_builtin_demodulators()
        .decode_metadata_resumable(metadata, output, plan, threads, resume)
}

fn decode_entry(
    registry: &GenericReceiver,
    path: &Path,
    output: &Path,
    plan: &Plan,
    threads: usize,
    metadata: Option<&formats::RawIqMetadata>,
    resume: bool,
) -> Result<Value, String> {
    let store = RunStore::open(output, resume)?;
    match decode_into(registry, path, &store, plan, threads, metadata, resume) {
        Ok(value) => Ok(value),
        Err(error) => {
            let record = json!({"schema":"rust-generic-receiver-result-v1","status":"failed","error":error,
                "plan":plan,"python_runtime_required":false,"publication_ready":false,"deployment_ready":false});
            // Resume authentication errors never mutate an existing result.
            if !resume && !present(&store.path("result.json"))? {
                store
                    .write(&store.path("result.json"), &record)
                    .map_err(|write_error| {
                        format!("{error}; failed to persist failure: {write_error}")
                    })?;
            }
            Err(error)
        }
    }
}

fn decode_into(
    registry: &GenericReceiver,
    path: &Path,
    store: &RunStore,
    plan: &Plan,
    threads: usize,
    metadata: Option<&formats::RawIqMetadata>,
    resume: bool,
) -> Result<Value, String> {
    let out = &store.root;
    if present(&store.path("source-invalidated.json"))? {
        return Err("run observed a source/executable change; invalidated checkpoints cannot be resumed even if the input is restored".into());
    }
    if matches!(plan.format, InputFormat::Metadata) != metadata.is_some() {
        return Err("metadata input format requires decode-metadata with a validated manifest; raw plans cannot silently override metadata".into());
    }
    if !(1..=64).contains(&threads) {
        return Err("threads must be in 1..=64".into());
    }
    let receiver = registry.configured(plan)?;
    receiver.validate_resolved(plan)?;
    let registry_contract = receiver.capabilities();
    if resume
        && receiver
            .demodulators
            .values()
            .any(|d| d.resume_identity().is_none_or(|s| s.trim().is_empty()))
        || resume
            && receiver
                .protocols
                .values()
                .any(|p| p.resume_identity().is_none_or(|s| s.trim().is_empty()))
    {
        return Err(
            "resumable custom plugins must declare deterministic complete configuration identities"
                .into(),
        );
    }
    let started = Instant::now();
    let source_guard = SourceGuard::open(path)?;
    let source = input::identity(path)?;
    source_guard.unchanged()?;
    if let Some(meta) = metadata
        && (meta.verified_sha256 != source.sha256
            || meta.size_bytes != source.bytes
            || meta.sample_rate_hz != plan.sample_rate_hz as f64)
    {
        return Err("IQ metadata identity/rate does not match recording and explicit plan".into());
    }
    let audio = if plan.format.is_iq() {
        None
    } else {
        Some(input::load_audio(path, out)?)
    };
    let available = if let Some(audio) = &audio {
        if audio.sample_rate != plan.sample_rate_hz {
            return Err("declared sample rate does not match audio; no implicit resampling".into());
        }
        audio.samples.len()
    } else if let Some(meta) = metadata {
        usize::try_from(meta.complex_sample_count).map_err(|e| e.to_string())?
    } else {
        if source.bytes % plan.format.width() as u64 != 0 {
            return Err("IQ input is not an exact number of complex samples".into());
        }
        usize::try_from(source.bytes / plan.format.width() as u64).map_err(|e| e.to_string())?
    };
    let count = plan.segment_sample_count.unwrap_or(
        available
            .checked_sub(plan.segment_start_sample)
            .ok_or("segment starts beyond input")?,
    );
    let end = plan
        .segment_start_sample
        .checked_add(count)
        .ok_or("segment overflow")?;
    if end > available || count as f64 / plan.sample_rate_hz as f64 > input::MAX_AUDIO_SECONDS {
        return Err("segment exceeds file or 1800 seconds".into());
    }
    let bounds = receiver::window_bounds(
        count,
        plan.sample_rate_hz,
        plan.window_seconds,
        plan.hop_seconds,
    )?;
    let largest = bounds.iter().map(|&(a, b)| b - a).max().unwrap_or(0);
    let loaded_bytes = audio.as_ref().map_or(0, |a| a.samples.len() as u128 * 8);
    if loaded_bytes + largest as u128 * 16 * 24 * threads as u128 > 6u128 * 1024 * 1024 * 1024 {
        return Err("generic worker estimate exceeds 6GiB".into());
    }
    let executable_path = std::env::current_exe().map_err(|e| e.to_string())?;
    let executable_guard = SourceGuard::open(&executable_path)?;
    let executable = input::identity(&executable_path)?;
    executable_guard.unchanged()?;
    let plan_document = json!({"schema":"rust-generic-receiver-plan-v1","plan":plan,"source":source,"threads":threads,
        "compute_identity":crate::compute::current().identity(),
        "executable":executable,"validated_iq_metadata":metadata,
        "reference_bytes_used_for_search":false,"input_pcm":audio.as_ref().map(|a|&a.wav_identity)});
    let contract = json!({"schema":"rust-generic-run-contract-v1","plan":plan,"source":source,"threads":threads,
        "compute_identity":crate::compute::current().identity(),
        "executable":executable,"validated_iq_metadata":metadata,"registry":registry_contract,"bounds":bounds,
        "pcm":audio.as_ref().map(|a|json!({"sha256":a.wav_identity.sha256,"bytes":a.wav_identity.bytes,"sample_rate":a.sample_rate})),
        "checkpoint_policy":"immutable successful windows; failures and unfinished attempts are never cached as success"});
    let contract_hash = fingerprint(&contract)?;
    let checkpoints = store.directory("windows", !resume)?;
    store.directory("attempts", !resume)?;
    if resume {
        if store.read(&store.path("run-contract.json"))? != contract {
            return Err("resume input/executable/plan/metadata/registry contract mismatch".into());
        }
        let mut previous_plan = store.read(&store.path("plan.json"))?;
        let mut expected_plan = plan_document.clone();
        // OGG conversion uses a fresh owned temporary path, never a different PCM identity.
        if previous_plan["input_pcm"].is_object() {
            previous_plan["input_pcm"]["path"] = Value::Null;
        }
        if expected_plan["input_pcm"].is_object() {
            expected_plan["input_pcm"]["path"] = Value::Null;
        }
        if previous_plan != expected_plan {
            return Err("resume immutable plan document mismatch".into());
        }
    } else {
        store.write(&store.path("plan.json"), &plan_document)?;
        store.write(&store.path("run-contract.json"), &contract)?;
    }
    let mut cached = vec![None; bounds.len()];
    for entry in fs::read_dir(&checkpoints).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        owned_regular(&entry.path())?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "non-UTF8 checkpoint name")?;
        // A crash can leave an unpublished temporary file. It is retained,
        // counts toward the resource budget, and is never reused as a window.
        if name.starts_with(".tmp") {
            continue;
        }
        let index = name
            .strip_suffix(".json")
            .and_then(|s| s.parse::<usize>().ok())
            .ok_or("unexpected window checkpoint artifact")?;
        if index >= bounds.len() || name != format!("{index:08}.json") {
            return Err("checkpoint index outside declared window schedule".into());
        }
        let (start, stop) = bounds[index];
        cached[index] = Some(verify_window(
            store.read(&entry.path())?,
            &contract_hash,
            index,
            start,
            stop,
            plan,
        )?);
    }
    let complete = if present(&store.path("completion.json"))? {
        let commit = store.read(&store.path("completion.json"))?;
        if commit["schema"] != "rust-generic-completion-v1"
            || commit["contract_sha256"] != contract_hash
            || cached.iter().any(Option::is_none)
        {
            return Err("completion lacks the complete hash-verified window schedule".into());
        }
        for (key, name) in [("result", "result.json"), ("journal", "windows.jsonl")] {
            owned_regular(&store.path(name))?;
            if serde_json::to_value(input::identity(&store.path(name))?)
                .map_err(|e| e.to_string())?
                != commit[key]
            {
                return Err("completion artifact identity mismatch".into());
            }
        }
        Some(store.read(&store.path("result.json"))?)
    } else {
        None
    };
    let attempt = if complete.is_none() {
        let attempt = store.attempt()?;
        store.write(&attempt.join("start.json"), &json!({"contract_sha256":contract_hash,"resume":resume,
            "compute":crate::compute::current().report(),
            "cached_windows":cached.iter().filter(|w|w.is_some()).count(),"window_count":bounds.len(),"threads":threads}))?;
        // Preserve failed or interrupted final publication artifacts rather
        // than overwriting them. Only a validated run contract permits this.
        for name in ["result.json", "windows.jsonl"] {
            let path = store.path(name);
            if present(&path)? {
                owned_regular(&path)?;
                fs::rename(&path, attempt.join(format!("prior-{name}")))
                    .map_err(|e| e.to_string())?;
            }
        }
        File::open(out)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        Some(attempt)
    } else {
        None
    };
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .map_err(|e| e.to_string())?;
    let windows:Vec<_>=pool.install(||bounds.par_iter().enumerate().map(|(index,&(start,stop))|->Result<CompletedWindow,String>{
        check_runtime_guards(store,&source_guard,&executable_guard)?;
        if let Some(window)=&cached[index] {return Ok(window.clone());}
        let decoded_window=(||->Result<CompletedWindow,String>{
        let offset=plan.segment_start_sample+start;let length=stop-start;
        let iq=if let Some(meta)=metadata {Some(read_metadata_window(meta,offset,length)?)}
            else if plan.format.is_iq() {Some(read_iq_window(path,&plan.format,offset,length)?)}else{None};
        let mut frames:Vec<GenericFrame>=Vec::new();let mut seen:HashMap<(Vec<u8>,String),usize>=HashMap::new();let mut failures=Vec::new();let mut timing_attempts=0;
        for hypothesis in &plan.hypotheses {
            let w=&hypothesis.waveform;
            let decoded=(||->Result<(),String>{
                let signal=if let Some(iq)=&iq {Signal::Iq(iq)}else{Signal::Pcm(&audio.as_ref().unwrap().samples[offset..offset+length])};
                let mut rank=0;
                let attempts=receiver.demodulators[&w.demodulator_id].visit_soft_symbols(signal,plan.sample_rate_hz,w,&mut |soft,threshold,score,variant| {
                    for frame in receiver.protocols[&hypothesis.protocol_id].decode(soft,threshold)? {
                        let key=(frame.frame.clone(),hypothesis.protocol_id.clone());
                        let provenance=GenericProvenance {window_start_seconds:offset as f64/plan.sample_rate_hz as f64,waveform:w.hypothesis_id.clone(),timing_rank:rank,timing_score:score,symbol_variant:variant.map(str::to_owned)};
                        if let Some(index)=seen.get(&key) {frames[*index].provenance.push(provenance);continue;}
                        seen.insert(key,frames.len());
                        frames.push(GenericFrame {frame_hex:hex::encode(&frame.frame),frame_sha256:hex::encode(Sha256::digest(&frame.frame)),frame_bytes:frame.frame.len(),
                            protocol_id:hypothesis.protocol_id.clone(),validation_layers:frame.validation_layers,
                            provenance:vec![provenance]});
                    }
                    rank+=1;
                    Ok(())
                })?;
                timing_attempts+=attempts;
                Ok(())
            })();
            if let Err(error)=decoded {failures.push(format!("{}: {error}",w.hypothesis_id));}
        }
        Ok(CompletedWindow {row:json!({"offset_seconds":offset as f64/plan.sample_rate_hz as f64,"samples":length,"frame_instances":frames.len(),"failures":failures,"timing_attempts":timing_attempts}),frames})
        })();
        check_runtime_guards(store,&source_guard,&executable_guard)?;
        match &decoded_window {
            Ok(window) if window.row["failures"].as_array().is_some_and(Vec::is_empty)=>{
                let record=window_record(&contract_hash,index,start,stop,window)?;
                // Validate the serialized replay boundary before publication,
                // including original FCS for built-in AX25 protocol routes.
                verify_window(record.clone(),&contract_hash,index,start,stop,plan)?;
                store.write(&checkpoints.join(format!("{index:08}.json")),&record)?;
            },
            Ok(window)=>store.write(&attempt.as_ref().ok_or("failed window in terminal resume")?.join(format!("window-{index:08}-failed.json")),
                &json!({"status":"failed","contract_sha256":contract_hash,"index":index,"window":window}))?,
            Err(error)=>store.write(&attempt.as_ref().ok_or("failed window in terminal resume")?.join(format!("window-{index:08}-failed.json")),
                &json!({"status":"failed","contract_sha256":contract_hash,"index":index,"error":error}))?,
        }
        decoded_window
    }).collect());
    let mut unique: BTreeMap<(String, String), GenericFrame> = BTreeMap::new();
    let mut rows = Vec::new();
    for window in windows {
        let CompletedWindow { mut row, frames } = match window {
            Ok(window) => window,
            Err(error) => {
                // Failed execution must still check the immutable-source
                // boundary before retaining already committed windows.
                let error = match check_source_final(
                    store,
                    &source_guard,
                    &executable_guard,
                    &source.sha256,
                ) {
                    Ok(()) => error,
                    Err(source_error) => format!("{error}; {source_error}"),
                };
                if let Some(attempt) = &attempt {
                    let failure = json!({"schema":"rust-generic-receiver-result-v1","status":"failed","error":error,
                        "plan":plan,"python_runtime_required":false,"publication_ready":false,"deployment_ready":false});
                    store.write(&attempt.join("failure.json"), &failure)?;
                    if !present(&store.path("result.json"))? {
                        store.write(&store.path("result.json"), &failure)?;
                    }
                }
                return Err(error);
            }
        };
        for frame in frames {
            unique
                .entry((frame.protocol_id.clone(), frame.frame_hex.clone()))
                .and_modify(|old| old.provenance.extend(frame.provenance.clone()))
                .or_insert(frame);
        }
        row["unique_total"] = json!(unique.len());
        rows.push(row);
    }
    check_source_final(store, &source_guard, &executable_guard, &source.sha256)?;
    let mut journal = Vec::new();
    for row in &rows {
        serde_json::to_writer(&mut journal, row).map_err(|e| e.to_string())?;
        journal.write_all(b"\n").map_err(|e| e.to_string())?;
    }
    let failures = rows
        .iter()
        .filter(|row| !row["failures"].as_array().unwrap().is_empty())
        .count();
    let frames = unique.into_values().collect::<Vec<_>>();
    let result = json!({"schema":"rust-generic-receiver-result-v1","status":if failures==0 {"complete"}else{"failed"},"source":source,"plan":plan,
        "window_count":rows.len(),"failed_window_count":failures,"unique_frame_count":frames.len(),"frames":frames,
        "provenance_policy":"all ordered timing and waveform origins; additive over legacy first-origin-only generic policy",
        "elapsed_seconds":started.elapsed().as_secs_f64(),"python_runtime_required":false,"publication_ready":false,"deployment_ready":false});
    if let Some(previous) = complete {
        let mut expected = result.clone();
        let mut recorded = previous.clone();
        expected.as_object_mut().unwrap().remove("elapsed_seconds");
        recorded
            .as_object_mut()
            .ok_or("completed result must be an object")?
            .remove("elapsed_seconds");
        if expected != recorded
            || input::read_bytes_bounded(&store.path("windows.jsonl"), MAX_WINDOW_CHECKPOINT_BYTES)?
                != journal
        {
            return Err(
                "completed result differs from independently merged window checkpoints".into(),
            );
        }
        return Ok(previous);
    }
    store.write_bytes(&store.path("windows.jsonl"), &journal)?;
    store.write(&store.path("result.json"), &result)?;
    if failures == 0 {
        store.write(&store.path("completion.json"),&json!({"schema":"rust-generic-completion-v1","contract_sha256":contract_hash,
            "result":input::identity(&store.path("result.json"))?,"journal":input::identity(&store.path("windows.jsonl"))?}))?;
    }
    if let Some(attempt) = &attempt {
        store.write(&attempt.join("finish.json"),&json!({"status":result["status"],"window_count":rows.len(),
        "failed_window_count":failures,"reused_windows":cached.iter().filter(|w|w.is_some()).count()}))?;
    }
    Ok(result)
}

/// File-relative windows after explicit origin/offset normalization. Integer
/// scaling is (component - scalar_zero) / iq_scale, then configured Q polarity.
pub fn read_metadata_window(
    meta: &formats::RawIqMetadata,
    start: usize,
    count: usize,
) -> Result<Vec<Complex64>, String> {
    if count > dsp::MAX_PCM_WINDOW_SAMPLES
        || !meta.scalar_zero.is_finite()
        || !meta.iq_scale.is_finite()
        || meta.iq_scale <= 0.0
        || ![-1, 1].contains(&meta.q_sign)
    {
        return Err("invalid bounded IQ layout/scale".into());
    }
    let scalar_bytes = meta.scalar_encoding.scalar_bytes() as usize;
    let width = 2 * scalar_bytes;
    let end = start.checked_add(count).ok_or("IQ range overflow")?;
    if end as u64 > meta.complex_sample_count {
        return Err("IQ window exceeds declared sample count".into());
    }
    let offset = meta
        .byte_offset
        .checked_add(
            (start as u64)
                .checked_mul(width as u64)
                .ok_or("IQ offset overflow")?,
        )
        .ok_or("IQ offset overflow")?;
    let size = count.checked_mul(width).ok_or("IQ buffer overflow")?;
    let file = input::open_regular(&meta.data_path)?;
    let length = file.metadata().map_err(|e| e.to_string())?.len();
    if length != meta.size_bytes || offset as u128 + size as u128 > length as u128 {
        return Err("IQ layout no longer matches file size".into());
    }
    let mut reader = BufReader::new(file);
    reader
        .seek(SeekFrom::Start(offset))
        .map_err(|e| e.to_string())?;
    let mut raw = vec![0u8; size];
    reader.read_exact(&mut raw).map_err(|e| e.to_string())?;
    raw.chunks_exact(width)
        .map(|chunk| {
            let a = (meta.scalar_encoding.decode_scalar(&chunk[..scalar_bytes])?
                - meta.scalar_zero)
                / meta.iq_scale;
            let b = (meta.scalar_encoding.decode_scalar(&chunk[scalar_bytes..])?
                - meta.scalar_zero)
                / meta.iq_scale;
            let (i, q) = if meta.interleaving == formats::Interleaving::Qi {
                (b, a)
            } else {
                (a, b)
            };
            let value = Complex64::new(i, q * meta.q_sign as f64);
            if !value.re.is_finite() || !value.im.is_finite() {
                Err("nonfinite normalized IQ sample".into())
            } else {
                Ok(value)
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn registry_rejects_duplicates_and_missing_integrity() {
        let mut r = GenericReceiver::with_builtin_demodulators();
        let p = Arc::new(ProtocolConfig::Ax25 {
            g3ruh_modes: vec![false, true],
        });
        r.register_protocol("ax25", p.clone()).unwrap();
        assert!(r.register_protocol("ax25", p).is_err());
        assert!(
            ProtocolConfig::Ax25 {
                g3ruh_modes: vec![]
            }
            .validate()
            .is_err()
        );
        let invalid = ProtocolConfig::CcsdsTm {
            config: protocol::CcsdsTmDecoderConfig {
                frame: protocol::TmTransferFrameConfig {
                    frame_length_bytes: 32,
                    fecf_present: false,
                },
                sync_marker: vec![1, 0, 1, 0],
                maximum_sync_hamming: 0,
                invert_modes: vec![false],
            },
        };
        assert!(invalid.validate().is_err());
    }
    #[test]
    fn iq_file_type_order_and_window_are_exact() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("test.iq");
        let mut file = File::create(&path).unwrap();
        for (i, q) in [(-32768i16, 32767i16), (17, -23)] {
            file.write_all(&i.to_le_bytes()).unwrap();
            file.write_all(&q.to_le_bytes()).unwrap();
        }
        let values = read_iq_window(&path, &InputFormat::Ci16Le, 1, 1).unwrap();
        assert_eq!(values, vec![Complex64::new(17.0, -23.0)]);
        assert!(read_iq_window(&path, &InputFormat::Ci16Le, 2, 1).is_err());
    }
}

#[cfg(test)]
#[path = "tests/generic_tests.rs"]
mod end_to_end_tests;

#[cfg(test)]
#[path = "tests/generic_resume_tests.rs"]
mod resume_tests;
