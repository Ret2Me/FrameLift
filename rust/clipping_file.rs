//! Content-bound file entry point for the clipping research receiver.
//!
//! v1 deliberately uses bounded in-memory statistics over streaming IQ instead
//! of the old SQLite spill store. Selection ordering is a qualified contract;
//! the storage backend and counters are explicitly a new version. A resumed
//! completed result is decoded again and compared, not accepted by file name.

use crate::{clipping, input, ledger, protocol};
use clipping::{BlindPhaseFskConfig, BlindPhaseFskResult, PhaseWindowCandidate};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

pub const API_VERSION: &str = "rust-blind-phase-fsk-file-api-v1";
pub const SELECTOR_VERSION: &str = "streaming-iq-bounded-memory-statistics-v1";
const MAX_RESULT_BYTES: u64 = 128 * 1024 * 1024;
const MAX_WORKING_SET_BYTES: u128 = 512 * 1024 * 1024;
type Result<T> = std::result::Result<T, String>;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlindPhaseFskRunConfig {
    pub receiver: BlindPhaseFskConfig,
    pub expected_input_size_bytes: u64,
    pub expected_input_sha256: String,
    #[serde(default = "default_scratch")]
    pub maximum_scratch_bytes: u64,
}
fn default_scratch() -> u64 {
    512 * 1024 * 1024
}
fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn fingerprint(value: &Value) -> Result<String> {
    Ok(digest(ledger::canonical_json(value)?.as_bytes()))
}

impl BlindPhaseFskRunConfig {
    pub fn validate(&self) -> Result<()> {
        if self.expected_input_size_bytes == 0 || !self.expected_input_size_bytes.is_multiple_of(4)
        {
            return Err("expected CI16 size must be a positive multiple of four".into());
        }
        if self.expected_input_sha256.len() != 64
            || !self
                .expected_input_sha256
                .bytes()
                .all(|b| b.is_ascii_hexdigit())
        {
            return Err("expected input hash must be SHA256".into());
        }
        if !(1024 * 1024..=1024 * 1024 * 1024).contains(&self.maximum_scratch_bytes) {
            return Err("scratch budget must be between 1 MiB and 1 GiB".into());
        }
        validate_receiver_bounds(&self.receiver)?;
        let analysis = (self.receiver.sample_rate_hz as f64 * self.receiver.analysis_window_seconds)
            .round_ties_even() as u64;
        let windows = self.expected_input_size_bytes / 4 / analysis;
        if !(2..=1_000_000).contains(&windows)
            || windows as u128 * 1024 > self.maximum_scratch_bytes as u128
        {
            return Err(
                "selector statistics exceed bounded analysis count or legacy scratch-row estimate"
                    .into(),
            );
        }
        Ok(())
    }
}

pub fn decoder_working_set_model_bytes(c: &BlindPhaseFskConfig) -> Result<u128> {
    c.validate()?;
    if c.constant_radius_factors.len() > 64
        || c.phase_difference_lags.len() > 64
        || c.deep_least_reliable_symbols > 512
        || c.maximum_map_seed_states > 4096
        || c.deep_maximum_flips > 16
        || c.repair_path_maximum_unique_frames > 8
    {
        return Err("working-set model inputs exceed its bounded domain".into());
    }
    let samples = (c.decoder_window_seconds * c.sample_rate_hz as f64).round_ties_even() as u128;
    let decimation = clipping::frontend_decimation_and_sps(c)?.0 as u128;
    let symbols = samples.div_ceil(decimation);
    let streams = c.constant_radius_factors.len() as u128
        * c.phase_difference_lags.len() as u128
        * c.short_search_timing_hypotheses
            .max(c.deep_search_timing_hypotheses) as u128;
    let error_units =
        c.deep_least_reliable_symbols as u128 * (1 + 2 * c.candidate_neighbor_radius) as u128;
    let seeds = c.maximum_map_seed_states as u128 + c.repair_path_maximum_unique_frames as u128;
    Ok(192 * 1024 * 1024
        + samples * 128
        + symbols * (64 + 40 * streams)
        + c.maximum_receiver_paths_per_window as u128 * 512
        + error_units * 256
        + seeds * error_units * 64
        + seeds * (c.deep_maximum_flips as u128 + 1) * 384
        + c.deep_maximum_attempts.min(c.repair_path_maximum_attempts) as u128 * 512)
}

fn validate_receiver_bounds(c: &BlindPhaseFskConfig) -> Result<()> {
    c.validate()?;
    if !c.error_unit_penalty.is_finite()
        || c.constant_radius_factors.iter().any(|x| *x > 100.0)
        || c.rate_errors_ppm.iter().any(|x| x.abs() > 500_000.0)
        || [
            c.constant_radius_factors.len(),
            c.phase_difference_lags.len(),
            c.rate_errors_ppm.len(),
        ]
        .iter()
        .any(|n| *n > 64)
    {
        return Err("receiver hypothesis values exceed the file API envelope".into());
    }
    if c.short_least_reliable_symbols != 64
        || c.short_maximum_flips != 2
        || c.short_maximum_attempts != 20_000
    {
        return Err("reserved short-search fields must retain 64/2/20000".into());
    }
    let limits = [
        (c.candidate_window_limit, 4096),
        (c.maximum_regions_per_start, 64),
        (c.deep_least_reliable_symbols, 512),
        (c.deep_maximum_flips, 16),
        (c.deep_maximum_attempts, 2_000_000),
        (c.maximum_map_seed_states, 4096),
        (c.maximum_receiver_paths_per_window, 20_000),
        (c.maximum_frame_detections, 20_000),
        (c.repair_path_maximum_attempts, 100_000),
        (c.repair_path_maximum_unique_frames, 8),
        (c.repair_region_maximum_attempts, 50_000),
        (c.repair_event_maximum_attempts, 400_000),
        (c.repair_event_maximum_unique_frames, 8),
        (c.repair_window_maximum_events, 16),
        (c.repair_window_maximum_attempts, 1_600_000),
        (c.repair_window_maximum_unique_frames, 16),
        (c.event_cluster_tolerance_symbols, 16),
    ];
    if limits.iter().any(|(value, limit)| value > limit) {
        return Err("receiver search/scheduler limit exceeds the production file envelope".into());
    }
    let mut rates = c.rate_errors_ppm.clone();
    rates.sort_by(f64::total_cmp);
    rates.dedup();
    let bank = rates.len() as u128 * c.phase_bins as u128;
    let total = c.candidate_window_limit as u128;
    if bank > 65_536
        || total
            * c.constant_radius_factors.len() as u128
            * c.phase_difference_lags.len() as u128
            * c.descramble_modes.len() as u128
            * bank
            > 1_000_000
        || total * c.repair_window_maximum_attempts as u128 > 51_200_000
        || total * c.repair_window_maximum_unique_frames as u128 > 512
    {
        return Err(
            "aggregate timing/repair/output work exceeds the production file envelope".into(),
        );
    }
    let analysis = (c.analysis_window_seconds * c.sample_rate_hz as f64).round_ties_even() as usize;
    let decoder = (c.decoder_window_seconds * c.sample_rate_hz as f64).round_ties_even() as usize;
    if analysis > 1_000_000
        || analysis > decoder
        || decoder < 116
        || c.phase_difference_lags
            .iter()
            .any(|lag| *lag >= decoder - 115)
    {
        return Err("analysis/decoder/lag geometry exceeds the production file envelope".into());
    }
    let (decimation, sps) = clipping::frontend_decimation_and_sps(c)?;
    let maximum_step = sps * (1.0 + rates.last().unwrap() * 1e-6);
    let numerator = decoder - c.phase_difference_lags.iter().max().unwrap() - 114;
    let filtered = numerator.div_ceil(decimation);
    if filtered <= 1024
        || filtered - 1023 < 256
        || ((filtered - 1023) as f64 - maximum_step) / maximum_step < 192.0
    {
        return Err("window too short after discriminator/timing guard".into());
    }
    if decoder_working_set_model_bytes(c)? > MAX_WORKING_SET_BYTES {
        return Err("conservative legacy working-set model exceeds 512 MiB".into());
    }
    Ok(())
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileStamp {
    size: u64,
    device: u64,
    inode: u64,
    mtime: i64,
    mtime_ns: i64,
    ctime: i64,
    ctime_ns: i64,
}
fn stamp(metadata: &fs::Metadata) -> FileStamp {
    use std::os::unix::fs::MetadataExt;
    FileStamp {
        size: metadata.len(),
        device: metadata.dev(),
        inode: metadata.ino(),
        mtime: metadata.mtime(),
        mtime_ns: metadata.mtime_nsec(),
        ctime: metadata.ctime(),
        ctime_ns: metadata.ctime_nsec(),
    }
}
fn regular_stamp(path: &Path) -> Result<FileStamp> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("expected a regular non-symlink file".into());
    }
    Ok(stamp(&metadata))
}
fn require_stamp(path: &Path, expected: &FileStamp) -> Result<()> {
    if &regular_stamp(path)? != expected {
        return Err("source/lock file identity changed".into());
    }
    Ok(())
}

struct AttemptLock {
    file: File,
    path: PathBuf,
}
impl AttemptLock {
    fn acquire(directory: &Path) -> Result<Self> {
        use std::os::fd::AsRawFd;
        use std::os::unix::fs::OpenOptionsExt;
        let path = directory.join("attempt.lock");
        let file = match OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                regular_stamp(&path)?;
                // A lock must never canonicalize through a substituted final
                // symlink into an unrelated inode, even temporarily.
                OpenOptions::new()
                    .read(true)
                    .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
                    .open(&path)
                    .map_err(|e| e.to_string())?
            }
            Err(error) => return Err(error.to_string()),
        };
        if !file.metadata().map_err(|e| e.to_string())?.is_file() {
            return Err("attempt lock is not a regular file".into());
        }
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(
                "clipping attempt is locked by another live owner; no PID was guessed or killed"
                    .into(),
            );
        }
        let lock = Self { file, path };
        lock.verify()?;
        Ok(lock)
    }
    fn verify(&self) -> Result<()> {
        let held = stamp(&self.file.metadata().map_err(|e| e.to_string())?);
        let named = regular_stamp(&self.path)?;
        if held.device != named.device || held.inode != named.inode {
            return Err("owned attempt lock was replaced".into());
        }
        Ok(())
    }
}
// File descriptor close releases flock. The persistent lock inode is never
// removed, avoiding an unlock/unlink/new-lock race with another invocation.

fn output_directory(path: &Path) -> Result<PathBuf> {
    if !path.exists() {
        fs::create_dir_all(path).map_err(|e| e.to_string())?;
    }
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err("output must be a real directory".into());
    }
    path.canonicalize().map_err(|e| e.to_string())
}
fn check_disk_room(directory: &Path) -> Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let directory =
        std::ffi::CString::new(directory.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    let mut stat = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(directory.as_ptr(), stat.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let stat = unsafe { stat.assume_init() };
    if stat.f_bavail as u128 * (stat.f_frsize as u128) < 2 * MAX_RESULT_BYTES as u128 + 1024 * 1024
    {
        return Err("insufficient disk headroom for bounded no-clobber result publication".into());
    }
    Ok(())
}

fn all_zero_prefix(path: &Path, bytes: u64) -> Result<bool> {
    let mut file = input::open_regular(path)?.take(bytes);
    let mut buffer = [0u8; 65536];
    let mut consumed = 0;
    loop {
        let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        consumed += count as u64;
        if buffer[..count].iter().any(|b| *b != 0) {
            return Ok(false);
        }
    }
    if consumed != bytes {
        return Err("CI16 input became incomplete during zero scan".into());
    }
    Ok(true)
}

/// Same selected-window ordering contract as the research selector; memory is
/// explicitly bounded by the number of scalar-statistics rows, not constant.
pub fn select_windows_bounded(
    source: &Path,
    config: &BlindPhaseFskRunConfig,
) -> Result<(Vec<PhaseWindowCandidate>, Value)> {
    config.validate()?;
    let before = regular_stamp(source)?;
    if before.size != config.expected_input_size_bytes {
        return Err("CI16 size changed before selection".into());
    }
    let analysis = (config.receiver.sample_rate_hz as f64 * config.receiver.analysis_window_seconds)
        .round_ties_even() as u64;
    let windows = before.size / (analysis * 4);
    let degenerate = all_zero_prefix(source, windows * analysis * 4)?;
    let selected = if degenerate {
        vec![]
    } else {
        clipping::select_ci16_phase_windows(source, &config.receiver)?
    };
    require_stamp(source, &before)?;
    Ok((
        selected,
        json!({"selector_version":SELECTOR_VERSION,"degenerate_input":degenerate,
        "degenerate_reason":if degenerate {Some("all_zero_ci16")} else {None},
        "analysis_windows_scanned":windows,"analysis_window_bytes":analysis*4,
        "maximum_analysis_array_bytes":analysis*64,"statistics_memory_bound_bytes":windows*80,
        "legacy_scratch_row_estimate_bytes":windows*1024,"actual_scratch_bytes":0,
        "unanalysed_tail_bytes":before.size-windows*analysis*4,"sqlite_storage_equivalence_claimed":false}),
    ))
}

fn validate_selected(
    windows: &[PhaseWindowCandidate],
    config: &BlindPhaseFskRunConfig,
) -> Result<()> {
    if windows.is_empty() || windows.len() > config.receiver.candidate_window_limit {
        return Err("caller-selected windows must be nonempty and bounded".into());
    }
    let samples = config.expected_input_size_bytes / 4;
    let width = (config.receiver.decoder_window_seconds * config.receiver.sample_rate_hz as f64)
        .round_ties_even();
    for w in windows {
        if [
            w.analysis_start_seconds,
            w.decoder_start_seconds,
            w.mean_power,
            w.endpoint_clip_fraction,
            w.lag1_phase_coherence,
            w.score,
        ]
        .iter()
        .any(|x| !x.is_finite())
            || w.analysis_start_seconds < 0.0
            || w.decoder_start_seconds < 0.0
            || w.mean_power < 0.0
            || !(0.0..=1.0).contains(&w.endpoint_clip_fraction)
            || !(0.0..=1.0 + 1e-12).contains(&w.lag1_phase_coherence)
            || (w.decoder_start_seconds * config.receiver.sample_rate_hz as f64).round_ties_even()
                + width
                > samples as f64
        {
            return Err("invalid caller-selected window provenance or bounds".into());
        }
    }
    Ok(())
}

fn empty_decode(source: &input::Identity, config: &BlindPhaseFskConfig) -> BlindPhaseFskResult {
    BlindPhaseFskResult {
        input_path: source.path.clone(),
        input_sha256: source.sha256.clone(),
        input_complex_samples: source.bytes / 4,
        input_duration_seconds: (source.bytes / 4) as f64 / config.sample_rate_hz as f64,
        selected_windows: vec![],
        decoded_windows: 0,
        timing_hypotheses_examined: 0,
        protocol_candidates_attempted: 0,
        native_protocol_candidates_attempted: 0,
        repair_protocol_candidates_attempted: 0,
        frames: vec![],
        frontend_input_representation: "constant_radius_endpoint_reconstruction_not_recorded_IQ"
            .into(),
        repaired_frames_require_external_verification: true,
    }
}
fn independently_valid(frame: &clipping::ClippingRobustAx25Frame) -> bool {
    let mut register = 0xffff_u16;
    for byte in &frame.payload {
        for bit in 0..8 {
            let feedback = (register ^ u16::from((byte >> bit) & 1)) & 1;
            register >>= 1;
            if feedback != 0 {
                register ^= 0x8408;
            }
        }
    }
    frame.fcs == (register ^ 0xffff).to_le_bytes()
        && protocol::parse_ax25_ui(&frame.payload).is_some()
}
fn result_document(plan: &Value, decoded: BlindPhaseFskResult, selector: Value) -> Result<Value> {
    let mut frames = Vec::new();
    let mut native = BTreeSet::new();
    let mut repaired = BTreeSet::new();
    for frame in &decoded.frames {
        if !independently_valid(frame) {
            return Err(
                "frame failed independent bitwise FCS/AX25 validation at publication boundary"
                    .into(),
            );
        }
        let bytes: Vec<_> = frame.payload.iter().chain(&frame.fcs).copied().collect();
        let frame_hash = digest(&bytes);
        if frame.flipped_symbol_indices.is_empty() {
            native.insert(frame_hash.clone());
        } else {
            repaired.insert(frame_hash.clone());
        }
        let mut document = serde_json::to_value(frame).map_err(|e| e.to_string())?;
        let object = document.as_object_mut().unwrap();
        object.remove("payload");
        object.remove("fcs");
        object.insert("payload_hex".into(), json!(hex::encode(&frame.payload)));
        object.insert("fcs_hex".into(), json!(hex::encode(&frame.fcs)));
        object.insert("frame_with_fcs_hex".into(), json!(hex::encode(&bytes)));
        object.insert("frame_with_fcs_sha256".into(), json!(frame_hash));
        object.insert(
            "native_crc_accepted".into(),
            json!(frame.flipped_symbol_indices.is_empty()),
        );
        object.insert("repaired_candidate_authenticated".into(), json!(false));
        frames.push(document);
    }
    let mut document = json!({"schema":"rust-blind-phase-fsk-file-result-v1","api_version":API_VERSION,"status":"complete",
        "attempt_fingerprint":plan["attempt_fingerprint"],"identity":plan["identity"],"selector":selector,
        "selected_windows":decoded.selected_windows,"decoded_windows":decoded.decoded_windows,
        "timing_hypotheses_examined":decoded.timing_hypotheses_examined,"protocol_candidates_attempted":decoded.protocol_candidates_attempted,
        "native_protocol_candidates_attempted":decoded.native_protocol_candidates_attempted,"repair_protocol_candidates_attempted":decoded.repair_protocol_candidates_attempted,
        "input_complex_samples":decoded.input_complex_samples,"input_duration_seconds":decoded.input_duration_seconds,
        "frame_detections":frames.len(),"native_detection_count":decoded.frames.iter().filter(|f| f.flipped_symbol_indices.is_empty()).count(),
        "repaired_candidate_detection_count":decoded.frames.iter().filter(|f| !f.flipped_symbol_indices.is_empty()).count(),
        "unique_native_full_frame_count":native.len(),"unique_repaired_candidate_full_frame_count":repaired.len(),"frames":frames,
        "frontend_input_representation":decoded.frontend_input_representation,"repaired_frames_require_external_verification":true,
        "independent_bitwise_fcs_and_ax25_validation":true,"caller_expected_input_identity_is_external_authentication":false,
        "python_runtime_required":false,"publication_ready":false,"deployment_ready":false});
    document["result_payload_sha256"] = json!(fingerprint(&document)?);
    Ok(document)
}
fn bounded_json(path: &Path) -> Result<Value> {
    let metadata = regular_stamp(path)?;
    if metadata.size == 0 || metadata.size > MAX_RESULT_BYTES {
        return Err("result/plan is empty or exceeds 128 MiB".into());
    }
    // The shared small-input helper intentionally caps at 64 MiB; the legacy
    // file-result contract permits 128 MiB. Keep this separate bounded reader.
    let mut bytes = Vec::with_capacity(metadata.size as usize);
    input::open_regular(path)?
        .take(MAX_RESULT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 != metadata.size || bytes.len() as u64 > MAX_RESULT_BYTES {
        return Err("result/plan size changed during bounded read".into());
    }
    require_stamp(path, &metadata)?;
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}
fn validate_existing(document: &Value, plan: &Value) -> Result<()> {
    let mut unhashed = document.clone();
    let hash = unhashed
        .as_object_mut()
        .ok_or("result must be an object")?
        .remove("result_payload_sha256")
        .ok_or("result hash absent")?;
    if hash.as_str() != Some(fingerprint(&unhashed)?.as_str())
        || document["status"] != "complete"
        || document["schema"] != "rust-blind-phase-fsk-file-result-v1"
        || document["api_version"] != API_VERSION
        || document["identity"] != plan["identity"]
        || document["attempt_fingerprint"] != plan["attempt_fingerprint"]
    {
        return Err("existing result is corrupt, incomplete or belongs to another attempt".into());
    }
    Ok(())
}

fn staging_prefix(plan: &Value) -> String {
    format!(
        "clipping-owned-staging-{}-",
        plan["attempt_fingerprint"].as_str().unwrap()
    )
}

fn publish_new(path: &Path, document: &Value, plan: &Value) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(document).map_err(|e| e.to_string())?;
    if bytes.len() as u64 + 1 > MAX_RESULT_BYTES {
        return Err("pretty-serialized artifact exceeds 128 MiB output bound".into());
    }
    let parent = path.parent().ok_or("artifact parent absent")?;
    let mut temporary = tempfile::Builder::new()
        .prefix(&staging_prefix(plan))
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary.write_all(&bytes).map_err(|e| e.to_string())?;
    temporary.write_all(b"\n").map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary
        .persist_noclobber(path)
        .map_err(|e| e.to_string())?;
    File::open(parent)
        .and_then(|file| file.sync_all())
        .map_err(|e| e.to_string())
}

/// Owns only its directory lock and newly created artifacts. `resume` must be
/// explicit; caller-supplied windows are identity-bound and never labelled blind.
pub fn run_blind_phase_fsk_file(
    source: &Path,
    output: &Path,
    config: &BlindPhaseFskRunConfig,
    selected_windows: Option<&[PhaseWindowCandidate]>,
    resume: bool,
) -> Result<Value> {
    config.validate()?;
    if let Some(windows) = selected_windows {
        validate_selected(windows, config)?;
    }
    let original = regular_stamp(source)?;
    let source_identity = input::identity(source)?;
    require_stamp(source, &original)?;
    if source_identity.bytes != config.expected_input_size_bytes
        || source_identity.sha256 != config.expected_input_sha256.to_ascii_lowercase()
    {
        return Err("CI16 input does not match caller-bound expected identity".into());
    }
    let executable_path = std::env::current_exe().map_err(|e| e.to_string())?;
    let executable = input::identity(&executable_path)?;
    let mut config_value = serde_json::to_value(config).map_err(|e| e.to_string())?;
    config_value["expected_input_sha256"] =
        json!(config.expected_input_sha256.to_ascii_lowercase());
    let identity = json!({"api_version":API_VERSION,"source":source_identity,"config":config_value,
        "config_sha256":fingerprint(&config_value)?,"selector_version":SELECTOR_VERSION,
        "caller_selected_windows":selected_windows,"selection_mode":if selected_windows.is_some() {"caller_selected_not_blind"} else {"blind_statistics"},
        "executable":{"sha256":executable.sha256,"bytes":executable.bytes},"compute_identity":crate::compute::current().identity(),"crate_version":env!("CARGO_PKG_VERSION")});
    let plan = json!({"schema":"rust-blind-phase-fsk-file-plan-v1","attempt_fingerprint":fingerprint(&identity)?,"identity":identity});
    let directory = output_directory(output)?;
    let lock = AttemptLock::acquire(&directory)?;
    check_disk_room(&directory)?;
    let mut orphaned_staging = 0;
    let mut orphaned_bytes = 0_u64;
    for entry in fs::read_dir(&directory).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry
            .file_name()
            .to_str()
            .is_some_and(|name| name.starts_with(&staging_prefix(&plan)))
        {
            orphaned_staging += 1;
            let size = regular_stamp(&entry.path())?.size;
            if size > MAX_RESULT_BYTES {
                return Err("owned staging artifact exceeds 128 MiB".into());
            }
            orphaned_bytes = orphaned_bytes
                .checked_add(size)
                .ok_or("staging byte count overflow")?;
            if orphaned_staging > 8 || orphaned_bytes > 2 * MAX_RESULT_BYTES {
                return Err("preserved owned staging exceeds bounded recovery allowance".into());
            }
            continue;
        }
        if !matches!(
            entry.file_name().to_str(),
            Some("attempt.lock" | "plan.json" | "result.json")
        ) {
            return Err("unexpected artifact in clipping attempt directory".into());
        }
    }
    let plan_path = directory.join("plan.json");
    let result_path = directory.join("result.json");
    let existing_plan = fs::symlink_metadata(&plan_path).is_ok();
    let existing_result = fs::symlink_metadata(&result_path).is_ok();
    if existing_plan {
        if !resume {
            return Err("attempt already exists; explicit resume required".into());
        }
        if bounded_json(&plan_path)? != plan {
            return Err("existing attempt plan identity differs".into());
        }
    } else {
        if (resume && orphaned_staging == 0) || existing_result {
            return Err("resume requires an existing matching plan".into());
        }
        if orphaned_staging > 0 && !resume {
            return Err("owned interrupted staging exists; explicit resume required".into());
        }
        lock.verify()?;
        publish_new(&plan_path, &plan, &plan)?;
    }
    let previous = if existing_result {
        let result = bounded_json(&result_path)?;
        validate_existing(&result, &plan)?;
        Some(result)
    } else {
        None
    };
    let (windows, mut selector) = if let Some(windows) = selected_windows {
        let degenerate = all_zero_prefix(source, original.size)?;
        (
            if degenerate { vec![] } else { windows.to_vec() },
            json!({"selector_version":"caller-selected-v1","degenerate_input":degenerate,
            "degenerate_reason":if degenerate {Some("all_zero_ci16")} else {None},"caller_selected_windows_bound_in_identity":true,
            "caller_metadata_was_measured_by_this_invocation":false,"actual_scratch_bytes":0}),
        )
    } else {
        select_windows_bounded(source, config)?
    };
    selector["conservative_decoder_working_set_model_bytes"] =
        json!(decoder_working_set_model_bytes(&config.receiver)? as u64);
    require_stamp(source, &original)?;
    let decoded = if selector["degenerate_input"] == true {
        empty_decode(&source_identity, &config.receiver)
    } else {
        clipping::decode_clipping_robust_ax25_ci16(source, &config.receiver, Some(&windows))?
    };
    require_stamp(source, &original)?;
    let after = input::identity(source)?;
    if after.sha256 != source_identity.sha256 || after.bytes != source_identity.bytes {
        return Err("CI16 changed during file processing".into());
    }
    require_stamp(source, &original)?;
    let executable_after = input::identity(&executable_path)?;
    if executable_after.sha256 != executable.sha256 || executable_after.bytes != executable.bytes {
        return Err("receiver executable changed during processing".into());
    }
    lock.verify()?;
    if bounded_json(&plan_path)? != plan {
        return Err("plan changed during processing".into());
    }
    let result = result_document(&plan, decoded, selector)?;
    if serde_json::to_vec(&result)
        .map_err(|e| e.to_string())?
        .len() as u64
        > MAX_RESULT_BYTES
    {
        return Err("result exceeds 128 MiB output bound".into());
    }
    if let Some(previous) = previous {
        if previous != result || bounded_json(&result_path)? != previous {
            return Err("completed result failed deterministic revalidation".into());
        }
        Ok(previous)
    } else {
        check_disk_room(&directory)?;
        lock.verify()?;
        publish_new(&result_path, &result, &plan)?;
        Ok(result)
    }
}

#[cfg(test)]
#[path = "tests/clipping_file_tests.rs"]
mod tests;
