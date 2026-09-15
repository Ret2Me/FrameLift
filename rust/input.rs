//! Bounded sample ingestion. No implicit representation or channel conversion.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

pub const MAX_AUDIO_BYTES: u64 = 512 * 1024 * 1024;
pub const MAX_AUDIO_SECONDS: f64 = 1800.0;
pub const MAX_LOADED_AUDIO_SAMPLES: u64 = MAX_AUDIO_BYTES / 4;
/// Bounded regular-file read for native CLI format adapters, never a FIFO stream.
pub fn read_bytes_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, String> {
    if maximum == 0 || maximum > 64 * 1024 * 1024 {
        return Err("byte input limit must be1..64MiB".into());
    }
    let mut bytes = Vec::new();
    open_regular(path)?
        .take(maximum + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > maximum {
        return Err("file exceeds configured byte bound".into());
    }
    Ok(bytes)
}
const MAX_LOG_BYTES: u64 = 1024 * 1024;
const MAX_JSON_BYTES: u64 = 64 * 1024 * 1024;

pub(crate) fn open_regular(path: &Path) -> Result<File, String> {
    let canonical = path
        .canonicalize()
        .map_err(|e| format!("{}: {e}", path.display()))?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // Avoid blocking on a FIFO if a path changes after canonicalization.
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let file = options
        .open(&canonical)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("input must be a regular file".into());
    }
    Ok(file)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Identity {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

pub fn identity(path: &Path) -> Result<Identity, String> {
    let canonical = path.canonicalize().map_err(|e| e.to_string())?;
    let mut file = open_regular(&canonical)?;
    let before = file.metadata().map_err(|e| e.to_string())?;
    if !before.is_file() {
        return Err("input must be a regular file".into());
    }
    let mut digest = Sha256::new();
    let mut buf = [0u8; 65536];
    let mut bytes = 0;
    loop {
        let n = file.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        digest.update(&buf[..n]);
        bytes += n as u64;
    }
    let after = file.metadata().map_err(|e| e.to_string())?;
    if before.len() != bytes
        || after.len() != bytes
        || before.modified().ok() != after.modified().ok()
    {
        return Err("file changed during hashing".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let current = fs::metadata(&canonical).map_err(|e| e.to_string())?;
        if current.dev() != after.dev()
            || current.ino() != after.ino()
            || before.ctime() != after.ctime()
            || before.ctime_nsec() != after.ctime_nsec()
        {
            return Err("file identity changed during hashing".into());
        }
    }
    Ok(Identity {
        path: canonical.display().to_string(),
        sha256: hex::encode(digest.finalize()),
        bytes,
    })
}

fn require_unchanged_source(path: &Path, expected: &Identity) -> Result<(), String> {
    let actual = identity(path)?;
    if actual.sha256 != expected.sha256
        || actual.bytes != expected.bytes
        || actual.path != expected.path
    {
        return Err("source input changed during probe/conversion/reading".into());
    }
    Ok(())
}

pub fn write_json_new(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    {
        let mut writer = BufWriter::with_capacity(64 * 1024, &mut file);
        serde_json::to_writer_pretty(&mut writer, value).map_err(|e| e.to_string())?;
        writer.write_all(b"\n").map_err(|e| e.to_string())?;
        // Flush before syncing or publishing. Serialization/write failures
        // still drop the unpublished temporary file, never a partial result.
        writer.flush().map_err(|e| e.to_string())?;
    }
    file.as_file().sync_all().map_err(|e| e.to_string())?;
    file.persist_noclobber(path).map_err(|e| e.to_string())?;
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())
}

pub fn read_json(path: &Path) -> Result<serde_json::Value, String> {
    let file = open_regular(path)?;
    if file.metadata().map_err(|e| e.to_string())?.len() > MAX_JSON_BYTES {
        return Err("JSON exceeds 64 MiB limit".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_JSON_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_JSON_BYTES {
        return Err("JSON grew beyond 64 MiB limit".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", path.display()))
}

#[cfg(target_os = "linux")]
pub(crate) struct OwnedProcessGroup {
    child: Child,
    reaped: bool,
}

#[cfg(target_os = "linux")]
impl OwnedProcessGroup {
    /// Only a newly spawned, unreaped child whose pre_exec created its own
    /// process group may be adopted. No caller-supplied PID is accepted.
    pub(crate) fn new(mut child: Child) -> Result<Self, String> {
        let pid = i32::try_from(child.id()).map_err(|e| e.to_string())?;
        // SAFETY: getpgid queries the owned child; a mismatch never signals a group.
        if unsafe { libc::getpgid(pid) } != pid {
            let _ = child.kill();
            let _ = child.wait();
            return Err("external child must own its process group before adoption".into());
        }
        Ok(Self {
            child,
            reaped: false,
        })
    }
    // WNOWAIT preserves the direct child PID until group cleanup. An unrelated
    // process can therefore never reuse the group identifier before we kill it.
    pub(crate) fn exited_without_reaping(&mut self) -> Result<bool, String> {
        loop {
            let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
            let status = unsafe {
                libc::waitid(
                    libc::P_PID,
                    self.child.id(),
                    &mut info,
                    libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
                )
            };
            if status != 0 {
                let error = std::io::Error::last_os_error();
                if error.raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                if error.raw_os_error() == Some(libc::ECHILD) {
                    // A caller-installed reaper consumed our ownership anchor.
                    // Refuse to signal a possibly recycled process/group ID.
                    self.reaped = true;
                }
                return Err(format!("waitid for owned codec process: {error}"));
            }
            return Ok(unsafe { info.si_pid() } != 0);
        }
    }

    pub(crate) fn cleanup_and_wait(&mut self) -> Result<ExitStatus, String> {
        if self.reaped {
            return Err("codec child ownership already released".into());
        }
        self.exited_without_reaping()?;
        let pid = i32::try_from(self.child.id()).map_err(|e| e.to_string())?;
        let signalled = unsafe { libc::kill(-pid, libc::SIGKILL) };
        if signalled != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!("owned process-group cleanup: {error}"));
            }
        }
        // Also terminate the owned direct child if it changed its own group.
        let _ = self.child.kill();
        let status = self.child.wait().map_err(|e| e.to_string())?;
        self.reaped = true;
        Ok(status)
    }
}

#[cfg(target_os = "linux")]
impl Drop for OwnedProcessGroup {
    fn drop(&mut self) {
        if !self.reaped {
            let _ = self.cleanup_and_wait();
        }
    }
}

// Not a security sandbox: the known codec must not deliberately escape its
// process group. All normal exits, timeouts and I/O errors clean up that group.
#[cfg(target_os = "linux")]
fn external_command(
    mut command: Command,
    timeout: Duration,
    file_size_limit: u64,
) -> Result<Vec<u8>, String> {
    use std::os::unix::process::CommandExt;
    if file_size_limit == 0 {
        return Err("external file-size limit must be positive".into());
    }
    let mut stdout = tempfile::tempfile().map_err(|e| e.to_string())?;
    let stderr = tempfile::tempfile().map_err(|e| e.to_string())?;
    command
        .stdin(Stdio::null())
        .stdout(stdout.try_clone().map_err(|e| e.to_string())?)
        .stderr(stderr.try_clone().map_err(|e| e.to_string())?);
    let codec_parent = unsafe { libc::getpid() };
    // Only async-signal-safe libc calls are made after fork and before exec.
    unsafe {
        command.pre_exec(move || {
            // The progressive supervisor may terminate our caller. Codecs own
            // a separate group, so also bind their lifetime to that caller.
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() != codec_parent {
                return Err(std::io::Error::from_raw_os_error(libc::ESRCH));
            }
            if libc::setpgid(0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            let limit = libc::rlimit {
                rlim_cur: file_size_limit as libc::rlim_t,
                rlim_max: file_size_limit as libc::rlim_t,
            };
            if libc::setrlimit(libc::RLIMIT_FSIZE, &limit) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let child = command.spawn().map_err(|e| format!("codec spawn: {e}"))?;
    let mut group = OwnedProcessGroup {
        child,
        reaped: false,
    };
    let start = Instant::now();
    loop {
        if stdout.metadata().map_err(|e| e.to_string())?.len() > MAX_LOG_BYTES
            || stderr.metadata().map_err(|e| e.to_string())?.len() > MAX_LOG_BYTES
        {
            return Err("codec exceeded stdout/stderr limit".into());
        }
        if group.exited_without_reaping()? {
            break;
        }
        if start.elapsed() > timeout {
            return Err("codec exceeded timeout".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let status = group.cleanup_and_wait()?;
    // A fast process may complete between polls; both final logs are checked.
    if stdout.metadata().map_err(|e| e.to_string())?.len() > MAX_LOG_BYTES
        || stderr.metadata().map_err(|e| e.to_string())?.len() > MAX_LOG_BYTES
    {
        return Err("codec final stdout/stderr exceeds limit".into());
    }
    if !status.success() {
        return Err(format!("codec exited with {status}"));
    }
    stdout.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    stdout
        .take(MAX_LOG_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_LOG_BYTES {
        return Err("codec stdout grew beyond limit".into());
    }
    Ok(bytes)
}

#[cfg(not(target_os = "linux"))]
fn external_command(
    _command: Command,
    _timeout: Duration,
    _file_size_limit: u64,
) -> Result<Vec<u8>, String> {
    Err("bounded external codecs currently require Linux process ownership primitives".into())
}

pub(crate) fn external_with_file_limit(
    program: &str,
    args: &[String],
    seconds: u64,
    file_limit: u64,
) -> Result<Vec<u8>, String> {
    let mut command = Command::new(program);
    command.args(args);
    external_command(command, Duration::from_secs(seconds), file_limit)
        .map_err(|e| format!("{program}: {e}"))
}

/// External codecs never call Python. Logs, timeout and process cleanup are bounded.
pub fn external(program: &str, args: &[String], seconds: u64) -> Result<Vec<u8>, String> {
    external_with_file_limit(program, args, seconds, MAX_LOG_BYTES + 1)
}

pub struct AudioInput {
    pub samples: Vec<f64>,
    pub sample_rate: u32,
    pub wav_identity: Identity,
    pub source_identity: Identity,
    pub conversion_command: Option<Vec<String>>,
    _temporary: Option<tempfile::TempDir>,
}

fn read_wav(path: &Path) -> Result<(Vec<f64>, u32), String> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > MAX_AUDIO_BYTES {
        return Err("WAV exceeds 512 MiB limit".into());
    }
    // Hound requests individual samples from Read. Buffer the already-checked
    // regular file to avoid a syscall per sample; parsing, scaling and the
    // surrounding source-identity rechecks remain unchanged.
    let source = BufReader::with_capacity(64 * 1024, open_regular(path)?);
    let mut reader = hound::WavReader::new(source).map_err(|e| e.to_string())?;
    let spec = reader.spec();
    if spec.channels != 1 || spec.sample_rate == 0 {
        return Err(
            "audio must be mono, with explicit positive sample rate; no implicit downmix".into(),
        );
    }
    if reader.duration() as f64 / spec.sample_rate as f64 > MAX_AUDIO_SECONDS {
        return Err("audio exceeds 1800 second limit".into());
    }
    if u64::from(reader.len()) > MAX_LOADED_AUDIO_SAMPLES {
        return Err("decoded sample buffer exceeds 1 GiB f64 working-set limit".into());
    }
    let samples = match spec.sample_format {
        hound::SampleFormat::Float => {
            if spec.bits_per_sample != 32 {
                return Err("only float32 WAV is supported".into());
            }
            reader
                .samples::<f32>()
                .map(|v| v.map(|x| x as f64))
                .collect::<Result<Vec<_>, _>>()
        }
        hound::SampleFormat::Int => {
            if !(1..=32).contains(&spec.bits_per_sample) {
                return Err("unsupported integer WAV precision".into());
            }
            let scale = 2f64.powi(spec.bits_per_sample as i32 - 1);
            reader
                .samples::<i32>()
                .map(|v| v.map(|x| x as f64 / scale))
                .collect::<Result<Vec<_>, _>>()
        }
    }
    .map_err(|e| e.to_string())?;
    if samples.len() < 8192 || samples.iter().any(|x| !x.is_finite()) {
        return Err("audio requires >=8192 finite samples".into());
    }
    Ok((samples, spec.sample_rate))
}

pub fn load_audio(path: &Path, scratch: &Path) -> Result<AudioInput, String> {
    if open_regular(path)?
        .metadata()
        .map_err(|e| e.to_string())?
        .len()
        > MAX_AUDIO_BYTES
    {
        return Err("source input exceeds 512 MiB limit".into());
    }
    let source_identity = identity(path)?;
    let mut magic = [0u8; 4];
    open_regular(path)?
        .read_exact(&mut magic)
        .map_err(|e| e.to_string())?;
    let (wav, temporary, conversion) = if &magic == b"OggS" {
        if source_identity.bytes > 64 * 1024 * 1024 {
            return Err("OGG exceeds 64 MiB limit".into());
        }
        let probe_args = [
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
        ]
        .map(String::from)
        .into_iter()
        .chain([source_identity.path.clone()])
        .collect::<Vec<_>>();
        let probe: serde_json::Value =
            serde_json::from_slice(&external("ffprobe", &probe_args, 30)?)
                .map_err(|e| e.to_string())?;
        let streams = probe["streams"].as_array().ok_or("probe lacks streams")?;
        if streams.len() != 1 || streams[0]["codec_type"] != "audio" || streams[0]["channels"] != 1
        {
            return Err("OGG requires exactly one mono audio stream".into());
        }
        let duration = probe["format"]["duration"]
            .as_str()
            .ok_or("probe lacks duration")?
            .parse::<f64>()
            .map_err(|e| e.to_string())?;
        if !duration.is_finite() || !(0.0..=MAX_AUDIO_SECONDS).contains(&duration) {
            return Err("OGG duration exceeds bounds".into());
        }
        let temp = tempfile::Builder::new()
            .prefix("rust-audio-")
            .tempdir_in(scratch)
            .map_err(|e| e.to_string())?;
        let wav = temp.path().join("audio.wav");
        let args = vec![
            "-nostdin".into(),
            "-v".into(),
            "error".into(),
            "-n".into(),
            "-i".into(),
            source_identity.path.clone(),
            "-map".into(),
            "0:a:0".into(),
            "-c:a".into(),
            "pcm_f32le".into(),
            wav.display().to_string(),
        ];
        // RLIMIT_FSIZE applies during writes, not only after conversion. A
        // truncated/error conversion is rejected; no -t/-fs search pruning.
        external_with_file_limit("ffmpeg", &args, 120, MAX_AUDIO_BYTES)?;
        let command = std::iter::once("ffmpeg".into()).chain(args).collect();
        (wav, Some(temp), Some(command))
    } else {
        (path.to_path_buf(), None, None)
    };
    let wav_identity = identity(&wav)?;
    let (samples, sample_rate) = read_wav(&wav)?;
    let after = identity(&wav)?;
    if wav_identity.sha256 != after.sha256 {
        return Err("WAV changed during reading".into());
    }
    require_unchanged_source(path, &source_identity)?;
    Ok(AudioInput {
        samples,
        sample_rate,
        wav_identity,
        source_identity,
        conversion_command: conversion,
        _temporary: temporary,
    })
}

pub fn existing_new_dir(path: &Path) -> Result<PathBuf, String> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    fs::create_dir(path).map_err(|e| format!("new output directory {}: {e}", path.display()))?;
    path.canonicalize().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    // Independent frozen reader from before buffering. Keep checks and sample
    // conversion here instead of sharing implementation with read_wav.
    fn frozen_unbuffered_read_wav(path: &Path) -> Result<(Vec<f64>, u32), String> {
        if fs::metadata(path).map_err(|e| e.to_string())?.len() > MAX_AUDIO_BYTES {
            return Err("WAV exceeds 512 MiB limit".into());
        }
        let mut reader = hound::WavReader::new(open_regular(path)?).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        if spec.channels != 1 || spec.sample_rate == 0 {
            return Err(
                "audio must be mono, with explicit positive sample rate; no implicit downmix"
                    .into(),
            );
        }
        if reader.duration() as f64 / spec.sample_rate as f64 > MAX_AUDIO_SECONDS {
            return Err("audio exceeds 1800 second limit".into());
        }
        if u64::from(reader.len()) > MAX_LOADED_AUDIO_SAMPLES {
            return Err("decoded sample buffer exceeds 1 GiB f64 working-set limit".into());
        }
        let samples = match spec.sample_format {
            hound::SampleFormat::Float => {
                if spec.bits_per_sample != 32 {
                    return Err("only float32 WAV is supported".into());
                }
                reader
                    .samples::<f32>()
                    .map(|v| v.map(|x| x as f64))
                    .collect::<Result<Vec<_>, _>>()
            }
            hound::SampleFormat::Int => {
                if !(1..=32).contains(&spec.bits_per_sample) {
                    return Err("unsupported integer WAV precision".into());
                }
                let scale = 2f64.powi(spec.bits_per_sample as i32 - 1);
                reader
                    .samples::<i32>()
                    .map(|v| v.map(|x| x as f64 / scale))
                    .collect::<Result<Vec<_>, _>>()
            }
        }
        .map_err(|e| e.to_string())?;
        if samples.len() < 8192 || samples.iter().any(|x| !x.is_finite()) {
            return Err("audio requires >=8192 finite samples".into());
        }
        Ok((samples, spec.sample_rate))
    }

    fn assert_buffered_wav_matches(path: &Path) -> Result<(Vec<f64>, u32), String> {
        let bits = |result: &Result<(Vec<f64>, u32), String>| {
            result
                .as_ref()
                .map(|(samples, rate)| {
                    (
                        samples.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                        *rate,
                    )
                })
                .map_err(Clone::clone)
        };
        let expected = frozen_unbuffered_read_wav(path);
        let actual = read_wav(path);
        assert_eq!(bits(&actual), bits(&expected), "{}", path.display());
        actual
    }

    // Hand-built PCM/IEEE_FLOAT fixture with optional WAVEFORMATEXTENSIBLE valid
    // width, so coverage is not limited to the formats accepted by WavWriter.
    fn wav_fixture(tag: u16, container_bits: u16, valid_bits: Option<u16>, data: &[u8]) -> Vec<u8> {
        let width = container_bits.div_ceil(8);
        let fmt_len = if valid_bits.is_some() { 40_u32 } else { 16 };
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(20 + fmt_len + data.len() as u32).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&fmt_len.to_le_bytes());
        bytes.extend_from_slice(
            &if valid_bits.is_some() {
                0xfffe_u16
            } else {
                tag
            }
            .to_le_bytes(),
        );
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&48000_u32.to_le_bytes());
        bytes.extend_from_slice(&(48000_u32 * u32::from(width)).to_le_bytes());
        bytes.extend_from_slice(&width.to_le_bytes());
        bytes.extend_from_slice(&container_bits.to_le_bytes());
        if let Some(bits) = valid_bits {
            bytes.extend_from_slice(&22_u16.to_le_bytes());
            bytes.extend_from_slice(&bits.to_le_bytes());
            bytes.extend_from_slice(&0_u32.to_le_bytes());
            bytes.extend_from_slice(&u32::from(tag).to_le_bytes());
            bytes.extend_from_slice(&[0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71]);
        }
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&(data.len() as u32).to_le_bytes());
        bytes.extend_from_slice(data);
        bytes
    }

    #[test]
    fn buffered_wav_matches_frozen_float_bits_and_nonfinite_errors() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("float.wav");
        let pattern = [
            0.0_f32,
            -0.0,
            0.125,
            -0.75,
            f32::MAX,
            -f32::MAX,
            f32::MIN_POSITIVE,
            f32::from_bits(1),
        ];
        let mut data: Vec<_> = (0..20003)
            .flat_map(|i| pattern[i % pattern.len()].to_le_bytes())
            .collect();
        // More than 64 KiB, crossing the buffer boundary within sample data.
        fs::write(&path, wav_fixture(3, 32, None, &data)).unwrap();
        let (samples, rate) = assert_buffered_wav_matches(&path).unwrap();
        assert_eq!(rate, 48000);
        assert_eq!(samples.len(), 20003);
        for (i, sample) in samples.iter().enumerate() {
            assert_eq!(
                sample.to_bits(),
                (pattern[i % pattern.len()] as f64).to_bits()
            );
        }
        for invalid in [
            f32::NAN,
            f32::from_bits(0x7fa0_0042),
            f32::INFINITY,
            f32::NEG_INFINITY,
        ] {
            data[4 * 17000..4 * 17001].copy_from_slice(&invalid.to_le_bytes());
            fs::write(&path, wav_fixture(3, 32, None, &data)).unwrap();
            assert_eq!(
                assert_buffered_wav_matches(&path).unwrap_err(),
                "audio requires >=8192 finite samples"
            );
        }
    }

    #[test]
    fn buffered_wav_matches_frozen_integer_formats_and_all_valid_width_metadata() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("integer.wav");
        for bits in [8_u16, 16, 24, 32] {
            let limit = 1_i64 << (bits - 1);
            let pattern = [-limit, -1, 0, 1, limit - 1];
            let mut data = Vec::new();
            for i in 0..22001 {
                let sample = pattern[i % pattern.len()] as i32;
                if bits == 8 {
                    data.push((sample + 128) as u8);
                } else {
                    data.extend_from_slice(&sample.to_le_bytes()[..usize::from(bits / 8)]);
                }
            }
            fs::write(&path, wav_fixture(1, bits, None, &data)).unwrap();
            let (samples, _) = assert_buffered_wav_matches(&path).unwrap();
            for (i, sample) in samples.iter().enumerate() {
                assert_eq!(
                    sample.to_bits(),
                    (pattern[i % pattern.len()] as f64 / limit as f64).to_bits()
                );
            }
        }
        // The outer reader permits metadata 1..=32, while hound currently
        // supports 8/16/24/32 samples. Preserve both acceptance and its errors.
        for bits in 1_u16..=32 {
            let container_bits = bits.div_ceil(8) * 8;
            let data = vec![0_u8; 8192 * usize::from(container_bits / 8)];
            fs::write(&path, wav_fixture(1, container_bits, Some(bits), &data)).unwrap();
            let result = assert_buffered_wav_matches(&path);
            assert_eq!(
                result.is_ok(),
                [8, 16, 24, 32].contains(&bits),
                "valid bits {bits}"
            );
        }
        let pattern = [-8_388_608_i32, -1, 0, 1, 8_388_607];
        let data: Vec<_> = (0..20003)
            .flat_map(|i| pattern[i % 5].to_le_bytes())
            .collect();
        fs::write(&path, wav_fixture(1, 32, Some(24), &data)).unwrap();
        let (samples, _) = assert_buffered_wav_matches(&path).unwrap();
        for (i, sample) in samples.iter().enumerate() {
            assert_eq!(
                sample.to_bits(),
                (f64::from(pattern[i % 5]) / 8_388_608.0).to_bits()
            );
        }
    }

    #[test]
    fn buffered_wav_matches_frozen_truncation_metadata_and_size_errors() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("invalid.wav");
        let data = vec![0_u8; 20003 * 4];
        let valid = wav_fixture(3, 32, None, &data);
        for length in [
            0,
            1,
            3,
            4,
            8,
            12,
            16,
            20,
            24,
            28,
            32,
            36,
            40,
            43,
            44,
            45,
            65535,
            65536,
            valid.len() - 1,
        ] {
            fs::write(&path, &valid[..length]).unwrap();
            assert!(
                assert_buffered_wav_matches(&path).is_err(),
                "truncation {length}"
            );
        }
        for sample_count in [0, 8191, 8192] {
            fs::write(&path, wav_fixture(3, 32, None, &data[..sample_count * 4])).unwrap();
            assert_eq!(
                assert_buffered_wav_matches(&path).is_ok(),
                sample_count >= 8192
            );
        }
        for channels in [0_u16, 2] {
            let mut bytes = valid.clone();
            bytes[22..24].copy_from_slice(&channels.to_le_bytes());
            bytes[28..32].copy_from_slice(&(48000_u32 * 4 * u32::from(channels)).to_le_bytes());
            bytes[32..34].copy_from_slice(&(channels * 4).to_le_bytes());
            fs::write(&path, bytes).unwrap();
            assert!(assert_buffered_wav_matches(&path).is_err());
        }
        let mut zero_rate = valid.clone();
        zero_rate[24..32].fill(0);
        fs::write(&path, zero_rate).unwrap();
        assert!(assert_buffered_wav_matches(&path).is_err());
        for (tag, container, valid_bits) in [
            (3, 64, None),
            (3, 32, Some(24)),
            (1, 32, Some(33)),
            (1, 0, None),
            (1, 12, None),
            (99, 32, None),
        ] {
            fs::write(&path, wav_fixture(tag, container, valid_bits, &data)).unwrap();
            assert!(assert_buffered_wav_matches(&path).is_err());
        }
        // Header-declared bounds are checked before allocating/reading payload.
        for (rate, count, expected) in [
            (1_u32, 2000_u32, "audio exceeds 1800 second limit"),
            (
                96000,
                MAX_LOADED_AUDIO_SAMPLES as u32 + 1,
                "decoded sample buffer exceeds 1 GiB f64 working-set limit",
            ),
        ] {
            let mut bytes = wav_fixture(3, 32, None, &[]);
            bytes[24..28].copy_from_slice(&rate.to_le_bytes());
            bytes[28..32].copy_from_slice(&(rate * 4).to_le_bytes());
            bytes[40..44].copy_from_slice(&(count * 4).to_le_bytes());
            fs::write(&path, bytes).unwrap();
            assert_eq!(assert_buffered_wav_matches(&path).unwrap_err(), expected);
        }
        File::create(&path)
            .unwrap()
            .set_len(MAX_AUDIO_BYTES + 1)
            .unwrap();
        assert_eq!(
            assert_buffered_wav_matches(&path).unwrap_err(),
            "WAV exceeds 512 MiB limit"
        );
    }

    #[test]
    fn buffered_wav_keeps_regular_file_gate_and_source_rechecks() {
        let temp = tempfile::tempdir().unwrap();
        assert!(assert_buffered_wav_matches(&temp.path().join("missing.wav")).is_err());
        assert!(assert_buffered_wav_matches(temp.path()).is_err());
        let path = temp.path().join("input.wav");
        fs::write(&path, wav_fixture(3, 32, None, &vec![0_u8; 8192 * 4])).unwrap();
        let expected = identity(&path).unwrap();
        let scratch = temp.path().join("scratch");
        fs::create_dir(&scratch).unwrap();
        let loaded = load_audio(&path, &scratch).unwrap();
        assert_eq!(loaded.wav_identity.sha256, expected.sha256);
        assert_eq!(loaded.source_identity.sha256, expected.sha256);
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            let link = temp.path().join("link.wav");
            std::os::unix::fs::symlink(&path, &link).unwrap();
            assert!(assert_buffered_wav_matches(&link).is_ok());
            let fifo = temp.path().join("not-wav.fifo");
            let encoded = std::ffi::CString::new(fifo.as_os_str().as_bytes()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(encoded.as_ptr(), 0o600) }, 0);
            let started = Instant::now();
            assert!(assert_buffered_wav_matches(&fifo).is_err());
            assert!(started.elapsed() < Duration::from_secs(1));
        }
    }

    fn frozen_unbuffered_write_json_new(path: &Path, value: &impl Serialize) -> Result<(), String> {
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        serde_json::to_writer_pretty(&mut file, value).map_err(|e| e.to_string())?;
        file.write_all(b"\n").map_err(|e| e.to_string())?;
        file.as_file().sync_all().map_err(|e| e.to_string())?;
        file.persist_noclobber(path).map_err(|e| e.to_string())?;
        File::open(parent)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())
    }

    #[test]
    fn buffered_json_is_byte_identical_to_frozen_writer_and_keeps_no_clobber() {
        let temp = tempfile::tempdir().unwrap();
        for size in [0, 1, 20003] {
            let value = serde_json::json!({
                "escaped":"quoted \" slash \\ newline \n", "unicode":"zażółć 🛰", "negative_zero":-0.0,
                "values":(0..size).map(|i| serde_json::json!({"index":i,"gain":0.75,"payload":"0123456789abcdef"})).collect::<Vec<_>>(),
                "nested":{"null":null,"bool":true,"tiny":f64::from_bits(1)}
            });
            let old = temp.path().join(format!("{size}-old.json"));
            let new = temp.path().join(format!("{size}-new.json"));
            frozen_unbuffered_write_json_new(&old, &value).unwrap();
            write_json_new(&new, &value).unwrap();
            let bytes = fs::read(&new).unwrap();
            assert_eq!(bytes, fs::read(&old).unwrap());
            assert_eq!(bytes.last(), Some(&b'\n'));
            assert!(write_json_new(&new, &serde_json::json!({"replacement":true})).is_err());
            assert_eq!(fs::read(&new).unwrap(), bytes);
        }
    }

    #[test]
    fn buffered_json_failure_after_buffer_flush_never_publishes_or_overwrites() {
        struct FailsAfterPrefix;
        impl Serialize for FailsAfterPrefix {
            fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                use serde::ser::SerializeSeq;
                let mut seq = serializer.serialize_seq(Some(2))?;
                seq.serialize_element(&"x".repeat(128 * 1024))?;
                Err(serde::ser::Error::custom("injected after flushed prefix"))
            }
        }
        let temp = tempfile::tempdir().unwrap();
        let absent = temp.path().join("absent.json");
        assert_eq!(
            write_json_new(&absent, &FailsAfterPrefix).unwrap_err(),
            "injected after flushed prefix"
        );
        assert!(!absent.exists());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
        let existing = temp.path().join("existing.json");
        fs::write(&existing, b"previous result\n").unwrap();
        assert!(write_json_new(&existing, &FailsAfterPrefix).is_err());
        assert_eq!(fs::read(&existing).unwrap(), b"previous result\n");
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    }

    #[test]
    fn refuses_overwrite() {
        let temp = tempfile::tempdir().unwrap();
        let p = temp.path().join("result.json");
        write_json_new(&p, &serde_json::json!({"a":1})).unwrap();
        assert!(write_json_new(&p, &serde_json::json!({"a":2})).is_err());
        assert_eq!(read_json(&p).unwrap()["a"], 1);
    }
    #[test]
    fn wav_float_samples_are_exact_and_stereo_rejected() {
        let temp = tempfile::tempdir().unwrap();
        for channels in [1, 2] {
            let p = temp.path().join(format!("{channels}.wav"));
            let spec = hound::WavSpec {
                channels,
                sample_rate: 48000,
                bits_per_sample: 32,
                sample_format: hound::SampleFormat::Float,
            };
            let mut w = hound::WavWriter::create(&p, spec).unwrap();
            for _ in 0..8192 {
                w.write_sample(0.125f32).unwrap();
            }
            w.finalize().unwrap();
            let result = read_wav(&p);
            if channels == 1 {
                assert!(result.unwrap().0.iter().all(|x| *x == 0.125));
            } else {
                assert!(result.is_err());
            }
        }
    }

    #[test]
    fn source_recheck_rejects_same_length_mutation() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("source.ogg");
        fs::write(&path, b"before").unwrap();
        let expected = identity(&path).unwrap();
        require_unchanged_source(&path, &expected).unwrap();
        fs::write(&path, b"after!").unwrap();
        assert!(
            require_unchanged_source(&path, &expected)
                .unwrap_err()
                .contains("changed")
        );
    }

    #[test]
    fn rejects_directory_and_non_regular_json() {
        let temp = tempfile::tempdir().unwrap();
        assert!(identity(temp.path()).is_err());
        assert!(read_json(temp.path()).is_err());
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            let path = temp.path().join("not-json.fifo");
            let encoded = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(encoded.as_ptr(), 0o600) }, 0);
            let start = Instant::now();
            assert!(read_json(&path).is_err());
            assert!(identity(&path).is_err());
            assert!(start.elapsed() < Duration::from_secs(1));
        }
    }

    #[test]
    fn failed_serialization_never_publishes_partial_json() {
        struct Fails;
        impl Serialize for Fails {
            fn serialize<S: serde::Serializer>(&self, _: S) -> Result<S::Ok, S::Error> {
                Err(serde::ser::Error::custom("injected serialization error"))
            }
        }
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("result.json");
        assert!(write_json_new(&path, &Fails).is_err());
        assert!(!path.exists());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
    }

    #[cfg(target_os = "linux")]
    fn fixture_command(kind: &str, directory: &Path) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "input::tests::external_child_fixture",
                "--ignored",
                "--nocapture",
            ])
            .env("TELEMETRY_YIELD_CODEC_TEST_KIND", kind)
            .env("TELEMETRY_YIELD_CODEC_TEST_DIRECTORY", directory);
        command
    }

    #[cfg(target_os = "linux")]
    fn assert_no_running_descendant(directory: &Path) {
        let pid = fs::read_to_string(directory.join("descendant.pid")).unwrap();
        let stat = PathBuf::from(format!("/proc/{}/stat", pid.trim()));
        for _ in 0..100 {
            match fs::read_to_string(&stat) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
                Ok(data) => {
                    let state = data
                        .rsplit_once(')')
                        .unwrap()
                        .1
                        .trim_start()
                        .chars()
                        .next()
                        .unwrap();
                    if state == 'Z' || state == 'X' {
                        return;
                    }
                }
                Err(error) => panic!("cannot inspect test-owned descendant: {error}"),
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        panic!("test-owned descendant remained alive after codec cleanup");
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "private process fixture; invoked only by the bounded-process tests"]
    fn external_child_fixture() {
        let Ok(kind) = std::env::var("TELEMETRY_YIELD_CODEC_TEST_KIND") else {
            return;
        };
        let directory =
            PathBuf::from(std::env::var_os("TELEMETRY_YIELD_CODEC_TEST_DIRECTORY").unwrap());
        match kind.as_str() {
            "stderr" => {
                let _ = std::io::stderr().write_all(&vec![b'e'; MAX_LOG_BYTES as usize + 128]);
            }
            "stdout" => {
                let _ = std::io::stdout().write_all(&vec![b'o'; MAX_LOG_BYTES as usize + 128]);
            }
            "file_overflow" => {
                let mut file = File::create(directory.join("codec-output.bin")).unwrap();
                let _ = file.write_all(&vec![b'x'; 262144]);
            }
            "sleep" => std::thread::sleep(Duration::from_secs(30)),
            "fork_timeout" | "fork_parent_exit" | "fork_output_error" => {
                // Deliberate orphan fixture: the parent controller verifies
                // owned-group cleanup even after this intermediate exits.
                #[allow(clippy::zombie_processes)]
                let child = fixture_command("sleep", &directory).spawn().unwrap();
                fs::write(directory.join("descendant.pid"), child.id().to_string()).unwrap();
                if kind == "fork_output_error" {
                    let _ = std::io::stderr().write_all(&vec![b'e'; MAX_LOG_BYTES as usize + 128]);
                }
                if kind != "fork_parent_exit" {
                    std::thread::sleep(Duration::from_secs(30));
                }
            }
            _ => panic!("unknown private child fixture"),
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn quick_successful_process_cannot_bypass_either_final_log_cap() {
        let temp = tempfile::tempdir().unwrap();
        for kind in ["stdout", "stderr"] {
            // Larger RLIMIT deliberately isolates the separate 1 MiB log cap.
            assert!(
                external_command(
                    fixture_command(kind, temp.path()),
                    Duration::from_secs(2),
                    MAX_LOG_BYTES * 2
                )
                .unwrap_err()
                .contains("stdout/stderr")
            );
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn kernel_limit_caps_owned_output_during_write() {
        let temp = tempfile::tempdir().unwrap();
        let limit = 65536;
        assert!(
            external_command(
                fixture_command("file_overflow", temp.path()),
                Duration::from_secs(2),
                limit
            )
            .is_err()
        );
        assert!(
            fs::metadata(temp.path().join("codec-output.bin"))
                .unwrap()
                .len()
                <= limit
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn timeout_kills_only_owned_group_including_descendant() {
        use std::os::unix::process::CommandExt;
        let temp = tempfile::tempdir().unwrap();
        let mut control_command = fixture_command("sleep", temp.path());
        control_command
            .process_group(0)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let mut control = OwnedProcessGroup {
            child: control_command.spawn().unwrap(),
            reaped: false,
        };
        assert!(
            external_command(
                fixture_command("fork_timeout", temp.path()),
                Duration::from_secs(1),
                MAX_LOG_BYTES + 1
            )
            .unwrap_err()
            .contains("timeout")
        );
        assert_no_running_descendant(temp.path());
        assert!(
            !control.exited_without_reaping().unwrap(),
            "unrelated control process was killed"
        );
        control.cleanup_and_wait().unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn successful_parent_exit_still_cleans_up_descendant() {
        let temp = tempfile::tempdir().unwrap();
        external_command(
            fixture_command("fork_parent_exit", temp.path()),
            Duration::from_secs(2),
            MAX_LOG_BYTES + 1,
        )
        .unwrap();
        assert_no_running_descendant(temp.path());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn output_error_drop_guard_cleans_up_descendant() {
        let temp = tempfile::tempdir().unwrap();
        assert!(
            external_command(
                fixture_command("fork_output_error", temp.path()),
                Duration::from_secs(2),
                MAX_LOG_BYTES * 2
            )
            .is_err()
        );
        assert_no_running_descendant(temp.path());
    }
}
