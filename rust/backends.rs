//! Shell-free, bounded adapters for external comparison decoders. External
//! PDUs are byte candidates only; no integrity or transmitter trust is implied.
use crate::{formats, input};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const MAX_CAPTURED_BYTES: usize = 64 * 1024 * 1024;

fn nonempty(value: &str, name: &str) -> Result<(), String> {
    if value.trim().is_empty() || value.contains('\0') {
        return Err(format!("{name} must be nonempty and NUL-free"));
    }
    Ok(())
}
fn strings(values: &[String], name: &str) -> Result<(), String> {
    let mut unique = BTreeSet::new();
    for value in values {
        nonempty(value, name)?;
        if !unique.insert(value) {
            return Err(format!("{name} must be unique"));
        }
    }
    Ok(())
}
fn json_keys(values: &Map<String, Value>, name: &str) -> Result<(), String> {
    for key in values.keys() {
        nonempty(key, name)?;
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureSegment {
    pub path: PathBuf,
    pub sample_format: String,
    pub sample_rate_hz: f64,
    #[serde(default)]
    pub start_sample: u64,
    pub sample_count: Option<u64>,
    #[serde(default)]
    pub hints: Map<String, Value>,
}
impl CaptureSegment {
    pub fn validate(&self) -> Result<(), String> {
        if self.path.as_os_str().is_empty() {
            return Err("capture path must be nonempty".into());
        }
        nonempty(&self.sample_format, "sample_format")?;
        if !self.sample_rate_hz.is_finite() || self.sample_rate_hz <= 0.0 {
            return Err("sample_rate_hz must be finite and positive".into());
        }
        if self.sample_count == Some(0) {
            return Err("sample_count must be positive".into());
        }
        if self
            .sample_count
            .is_some_and(|n| self.start_sample.checked_add(n).is_none())
        {
            return Err("capture sample range overflows".into());
        }
        json_keys(&self.hints, "capture hints")
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExternalBackendCapabilities {
    pub modulations: Vec<String>,
    pub framing: Vec<String>,
    pub fec: Vec<String>,
    #[serde(default)]
    pub sample_formats: Vec<String>,
}
impl ExternalBackendCapabilities {
    pub fn validate(&self) -> Result<(), String> {
        strings(&self.modulations, "modulations")?;
        strings(&self.framing, "framing")?;
        strings(&self.fec, "fec")?;
        strings(&self.sample_formats, "sample_formats")
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BackendProcessOutput {
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub returncode: i32,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ParsedByteCandidate {
    pub payload: Vec<u8>,
    #[serde(default)]
    pub provenance: Map<String, Value>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CandidateProvenance {
    pub backend_id: String,
    pub backend_version: String,
    pub capture_path: String,
    pub sample_format: String,
    pub sample_rate_hz: f64,
    pub start_sample: u64,
    pub sample_count: Option<u64>,
    pub capture_hints: Map<String, Value>,
    pub argv: Vec<String>,
    pub parser: Map<String, Value>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExternalByteCandidate {
    pub payload: Vec<u8>,
    pub provenance: CandidateProvenance,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureCode {
    InputError,
    InvocationError,
    LaunchError,
    Timeout,
    OutputLimit,
    NonzeroExit,
    ParseError,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExternalBackendFailure {
    pub code: FailureCode,
    pub message: String,
    pub returncode: Option<i32>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BackendStatus {
    Success,
    Failure,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExternalBackendResult {
    pub backend_id: String,
    pub backend_version: String,
    pub capabilities: ExternalBackendCapabilities,
    pub status: BackendStatus,
    pub candidates: Vec<ExternalByteCandidate>,
    pub argv: Vec<String>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub returncode: Option<i32>,
    pub failure: Option<ExternalBackendFailure>,
}
impl ExternalBackendResult {
    pub fn succeeded(&self) -> bool {
        self.status == BackendStatus::Success
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExternalBackendConfig {
    pub backend_id: String,
    pub backend_version: String,
    pub capabilities: ExternalBackendCapabilities,
    pub timeout_seconds: f64,
    pub max_output_bytes: usize,
    /// Per-file bound for child-created files, in addition to combined pipe
    /// output bounds. This is resource containment, not a security sandbox.
    pub max_file_bytes: u64,
    #[serde(default)]
    pub environment_overrides: BTreeMap<String, String>,
}
impl ExternalBackendConfig {
    pub fn validate(&self) -> Result<(), String> {
        nonempty(&self.backend_id, "backend_id")?;
        nonempty(&self.backend_version, "backend_version")?;
        self.capabilities.validate()?;
        if !self.timeout_seconds.is_finite()
            || self.timeout_seconds <= 0.0
            || self.timeout_seconds > 86400.0
        {
            return Err("timeout_seconds must be in (0, 86400]".into());
        }
        if self.max_output_bytes == 0 || self.max_output_bytes > MAX_CAPTURED_BYTES {
            return Err("combined stdout/stderr bound must be 1..64 MiB".into());
        }
        if self.max_file_bytes == 0 || self.max_file_bytes > 1024 * 1024 * 1024 {
            return Err("per-file child output bound must be 1 byte..1 GiB".into());
        }
        for (key, value) in &self.environment_overrides {
            if key.is_empty() || key.contains(['\0', '=']) || value.contains('\0') {
                return Err("invalid environment override".into());
            }
        }
        Ok(())
    }
    fn failure(
        &self,
        code: FailureCode,
        message: String,
        argv: Vec<String>,
        process: ProcessResult,
    ) -> ExternalBackendResult {
        ExternalBackendResult {
            backend_id: self.backend_id.clone(),
            backend_version: self.backend_version.clone(),
            capabilities: self.capabilities.clone(),
            status: BackendStatus::Failure,
            candidates: vec![],
            argv,
            stdout: process.stdout,
            stderr: process.stderr,
            returncode: process.returncode,
            failure: Some(ExternalBackendFailure {
                code,
                message,
                returncode: process.returncode,
            }),
        }
    }
}
#[derive(Default)]
struct ProcessResult {
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    returncode: Option<i32>,
    failure: Option<(FailureCode, String)>,
}

/// Python json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True)
/// ordering key. Original escaped Unicode ordering matters for deduplication.
fn candidate_key(value: &Map<String, Value>) -> Result<String, String> {
    let encoded = crate::ledger::canonical_json(&Value::Object(value.clone()))?;
    let mut ascii = String::new();
    for ch in encoded.chars() {
        if ch.is_ascii() {
            ascii.push(ch);
        } else {
            let mut units = [0; 2];
            for unit in ch.encode_utf16(&mut units) {
                ascii.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    Ok(ascii)
}
fn attach_candidates(
    config: &ExternalBackendConfig,
    segment: &CaptureSegment,
    argv: &[String],
    parsed: Vec<ParsedByteCandidate>,
) -> Result<Vec<ExternalByteCandidate>, String> {
    let mut unique = BTreeMap::new();
    let mut candidate_bytes = 0usize;
    for candidate in parsed {
        if candidate.payload.is_empty() {
            return Err("parsed candidate payload must be nonempty".into());
        }
        json_keys(&candidate.provenance, "candidate provenance")?;
        let metadata = candidate_key(&candidate.provenance)?;
        candidate_bytes = candidate_bytes
            .checked_add(candidate.payload.len())
            .and_then(|n| n.checked_add(metadata.len()))
            .ok_or("candidate output size overflow")?;
        if candidate_bytes > MAX_CAPTURED_BYTES {
            return Err("parsed candidates exceed 64 MiB".into());
        }
        unique
            .entry((candidate.payload.clone(), metadata))
            .or_insert(candidate);
    }
    Ok(unique
        .into_iter()
        .map(|((payload, _), candidate)| ExternalByteCandidate {
            payload,
            provenance: CandidateProvenance {
                backend_id: config.backend_id.clone(),
                backend_version: config.backend_version.clone(),
                capture_path: segment.path.display().to_string(),
                sample_format: segment.sample_format.clone(),
                sample_rate_hz: segment.sample_rate_hz,
                start_sample: segment.start_sample,
                sample_count: segment.sample_count,
                capture_hints: segment.hints.clone(),
                argv: argv.to_vec(),
                parser: candidate.provenance,
            },
        })
        .collect())
}

/// Expected operational failures are returned as structured data. The builder
/// and parser are native Rust closures; no shell or Python wrapper is inserted.
pub fn run_external_backend<B, P>(
    config: &ExternalBackendConfig,
    segment: &CaptureSegment,
    builder: B,
    parser: P,
) -> ExternalBackendResult
where
    B: FnOnce(&CaptureSegment) -> Result<Vec<String>, String>,
    P: FnOnce(&BackendProcessOutput, &CaptureSegment) -> Result<Vec<ParsedByteCandidate>, String>,
{
    if let Err(e) = config.validate() {
        return config.failure(
            FailureCode::InvocationError,
            e,
            vec![],
            ProcessResult::default(),
        );
    }
    if let Err(e) = segment
        .validate()
        .and_then(|_| input::open_regular(&segment.path).map(|_| ()))
    {
        return config.failure(FailureCode::InputError, e, vec![], ProcessResult::default());
    }
    let argv = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| builder(segment))) {
        Ok(Ok(values))
            if !values.is_empty() && values.iter().all(|v| !v.is_empty() && !v.contains('\0')) =>
        {
            values
        }
        Ok(Err(e)) => {
            return config.failure(
                FailureCode::InvocationError,
                e,
                vec![],
                ProcessResult::default(),
            );
        }
        _ => {
            return config.failure(
                FailureCode::InvocationError,
                "argv builder failed or produced invalid argv".into(),
                vec![],
                ProcessResult::default(),
            );
        }
    };
    let process = bounded_process(&argv, config);
    if let Some((code, message)) = &process.failure {
        return config.failure(*code, message.clone(), argv, process);
    }
    let output = BackendProcessOutput {
        stdout: process.stdout,
        stderr: process.stderr,
        returncode: process.returncode.unwrap_or(0),
    };
    let parsed =
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| parser(&output, segment)));
    let candidates = match parsed {
        Ok(Ok(parsed)) => attach_candidates(config, segment, &argv, parsed),
        Ok(Err(e)) => Err(e),
        Err(_) => Err("output parser panicked".into()),
    };
    match candidates {
        Ok(candidates) => ExternalBackendResult {
            backend_id: config.backend_id.clone(),
            backend_version: config.backend_version.clone(),
            capabilities: config.capabilities.clone(),
            status: BackendStatus::Success,
            candidates,
            argv,
            stdout: output.stdout,
            stderr: output.stderr,
            returncode: Some(output.returncode),
            failure: None,
        },
        Err(message) => config.failure(
            FailureCode::ParseError,
            message,
            argv,
            ProcessResult {
                stdout: output.stdout,
                stderr: output.stderr,
                returncode: Some(output.returncode),
                failure: None,
            },
        ),
    }
}

#[cfg(target_os = "linux")]
fn bounded_process(argv: &[String], config: &ExternalBackendConfig) -> ProcessResult {
    use std::os::fd::AsRawFd;
    use std::os::unix::process::{CommandExt, ExitStatusExt};
    let mut result = ProcessResult::default();
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .envs(&config.environment_overrides)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let file_bound = config.max_file_bytes;
    // Only async-signal-safe calls occur between fork and exec.
    unsafe {
        command.pre_exec(move || {
            if libc::setpgid(0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            let limit = libc::rlimit {
                rlim_cur: file_bound,
                rlim_max: file_bound,
            };
            if libc::setrlimit(libc::RLIMIT_FSIZE, &limit) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(e) => {
            result.failure = Some((FailureCode::LaunchError, e.to_string()));
            return result;
        }
    };
    let stdout = child.stdout.take().expect("piped stdout");
    let stderr = child.stderr.take().expect("piped stderr");
    let mut group = match input::OwnedProcessGroup::new(child) {
        Ok(group) => group,
        Err(e) => {
            result.failure = Some((FailureCode::LaunchError, e));
            return result;
        }
    };
    let mut streams = [
        (stdout.as_raw_fd(), false, true),
        (stderr.as_raw_fd(), true, true),
    ];
    streams.sort_by_key(|s| s.0);
    for (fd, _, _) in &streams {
        let flags = unsafe { libc::fcntl(*fd, libc::F_GETFL) };
        if flags < 0 || unsafe { libc::fcntl(*fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
            result.failure = Some((
                FailureCode::LaunchError,
                std::io::Error::last_os_error().to_string(),
            ));
            break;
        }
    }
    let started = Instant::now();
    let timeout = Duration::from_secs_f64(config.timeout_seconds);
    let mut buffer = [0u8; 65536];
    while result.failure.is_none() && streams.iter().any(|s| s.2) {
        if started.elapsed() >= timeout {
            result.failure = Some((FailureCode::Timeout, "backend exceeded its timeout".into()));
            break;
        }
        let mut polling: Vec<libc::pollfd> = streams
            .iter()
            .filter(|s| s.2)
            .map(|s| libc::pollfd {
                fd: s.0,
                events: libc::POLLIN | libc::POLLHUP,
                revents: 0,
            })
            .collect();
        let remaining = timeout.saturating_sub(started.elapsed());
        let wait_ms = remaining.as_millis().clamp(1, 100) as i32;
        let ready =
            unsafe { libc::poll(polling.as_mut_ptr(), polling.len() as libc::nfds_t, wait_ms) };
        if ready < 0 {
            let e = std::io::Error::last_os_error();
            if e.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            result.failure = Some((FailureCode::LaunchError, e.to_string()));
            break;
        }
        for event in polling.iter().filter(|event| event.revents != 0) {
            let stream = streams.iter_mut().find(|s| s.0 == event.fd).unwrap();
            let available = config.max_output_bytes - result.stdout.len() - result.stderr.len();
            let request = buffer.len().min(available + 1);
            let count = unsafe { libc::read(event.fd, buffer.as_mut_ptr().cast(), request) };
            if count < 0 {
                let e = std::io::Error::last_os_error();
                if matches!(e.raw_os_error(), Some(libc::EAGAIN | libc::EINTR)) {
                    continue;
                }
                result.failure = Some((FailureCode::LaunchError, e.to_string()));
                break;
            }
            if count == 0 {
                stream.2 = false;
                continue;
            }
            let count = count as usize;
            let destination = if stream.1 {
                &mut result.stderr
            } else {
                &mut result.stdout
            };
            destination.extend_from_slice(&buffer[..count.min(available)]);
            if count > available {
                result.failure = Some((
                    FailureCode::OutputLimit,
                    "backend exceeded its combined output limit".into(),
                ));
                break;
            }
        }
    }
    while result.failure.is_none() {
        match group.exited_without_reaping() {
            Ok(true) => break,
            Err(e) => {
                result.failure = Some((FailureCode::LaunchError, e));
                break;
            }
            Ok(false) => (),
        }
        if started.elapsed() >= timeout {
            result.failure = Some((FailureCode::Timeout, "backend exceeded its timeout".into()));
            break;
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    match group.cleanup_and_wait() {
        Ok(status) => {
            result.returncode = status
                .code()
                .or_else(|| status.signal().map(|signal| -signal));
            if result.failure.is_none() && !status.success() {
                result.failure = Some((
                    FailureCode::NonzeroExit,
                    "backend exited with a nonzero status".into(),
                ));
            }
        }
        Err(e) if result.failure.is_none() => result.failure = Some((FailureCode::LaunchError, e)),
        Err(_) => (),
    }
    result
}
#[cfg(not(target_os = "linux"))]
fn bounded_process(_argv: &[String], _config: &ExternalBackendConfig) -> ProcessResult {
    ProcessResult {
        failure: Some((
            FailureCode::LaunchError,
            "bounded external adapters require Linux owned process groups".into(),
        )),
        ..Default::default()
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProfileSelectorKind {
    Name,
    Satyaml,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrSatellitesProfile {
    pub selector: String,
    pub selector_kind: ProfileSelectorKind,
    pub transmitters: Vec<formats::SatYamlTransmitter>,
    pub name: Option<String>,
    pub norad_id: Option<u64>,
}
impl From<&formats::SatYamlProfile> for GrSatellitesProfile {
    fn from(profile: &formats::SatYamlProfile) -> Self {
        Self {
            selector: profile.selector.clone(),
            selector_kind: ProfileSelectorKind::Satyaml,
            transmitters: profile.transmitters.clone(),
            name: Some(profile.name.clone()),
            norad_id: Some(profile.norad_id),
        }
    }
}
impl GrSatellitesProfile {
    pub fn validate(&self) -> Result<(), String> {
        nonempty(&self.selector, "profile selector")?;
        if let Some(name) = &self.name {
            nonempty(name, "profile name")?;
        }
        if self.norad_id == Some(0) {
            return Err("profile NORAD must be positive".into());
        }
        if self.transmitters.is_empty() {
            return Err("profile must declare at least one transmitter".into());
        }
        let mut ids = BTreeSet::new();
        for tx in &self.transmitters {
            nonempty(&tx.transmitter_id, "transmitter ID")?;
            nonempty(&tx.modulation, "modulation")?;
            nonempty(&tx.framing, "framing")?;
            strings(&tx.fec, "FEC")?;
            if !ids.insert(&tx.transmitter_id) {
                return Err("profile transmitter IDs must be unique".into());
            }
        }
        Ok(())
    }
}
fn default_kiss_bound() -> usize {
    16 * 1024 * 1024
}
fn yes() -> bool {
    true
}
fn gr_id() -> String {
    "gr_satellites".into()
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrSatellitesBackend {
    pub executable: String,
    pub executable_version: String,
    pub profile: GrSatellitesProfile,
    pub timeout_seconds: f64,
    pub max_output_bytes: usize,
    #[serde(default = "default_kiss_bound")]
    pub max_kiss_bytes: usize,
    #[serde(default = "yes")]
    pub supports_rawint16_iq: bool,
    #[serde(default = "yes")]
    pub supports_rawfile_complex64: bool,
    #[serde(default = "gr_id")]
    pub backend_id: String,
}
impl GrSatellitesBackend {
    pub fn supports_sample_ranges(&self) -> bool {
        false
    }
    pub fn supported_sample_formats(&self) -> Vec<String> {
        let mut formats = vec![];
        if cfg!(target_endian = "little") {
            if self.supports_rawint16_iq {
                formats.push("ci16_le".into());
            }
            if self.supports_rawfile_complex64 {
                formats.push("cf32_le".into());
            }
        }
        formats
    }
    pub fn capabilities(&self) -> ExternalBackendCapabilities {
        ExternalBackendCapabilities {
            modulations: self
                .profile
                .transmitters
                .iter()
                .map(|t| t.modulation.clone())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
            framing: self
                .profile
                .transmitters
                .iter()
                .map(|t| t.framing.clone())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
            fec: self
                .profile
                .transmitters
                .iter()
                .flat_map(|t| t.fec.iter().cloned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
            sample_formats: self.supported_sample_formats(),
        }
    }
    fn config(&self) -> ExternalBackendConfig {
        ExternalBackendConfig {
            backend_id: self.backend_id.clone(),
            backend_version: self.executable_version.clone(),
            capabilities: self.capabilities(),
            timeout_seconds: self.timeout_seconds,
            max_output_bytes: self.max_output_bytes,
            max_file_bytes: (self.max_kiss_bytes as u64).saturating_add(1),
            environment_overrides: BTreeMap::from([(
                "GR_SATELLITES_SUBMIT_TLM".into(),
                "0".into(),
            )]),
        }
    }
    pub fn validate(&self) -> Result<(), String> {
        nonempty(&self.executable, "executable")?;
        self.profile.validate()?;
        self.config().validate()?;
        if self.max_kiss_bytes == 0 || self.max_kiss_bytes > MAX_CAPTURED_BYTES {
            return Err("KISS file bound must be 1..64 MiB".into());
        }
        if self.supported_sample_formats().is_empty() {
            return Err("at least one supported little-endian IQ format must be enabled".into());
        }
        Ok(())
    }
    pub fn validate_segment(&self, segment: &CaptureSegment) -> Result<(), String> {
        self.validate()?;
        segment.validate()?;
        if !self
            .supported_sample_formats()
            .contains(&segment.sample_format)
        {
            return Err(format!(
                "unsupported IQ sample format: {}",
                segment.sample_format
            ));
        }
        if segment.start_sample != 0 || segment.sample_count.is_some() {
            return Err("this gr-satellites CLI cannot map sample start/count; materialize the exact segment as a separate full file".into());
        }
        if self.profile.selector_kind == ProfileSelectorKind::Satyaml {
            input::open_regular(Path::new(&self.profile.selector))?;
            if !self.profile.selector.to_ascii_lowercase().ends_with(".yml") {
                return Err("gr-satellites SatYAML file selectors must end in .yml".into());
            }
        } else if self.profile.selector.starts_with('-') {
            return Err("gr-satellites profile names must not begin with '-'".into());
        }
        if let Some(value) = segment.hints.get("start_time").filter(|v| !v.is_null()) {
            nonempty(
                value.as_str().ok_or("start_time must be a string")?,
                "start_time",
            )?;
        }
        Ok(())
    }
    pub fn build_argv(
        &self,
        segment: &CaptureSegment,
        kiss_output_path: &Path,
    ) -> Result<Vec<String>, String> {
        self.validate_segment(segment)?;
        let selector = if self.profile.selector_kind == ProfileSelectorKind::Satyaml {
            // Resolve relative SatYAML paths, including a name beginning '-',
            // so they cannot become options in the external CLI.
            Path::new(&self.profile.selector)
                .canonicalize()
                .map_err(|e| e.to_string())?
                .to_str()
                .ok_or("non-UTF8 SatYAML path")?
                .to_owned()
        } else {
            self.profile.selector.clone()
        };
        let mut argv = vec![
            self.executable.clone(),
            selector,
            if segment.sample_format == "ci16_le" {
                "--rawint16"
            } else {
                "--rawfile"
            }
            .into(),
            segment.path.to_str().ok_or("non-UTF8 capture path")?.into(),
            "--samp_rate".into(),
            format_g17(segment.sample_rate_hz),
            "--iq".into(),
            "--kiss_out".into(),
            kiss_output_path
                .to_str()
                .ok_or("non-UTF8 KISS path")?
                .into(),
            "--hexdump".into(),
        ];
        if let Some(Value::String(start)) = segment.hints.get("start_time") {
            argv.extend(["--start_time".into(), start.clone()]);
        }
        Ok(argv)
    }
    pub fn run(&self, segment: &CaptureSegment) -> ExternalBackendResult {
        let config = self.config();
        if let Err(message) = self.validate_segment(segment) {
            return config.failure(
                FailureCode::InputError,
                message,
                vec![],
                ProcessResult::default(),
            );
        }
        let directory = match tempfile::Builder::new()
            .prefix("telemetry-yield-grsat-")
            .tempdir()
        {
            Ok(directory) => directory,
            Err(e) => {
                return config.failure(
                    FailureCode::LaunchError,
                    e.to_string(),
                    vec![],
                    ProcessResult::default(),
                );
            }
        };
        let kiss_path = directory.path().join("decoded.kiss");
        run_external_backend(
            &config,
            segment,
            |segment| self.build_argv(segment, &kiss_path),
            |_, _| {
                // Owned output must not redirect to an unrelated file or FIFO.
                let metadata = std::fs::symlink_metadata(&kiss_path)
                    .map_err(|e| format!("missing KISS output: {e}"))?;
                if !metadata.is_file() || metadata.file_type().is_symlink() {
                    return Err("KISS output must be a regular non-symlink file".into());
                }
                let mut bytes = vec![];
                input::open_regular(&kiss_path)?
                    .take(self.max_kiss_bytes as u64 + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|e| e.to_string())?;
                if bytes.len() > self.max_kiss_bytes {
                    return Err("gr-satellites KISS output exceeded its byte limit".into());
                }
                let parsed = formats::parse_kiss(&bytes)?;
                let mut candidates = vec![];
                let mut candidate_bytes = 0usize;
                for frame in parsed.data_frames {
                    let provenance = json!({
                            "container":"kiss", "kiss_port":frame.port, "kiss_record_index":frame.record_index,
                            "kiss_timestamp_ms":frame.timestamp_ms, "malformed_kiss_records":parsed.malformed_records,
                            "profile_selector":self.profile.selector, "profile_selector_kind":self.profile.selector_kind,
                            "profile_name":self.profile.name, "profile_norad_id":self.profile.norad_id,
                            "profile_transmitters":self.profile.transmitters, "transmitter_attribution":null,
                            "candidate_validation":"pending_downstream_validator"
                    });
                    let provenance = provenance.as_object().unwrap().clone();
                    let metadata_bytes = candidate_key(&provenance)?.len();
                    candidate_bytes = candidate_bytes
                        .checked_add(frame.payload.len())
                        .and_then(|n| n.checked_add(metadata_bytes))
                        .and_then(|n| n.checked_add(512))
                        .ok_or("KISS candidate allocation estimate overflow")?;
                    if candidate_bytes > MAX_CAPTURED_BYTES {
                        return Err(
                            "KISS candidate metadata exceeds 64 MiB allocation bound".into()
                        );
                    }
                    candidates.push(ParsedByteCandidate {
                        payload: frame.payload,
                        provenance,
                    });
                }
                Ok(candidates)
            },
        )
    }
}

// Compatibility with Python format(value, '.17g'), without a Python runtime.
fn format_g17(value: f64) -> String {
    let scientific = format!("{value:.16e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap();
    let exponent: i32 = exponent.parse().unwrap();
    let digits = mantissa.replace('.', "").trim_end_matches('0').to_owned();
    if !(-4..17).contains(&exponent) {
        let mut mantissa = digits[..1].to_owned();
        if digits.len() > 1 {
            mantissa.push('.');
            mantissa.push_str(&digits[1..]);
        }
        return format!(
            "{mantissa}e{}{abs:02}",
            if exponent < 0 { "-" } else { "+" },
            abs = exponent.abs()
        );
    }
    let split = exponent + 1;
    if split <= 0 {
        format!("0.{}{}", "0".repeat((-split) as usize), digits)
    } else if split as usize >= digits.len() {
        format!("{}{}", digits, "0".repeat(split as usize - digits.len()))
    } else {
        format!(
            "{}.{}",
            &digits[..split as usize],
            &digits[split as usize..]
        )
    }
}

#[cfg(test)]
#[path = "tests/backends_tests.rs"]
mod tests;
