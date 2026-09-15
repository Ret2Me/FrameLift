//! Content-verified, seekable audio for bounded progressive tasks.
//!
//! Native WAV preparation hashes the file and reads its header, without making
//! a whole-recording f64 copy. Every window rechecks file metadata before and
//! after reading and authenticates the actual byte blocks against the frozen
//! full-file SHA256, even when filesystem timestamps do not change.
//! OGG conversion consumes a private bounded snapshot whose copied bytes match
//! the frozen source SHA256, then caches content-addressed float32 WAV using
//! exactly the existing loader's conversion (no resampling, gain change,
//! channel mixing or reduced precision).

use crate::input;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, Metadata};
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

#[path = "progressive_audio_integrity.rs"]
mod integrity;
use integrity::BlockProof;

const MAX_OGG_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparedMetadata {
    pub source: input::Identity,
    pub wav: input::Identity,
    pub sample_rate: u32,
    pub samples: usize,
    pub conversion_command: Option<Vec<String>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileStamp {
    bytes: u64,
    modified: Option<std::time::SystemTime>,
    #[cfg(unix)]
    dev: u64,
    #[cfg(unix)]
    ino: u64,
    #[cfg(unix)]
    ctime: (i64, i64),
    #[cfg(unix)]
    mtime: (i64, i64),
}

impl FileStamp {
    fn from_metadata(metadata: &Metadata) -> Result<Self, String> {
        if !metadata.is_file() {
            return Err("progressive audio requires a regular file".into());
        }
        #[cfg(unix)]
        use std::os::unix::fs::MetadataExt;
        Ok(Self {
            bytes: metadata.len(),
            modified: metadata.modified().ok(),
            #[cfg(unix)]
            dev: metadata.dev(),
            #[cfg(unix)]
            ino: metadata.ino(),
            #[cfg(unix)]
            ctime: (metadata.ctime(), metadata.ctime_nsec()),
            #[cfg(unix)]
            mtime: (metadata.mtime(), metadata.mtime_nsec()),
        })
    }
    fn check_path(&self, path: &Path) -> Result<(), String> {
        let now = Self::from_metadata(&fs::metadata(path).map_err(|e| e.to_string())?)?;
        if now != *self {
            return Err(format!("verified audio file changed: {}", path.display()));
        }
        Ok(())
    }
    fn check_handle(&self, file: &File) -> Result<(), String> {
        let now = Self::from_metadata(&file.metadata().map_err(|e| e.to_string())?)?;
        if now != *self {
            return Err("verified audio file handle changed".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
struct WavLayout {
    sample_rate: u32,
    samples: usize,
    bits: u16,
    format: hound::SampleFormat,
    storage_bytes: u16,
    data_offset: u64,
}

/// Immutable manifest metadata is distinct from invocation-local verified file
/// stamps. A same-content source relocation on resume preserves the original
/// manifest while reads use, and check, the currently supplied source path.
pub struct PreparedAudio {
    pub metadata: PreparedMetadata,
    source_path: PathBuf,
    wav_path: PathBuf,
    source_stamp: FileStamp,
    wav_stamp: FileStamp,
    wav_proof: BlockProof,
    layout: WavLayout,
}

fn verified(path: &Path, maximum: u64) -> Result<(input::Identity, FileStamp), String> {
    let file = input::open_regular(path)?;
    let before = FileStamp::from_metadata(&file.metadata().map_err(|e| e.to_string())?)?;
    if before.bytes > maximum {
        return Err("progressive audio file exceeds size limit".into());
    }
    before.check_path(path)?;
    let identity = input::identity(path)?;
    // Capture the stamp before hashing, not afterward: otherwise a mutation in
    // the interval between hashing and stamping could bless unverified bytes.
    before.check_handle(&file)?;
    before.check_path(path)?;
    if identity.bytes != before.bytes {
        return Err("audio identity size mismatch".into());
    }
    Ok((identity, before))
}

fn layout(path: &Path, stamp: &FileStamp, proof: &BlockProof) -> Result<WavLayout, String> {
    stamp.check_path(path)?;
    let file = input::open_regular(path)?;
    stamp.check_handle(&file)?;
    let reader = hound::WavReader::new(BufReader::with_capacity(65536, proof.reader(file)))
        .map_err(|e| e.to_string())?;
    let spec = reader.spec();
    let samples = reader.len() as usize;
    if spec.channels != 1 || spec.sample_rate == 0 {
        return Err("audio must be mono with a positive sample rate; no implicit downmix".into());
    }
    if !(8192..=input::MAX_LOADED_AUDIO_SAMPLES as usize).contains(&samples)
        || samples as f64 / spec.sample_rate as f64 > input::MAX_AUDIO_SECONDS
    {
        return Err(
            "WAV sample count or duration exceeds bounds (minimum 8192, maximum 1800s)".into(),
        );
    }
    let mut stream = reader.into_inner();
    let data_offset = stream.stream_position().map_err(|e| e.to_string())?;
    let length_offset = data_offset
        .checked_sub(4)
        .ok_or("invalid WAV data header")?;
    stream
        .seek(SeekFrom::Start(length_offset))
        .map_err(|e| e.to_string())?;
    let mut length_bytes = [0u8; 4];
    stream
        .read_exact(&mut length_bytes)
        .map_err(|e| e.to_string())?;
    let data_bytes = u64::from(u32::from_le_bytes(length_bytes));
    if data_bytes % samples as u64 != 0
        || data_offset
            .checked_add(data_bytes)
            .is_none_or(|end| end > stamp.bytes)
    {
        return Err("truncated or inconsistent WAV data length".into());
    }
    let storage_bytes = u16::try_from(data_bytes / samples as u64)
        .map_err(|_| "WAV storage width exceeds bounds")?;
    let supported = match spec.sample_format {
        hound::SampleFormat::Float => storage_bytes == 4 && spec.bits_per_sample == 32,
        hound::SampleFormat::Int => matches!(
            (storage_bytes, spec.bits_per_sample),
            (1, 8) | (2, 16) | (3, 24) | (4, 24) | (4, 32)
        ),
    };
    if !supported {
        return Err("unsupported WAV precision or storage width".into());
    }
    stamp.check_handle(stream.get_ref().get_ref())?;
    stamp.check_path(path)?;
    Ok(WavLayout {
        sample_rate: spec.sample_rate,
        samples,
        bits: spec.bits_per_sample,
        format: spec.sample_format,
        storage_bytes,
        data_offset,
    })
}

fn same_content(a: &input::Identity, b: &input::Identity) -> bool {
    a.sha256 == b.sha256 && a.bytes == b.bytes
}

fn cache_path(out: &Path, sha256: &str) -> Result<PathBuf, String> {
    if sha256.len() != 64
        || !sha256
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    {
        return Err("invalid prepared WAV checksum".into());
    }
    Ok(out
        .canonicalize()
        .map_err(|e| e.to_string())?
        .join(format!("prepared-{sha256}.wav")))
}

fn verify_cache(path: &Path) -> Result<(input::Identity, FileStamp), String> {
    if !fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_file()
    {
        return Err("prepared WAV cache must be a real regular file, not a symlink".into());
    }
    verified(path, input::MAX_AUDIO_BYTES)
}

/// Both external readers consume this invocation-owned copy. Hash the exact
/// byte slices written to it, so a source mutation within a filesystem stamp
/// tick cannot authenticate different input for ffprobe or ffmpeg. The private
/// directory and closed writable handle isolate ordinary writers to the source;
/// this is not protection against a process that can edit arbitrary private
/// files belonging to this user.
struct OggSnapshot {
    directory: tempfile::TempDir,
    path: PathBuf,
}

impl OggSnapshot {
    fn capture(
        source: &input::Identity,
        source_path: &Path,
        source_stamp: &FileStamp,
        scratch: &Path,
    ) -> Result<Self, String> {
        if source.bytes > MAX_OGG_BYTES {
            return Err("OGG exceeds 64 MiB limit".into());
        }
        source_stamp.check_path(source_path)?;
        let mut original = input::open_regular(source_path)?;
        source_stamp.check_handle(&original)?;
        let mut builder = tempfile::Builder::new();
        builder.prefix("seekable-audio-");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            builder.permissions(fs::Permissions::from_mode(0o700));
        }
        let directory = builder.tempdir_in(scratch).map_err(|e| e.to_string())?;
        let path = directory.path().join("verified-source.ogg");
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut copy = options.open(&path).map_err(|e| e.to_string())?;
        let mut digest = Sha256::new();
        let mut bytes = 0u64;
        let mut buffer = [0u8; 65536];
        while bytes < source.bytes {
            let remaining = (source.bytes - bytes).min(buffer.len() as u64) as usize;
            let length = original
                .read(&mut buffer[..remaining])
                .map_err(|e| e.to_string())?;
            if length == 0 {
                return Err("OGG snapshot byte count differs from frozen source".into());
            }
            copy.write_all(&buffer[..length])
                .map_err(|e| e.to_string())?;
            digest.update(&buffer[..length]);
            bytes += length as u64;
        }
        // One lookahead byte detects source growth without copying any data
        // beyond the frozen size or the 64 MiB snapshot bound.
        if original.read(&mut buffer[..1]).map_err(|e| e.to_string())? != 0 {
            return Err("OGG snapshot byte count differs from frozen source".into());
        }
        if hex::encode(digest.finalize()) != source.sha256 {
            return Err("OGG snapshot checksum differs from frozen source".into());
        }
        source_stamp.check_handle(&original)?;
        source_stamp.check_path(source_path)?;
        copy.sync_all().map_err(|e| e.to_string())?;
        let mut permissions = copy.metadata().map_err(|e| e.to_string())?.permissions();
        permissions.set_readonly(true);
        copy.set_permissions(permissions)
            .map_err(|e| e.to_string())?;
        drop(copy);
        Ok(Self { directory, path })
    }
}

fn convert_ogg(
    source: &input::Identity,
    source_path: &Path,
    source_stamp: &FileStamp,
    out: &Path,
    scratch: &Path,
) -> Result<(input::Identity, FileStamp, Vec<String>), String> {
    let snapshot = OggSnapshot::capture(source, source_path, source_stamp, scratch)?;
    let converted = convert_ogg_snapshot(snapshot, out)?;
    source_stamp.check_path(source_path)?;
    Ok(converted)
}

fn convert_ogg_snapshot(
    snapshot: OggSnapshot,
    out: &Path,
) -> Result<(input::Identity, FileStamp, Vec<String>), String> {
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
    .chain([snapshot.path.display().to_string()])
    .collect::<Vec<_>>();
    let probe: serde_json::Value =
        serde_json::from_slice(&input::external("ffprobe", &probe_args, 30)?)
            .map_err(|e| e.to_string())?;
    let streams = probe["streams"].as_array().ok_or("probe lacks streams")?;
    if streams.len() != 1 || streams[0]["codec_type"] != "audio" || streams[0]["channels"] != 1 {
        return Err("OGG requires exactly one mono audio stream".into());
    }
    let duration = probe["format"]["duration"]
        .as_str()
        .ok_or("probe lacks duration")?
        .parse::<f64>()
        .map_err(|e| e.to_string())?;
    if !duration.is_finite() || !(0.0..=input::MAX_AUDIO_SECONDS).contains(&duration) {
        return Err("OGG duration exceeds bounds".into());
    }
    let wav = snapshot.directory.path().join("audio.wav");
    let args = vec![
        "-nostdin".into(),
        "-v".into(),
        "error".into(),
        "-n".into(),
        "-i".into(),
        snapshot.path.display().to_string(),
        "-map".into(),
        "0:a:0".into(),
        "-c:a".into(),
        "pcm_f32le".into(),
        wav.display().to_string(),
    ];
    input::external_with_file_limit("ffmpeg", &args, 120, input::MAX_AUDIO_BYTES)?;
    let (converted, stamp) = verified(&wav, input::MAX_AUDIO_BYTES)?;
    let proof = BlockProof::capture(&wav, &stamp, &converted)?;
    layout(&wav, &stamp, &proof)?;
    let destination = cache_path(out, &converted.sha256)?;
    File::open(&wav)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    // Atomic, no-clobber publication. Invocation-owned scratch must live on the
    // same filesystem as the session, as it does in the progressive supervisor.
    match fs::hard_link(&wav, &destination) {
        Ok(()) => (),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(error) => {
            return Err(format!(
                "publish prepared WAV (same-filesystem scratch required): {error}"
            ));
        }
    }
    File::open(out)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    // Linking and unlinking change ctime. Finish both operations before the
    // final complete hash/stamp verification, so no cleanup-induced stamp
    // refresh could accidentally bless a concurrent unverified content change.
    drop(snapshot);
    let (published, published_stamp) = verify_cache(&destination)?;
    if !same_content(&converted, &published) {
        return Err("existing prepared WAV cache has invalid content".into());
    }
    let command = std::iter::once("ffmpeg".into()).chain(args).collect();
    Ok((published, published_stamp, command))
}

impl PreparedAudio {
    pub fn open(
        source: &Path,
        out: &Path,
        scratch: &Path,
        previous: Option<&PreparedMetadata>,
    ) -> Result<Self, String> {
        let (source_identity, source_stamp) = verified(source, input::MAX_AUDIO_BYTES)?;
        let mut magic = [0u8; 4];
        let mut source_file = input::open_regular(source)?;
        source_stamp.check_handle(&source_file)?;
        source_file
            .read_exact(&mut magic)
            .map_err(|e| e.to_string())?;
        source_stamp.check_handle(&source_file)?;
        source_stamp.check_path(source)?;
        let ogg = &magic == b"OggS";
        if ogg && source_identity.bytes > MAX_OGG_BYTES {
            return Err("OGG exceeds 64 MiB limit".into());
        }
        if let Some(previous) = previous {
            if !same_content(&source_identity, &previous.source)
                || previous.conversion_command.is_some() != ogg
            {
                return Err(
                    "resume refused: prepared audio source or representation changed".into(),
                );
            }
            let (wav_path, wav_identity, wav_stamp) = if ogg {
                let expected = cache_path(out, &previous.wav.sha256)?;
                if previous.wav.path != expected.display().to_string() {
                    return Err(
                        "prepared WAV path is not the session content-addressed cache".into(),
                    );
                }
                let (identity, stamp) = verify_cache(&expected)?;
                (expected, identity, stamp)
            } else {
                (
                    source.to_path_buf(),
                    source_identity.clone(),
                    source_stamp.clone(),
                )
            };
            if !same_content(&wav_identity, &previous.wav) {
                return Err("prepared WAV cache checksum mismatch".into());
            }
            let wav_proof = BlockProof::capture(&wav_path, &wav_stamp, &wav_identity)?;
            let info = layout(&wav_path, &wav_stamp, &wav_proof)?;
            if info.sample_rate != previous.sample_rate || info.samples != previous.samples {
                return Err("prepared WAV header differs from frozen metadata".into());
            }
            source_stamp.check_path(source)?;
            return Ok(Self {
                metadata: previous.clone(),
                source_path: source.to_path_buf(),
                wav_path,
                source_stamp,
                wav_stamp,
                wav_proof,
                layout: info,
            });
        }
        let (wav_identity, wav_stamp, conversion_command) = if ogg {
            let (identity, stamp, command) =
                convert_ogg(&source_identity, source, &source_stamp, out, scratch)?;
            (identity, stamp, Some(command))
        } else {
            (source_identity.clone(), source_stamp.clone(), None)
        };
        let wav_path = if ogg {
            PathBuf::from(&wav_identity.path)
        } else {
            source.to_path_buf()
        };
        let wav_proof = BlockProof::capture(&wav_path, &wav_stamp, &wav_identity)?;
        let info = layout(&wav_path, &wav_stamp, &wav_proof)?;
        source_stamp.check_path(source)?;
        let metadata = PreparedMetadata {
            source: source_identity,
            wav: wav_identity,
            sample_rate: info.sample_rate,
            samples: info.samples,
            conversion_command,
        };
        Ok(Self {
            metadata,
            source_path: source.to_path_buf(),
            wav_path,
            source_stamp,
            wav_stamp,
            wav_proof,
            layout: info,
        })
    }

    /// Read at most six seconds / the existing DSP sample bound. Results use
    /// exactly the frozen loader's f32-to-f64 or integer normalization arithmetic.
    pub fn get_window(&self, left: usize, right: usize) -> Result<Vec<f64>, String> {
        if left >= right
            || right > self.metadata.samples
            || !(8192..=crate::dsp::MAX_PCM_WINDOW_SAMPLES).contains(&(right - left))
            || (right - left) as f64 > 6.0 * self.metadata.sample_rate as f64
        {
            return Err(
                "audio window must be in bounds, contain >=8192 samples and be <=6 seconds".into(),
            );
        }
        self.source_stamp.check_path(&self.source_path)?;
        self.wav_stamp.check_path(&self.wav_path)?;
        let file = input::open_regular(&self.wav_path)?;
        self.wav_stamp.check_handle(&file)?;
        let mut reader =
            hound::WavReader::new(BufReader::with_capacity(65536, self.wav_proof.reader(file)))
                .map_err(|e| e.to_string())?;
        let mut samples;
        if self.layout.storage_bytes == self.layout.bits / 8 {
            reader
                .seek(u32::try_from(left).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
            samples = match self.layout.format {
                hound::SampleFormat::Float => reader
                    .samples::<f32>()
                    .take(right - left)
                    .map(|v| v.map(|x| x as f64))
                    .collect::<Result<Vec<_>, _>>(),
                hound::SampleFormat::Int => {
                    let scale = 2f64.powi(self.layout.bits as i32 - 1);
                    reader
                        .samples::<i32>()
                        .take(right - left)
                        .map(|v| v.map(|x| x as f64 / scale))
                        .collect::<Result<Vec<_>, _>>()
                }
            }
            .map_err(|e| e.to_string())?;
            self.wav_stamp
                .check_handle(reader.into_inner().get_ref().get_ref())?;
        } else {
            // Hound 3.5.1 seek uses valid bits/8, not container bytes. Its sample
            // decoder supports 24-bit values stored in four bytes; use that same
            // decoder with a byte-correct seek for this uncommon representation.
            let mut stream = reader.into_inner();
            stream
                .seek(SeekFrom::Start(
                    self.layout.data_offset + left as u64 * self.layout.storage_bytes as u64,
                ))
                .map_err(|e| e.to_string())?;
            let scale = 2f64.powi(self.layout.bits as i32 - 1);
            samples = Vec::with_capacity(right - left);
            for _ in left..right {
                let value = <i32 as hound::Sample>::read(
                    &mut stream,
                    self.layout.format,
                    self.layout.storage_bytes,
                    self.layout.bits,
                )
                .map_err(|e| e.to_string())?;
                samples.push(value as f64 / scale);
            }
            self.wav_stamp.check_handle(stream.get_ref().get_ref())?;
        }
        self.wav_stamp.check_path(&self.wav_path)?;
        self.source_stamp.check_path(&self.source_path)?;
        if samples.len() != right - left
            || samples.iter().any(|v| !v.is_finite() || v.abs() > 1e100)
        {
            return Err("audio window has truncated, nonfinite or oversized samples".into());
        }
        Ok(samples)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn wav(path: &Path, samples: usize, bits: u16, storage_bytes: u16, float: bool) {
        let file = File::create(path).unwrap();
        let mut writer = hound::WavWriter::new_with_spec_ex(
            file,
            hound::WavSpecEx {
                spec: hound::WavSpec {
                    channels: 1,
                    sample_rate: 48000,
                    bits_per_sample: bits,
                    sample_format: if float {
                        hound::SampleFormat::Float
                    } else {
                        hound::SampleFormat::Int
                    },
                },
                bytes_per_sample: storage_bytes,
            },
        )
        .unwrap();
        for index in 0..samples {
            if float {
                let value = if index % 127 == 0 {
                    -0.0_f32
                } else {
                    ((index.wrapping_mul(719) % 1009) as f32 - 504.0) / 505.0
                };
                writer.write_sample(value).unwrap();
            } else {
                let range = (1_i64 << (bits - 1)) - 1;
                let value = (index as i64 * 719 % (2 * range + 1) - range) as i32;
                writer.write_sample(value).unwrap();
            }
        }
        writer.finalize().unwrap();
    }

    fn bitwise_equal(a: &[f64], b: &[f64]) {
        assert_eq!(a.len(), b.len());
        assert!(a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits()));
    }

    #[test]
    fn seeked_integer_and_float_windows_match_original_loader_exactly() {
        let tmp = tempfile::tempdir().unwrap();
        for (bits, storage, float) in [
            (8, 1, false),
            (16, 2, false),
            (24, 3, false),
            (24, 4, false),
            (32, 4, false),
            (32, 4, true),
        ] {
            let source = tmp.path().join(format!("{bits}-{storage}-{float}.wav"));
            wav(&source, 96000, bits, storage, float);
            let reference = input::load_audio(&source, tmp.path()).unwrap();
            let audio = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
            assert_eq!(audio.metadata.samples, reference.samples.len());
            assert_eq!(audio.metadata.sample_rate, reference.sample_rate);
            assert!(audio.metadata.conversion_command.is_none());
            for (left, right) in [(0, 8192), (1, 20000), (731, 60000), (81777, 96000)] {
                bitwise_equal(
                    &audio.get_window(left, right).unwrap(),
                    &reference.samples[left..right],
                );
            }
        }
        assert!(fs::read_dir(tmp.path()).unwrap().all(|entry| {
            let name = entry.unwrap().file_name();
            !name.to_string_lossy().starts_with("prepared-")
                && !name.to_string_lossy().ends_with("f64le")
        }));
    }

    #[test]
    fn native_same_content_relocation_preserves_frozen_metadata() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.wav");
        let relocated = tmp.path().join("relocated.wav");
        wav(&source, 48000, 16, 2, false);
        let initial = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
        fs::copy(&source, &relocated).unwrap();
        let resumed =
            PreparedAudio::open(&relocated, tmp.path(), tmp.path(), Some(&initial.metadata))
                .unwrap();
        assert_eq!(
            serde_json::to_vec(&resumed.metadata).unwrap(),
            serde_json::to_vec(&initial.metadata).unwrap()
        );
        bitwise_equal(
            &resumed.get_window(0, 48000).unwrap(),
            &initial.get_window(0, 48000).unwrap(),
        );
        let mut changed = initial.metadata.clone();
        changed.samples += 1;
        assert!(PreparedAudio::open(&source, tmp.path(), tmp.path(), Some(&changed)).is_err());
    }

    #[test]
    fn same_size_content_mutation_after_verification_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.wav");
        wav(&source, 48000, 16, 2, false);
        let audio = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
        let mut file = fs::OpenOptions::new().write(true).open(&source).unwrap();
        file.seek(SeekFrom::Start(audio.layout.data_offset + 1200))
            .unwrap();
        file.write_all(&[1, 2]).unwrap();
        file.sync_all().unwrap();
        assert!(audio.get_window(0, 48000).unwrap_err().contains("changed"));
        assert!(
            PreparedAudio::open(&source, tmp.path(), tmp.path(), Some(&audio.metadata)).is_err()
        );
    }

    #[test]
    fn changed_waveform_is_rejected_even_if_metadata_guards_see_no_difference() {
        let tmp = tempfile::tempdir().unwrap();
        for sample in [600usize, 32780, 47000] {
            let source = tmp.path().join(format!("mutation-{sample}.wav"));
            wav(&source, 48000, 16, 2, false);
            let mut audio = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
            let mut f = fs::OpenOptions::new().write(true).open(&source).unwrap();
            f.seek(SeekFrom::Start(
                audio.layout.data_offset + sample as u64 * 2,
            ))
            .unwrap();
            f.write_all(&[0x12, 0x34]).unwrap();
            f.sync_all().unwrap();
            // Deterministically emulate a timestamp collision, independently
            // of filesystem tick resolution and scheduling speed.
            let stamp = FileStamp::from_metadata(&fs::metadata(&source).unwrap()).unwrap();
            audio.source_stamp = stamp.clone();
            audio.wav_stamp = stamp;
            let error = audio
                .get_window(0, 48000)
                .err()
                .expect("mutated bytes must not reach DSP");
            assert!(error.contains("checksum"), "{error}");
        }
    }

    #[test]
    fn same_content_replacement_inode_during_invocation_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.wav");
        let replacement = tmp.path().join("replacement.wav");
        wav(&source, 48000, 16, 2, false);
        let audio = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
        fs::copy(&source, &replacement).unwrap();
        fs::rename(&replacement, &source).unwrap();
        assert!(audio.get_window(0, 48000).unwrap_err().contains("changed"));
        // A new invocation may explicitly verify and accept that same content.
        assert!(
            PreparedAudio::open(&source, tmp.path(), tmp.path(), Some(&audio.metadata)).is_ok()
        );
    }

    #[cfg(unix)]
    #[test]
    fn retargeting_the_supplied_source_symlink_is_rejected() {
        use std::os::unix::fs::symlink;
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.wav");
        let alias = tmp.path().join("alias.wav");
        let replacement = tmp.path().join("replacement.wav");
        wav(&source, 48000, 16, 2, false);
        fs::copy(&source, &replacement).unwrap();
        symlink(&source, &alias).unwrap();
        let audio = PreparedAudio::open(&alias, tmp.path(), tmp.path(), None).unwrap();
        fs::remove_file(&alias).unwrap();
        symlink(&replacement, &alias).unwrap();
        assert!(audio.get_window(0, 48000).is_err());
    }

    #[test]
    fn short_truncated_and_oversized_sources_are_rejected_without_full_loading() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("short.wav");
        wav(&source, 8191, 16, 2, false);
        assert!(PreparedAudio::open(&source, tmp.path(), tmp.path(), None).is_err());
        let source = tmp.path().join("truncated.wav");
        wav(&source, 48000, 16, 2, false);
        let file = fs::OpenOptions::new().write(true).open(&source).unwrap();
        file.set_len(file.metadata().unwrap().len() - 1).unwrap();
        assert!(PreparedAudio::open(&source, tmp.path(), tmp.path(), None).is_err());
        let source = tmp.path().join("oversized.wav");
        File::create(&source)
            .unwrap()
            .set_len(input::MAX_AUDIO_BYTES + 1)
            .unwrap();
        assert!(PreparedAudio::open(&source, tmp.path(), tmp.path(), None).is_err());
    }

    #[test]
    fn only_bounded_finite_windows_are_materialized() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.wav");
        wav(&source, 48000 * 7, 32, 4, true);
        let initial = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
        let data_offset = initial.layout.data_offset;
        drop(initial);
        let mut file = fs::OpenOptions::new().write(true).open(&source).unwrap();
        file.seek(SeekFrom::Start(data_offset + 30000 * 4)).unwrap();
        file.write_all(&f32::NAN.to_le_bytes()).unwrap();
        file.sync_all().unwrap();
        let audio = PreparedAudio::open(&source, tmp.path(), tmp.path(), None).unwrap();
        assert!(audio.get_window(0, 8192).is_ok());
        assert!(
            audio
                .get_window(24000, 48000)
                .unwrap_err()
                .contains("nonfinite")
        );
        for (left, right) in [
            (0, 0),
            (1, 0),
            (0, 8191),
            (0, 48000 * 7),
            (48000 * 7, 48000 * 7 + 8192),
        ] {
            assert!(audio.get_window(left, right).is_err());
        }
    }

    #[test]
    fn ogg_snapshot_authenticates_copied_bytes_when_source_stamps_collide() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.ogg");
        let original = (0..(2 * 65536 + 19))
            .map(|index| (index % 251) as u8)
            .collect::<Vec<_>>();
        fs::write(&source, &original).unwrap();
        let (identity, _) = verified(&source, input::MAX_AUDIO_BYTES).unwrap();
        let mut changed = original.clone();
        changed[65539] ^= 0x80;
        fs::write(&source, &changed).unwrap();
        // Emulate a metadata collision deterministically: stamp guards see the
        // current file, but content must still match the original frozen hash.
        let stamp = FileStamp::from_metadata(&fs::metadata(&source).unwrap()).unwrap();
        let error = OggSnapshot::capture(&identity, &source, &stamp, tmp.path())
            .err()
            .expect("changed source must not be blessed by matching metadata");
        assert!(error.contains("checksum"), "{error}");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);

        fs::write(&source, &original[..original.len() - 1]).unwrap();
        let stamp = FileStamp::from_metadata(&fs::metadata(&source).unwrap()).unwrap();
        let error = OggSnapshot::capture(&identity, &source, &stamp, tmp.path())
            .err()
            .expect("truncation must be rejected");
        assert!(error.contains("byte count"), "{error}");

        fs::write(&source, [&original[..], &[0x17]].concat()).unwrap();
        let stamp = FileStamp::from_metadata(&fs::metadata(&source).unwrap()).unwrap();
        let error = OggSnapshot::capture(&identity, &source, &stamp, tmp.path())
            .err()
            .expect("growth must be rejected without copying the extra byte");
        assert!(error.contains("byte count"), "{error}");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
    }

    #[test]
    fn ogg_snapshot_copy_is_private_bounded_and_independent_of_original() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("source.ogg");
        let original = (0..(2 * 65536 + 19))
            .map(|index| (index % 251) as u8)
            .collect::<Vec<_>>();
        fs::write(&source, &original).unwrap();
        let (identity, stamp) = verified(&source, input::MAX_AUDIO_BYTES).unwrap();
        let snapshot = OggSnapshot::capture(&identity, &source, &stamp, tmp.path()).unwrap();
        let snapshot_path = snapshot.path.clone();
        assert!(same_content(
            &identity,
            &input::identity(&snapshot.path).unwrap()
        ));
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            let copy_metadata = fs::metadata(&snapshot.path).unwrap();
            assert_ne!(copy_metadata.ino(), fs::metadata(&source).unwrap().ino());
            assert_eq!(copy_metadata.permissions().mode() & 0o777, 0o400);
            assert_eq!(
                fs::metadata(snapshot.directory.path())
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
        fs::write(&source, vec![0u8; original.len()]).unwrap();
        assert_eq!(fs::read(&snapshot.path).unwrap(), original);
        assert_ne!(input::identity(&source).unwrap().sha256, identity.sha256);
        drop(snapshot);
        assert!(!snapshot_path.exists());

        File::create(&source)
            .unwrap()
            .set_len(MAX_OGG_BYTES + 1)
            .unwrap();
        let stamp = FileStamp::from_metadata(&fs::metadata(&source).unwrap()).unwrap();
        let mut oversized = identity;
        oversized.bytes = MAX_OGG_BYTES + 1;
        let error = OggSnapshot::capture(&oversized, &source, &stamp, tmp.path())
            .err()
            .expect("oversized source must be rejected before copying");
        assert!(error.contains("64 MiB"), "{error}");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn ogg_probe_and_conversion_consume_verified_snapshot_after_original_mutates() {
        let tmp = tempfile::tempdir().unwrap();
        let source_wav = tmp.path().join("source.wav");
        let ogg = tmp.path().join("source.ogg");
        wav(&source_wav, 48000, 16, 2, false);
        let args = vec![
            "-nostdin".into(),
            "-v".into(),
            "error".into(),
            "-n".into(),
            "-i".into(),
            source_wav.display().to_string(),
            "-c:a".into(),
            "libvorbis".into(),
            ogg.display().to_string(),
        ];
        input::external_with_file_limit("ffmpeg", &args, 15, input::MAX_AUDIO_BYTES).unwrap();
        let reference = input::load_audio(&ogg, tmp.path()).unwrap();
        let (source_identity, source_stamp) = verified(&ogg, input::MAX_AUDIO_BYTES).unwrap();
        let snapshot =
            OggSnapshot::capture(&source_identity, &ogg, &source_stamp, tmp.path()).unwrap();
        let snapshot_path = snapshot.path.clone();
        assert!(same_content(
            &source_identity,
            &input::identity(&snapshot_path).unwrap()
        ));
        // Neither external tool can succeed on this original now. Conversion
        // still has to produce the precise samples of its verified snapshot.
        fs::write(&ogg, vec![0u8; source_identity.bytes as usize]).unwrap();
        let (converted, _, command) = convert_ogg_snapshot(snapshot, tmp.path()).unwrap();
        let input_index = command.iter().position(|arg| arg == "-i").unwrap() + 1;
        assert_eq!(command[input_index], snapshot_path.display().to_string());
        assert!(!command.contains(&source_identity.path));
        assert!(!snapshot_path.exists());
        let actual = input::load_audio(Path::new(&converted.path), tmp.path()).unwrap();
        bitwise_equal(&actual.samples, &reference.samples);
        assert_ne!(
            input::identity(&ogg).unwrap().sha256,
            source_identity.sha256
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn ogg_conversion_resume_and_corrupt_cache_checks_match_original_samples() {
        let tmp = tempfile::tempdir().unwrap();
        let source_wav = tmp.path().join("source.wav");
        let ogg = tmp.path().join("source.ogg");
        wav(&source_wav, 48000, 16, 2, false);
        let args = vec![
            "-nostdin".into(),
            "-v".into(),
            "error".into(),
            "-n".into(),
            "-i".into(),
            source_wav.display().to_string(),
            "-c:a".into(),
            "libvorbis".into(),
            ogg.display().to_string(),
        ];
        input::external_with_file_limit("ffmpeg", &args, 15, input::MAX_AUDIO_BYTES).unwrap();
        let reference = input::load_audio(&ogg, tmp.path()).unwrap();
        let audio = PreparedAudio::open(&ogg, tmp.path(), tmp.path(), None).unwrap();
        bitwise_equal(
            &audio.get_window(0, 48000).unwrap(),
            &reference.samples[..48000],
        );
        assert!(audio.metadata.conversion_command.is_some());
        let resumed =
            PreparedAudio::open(&ogg, tmp.path(), tmp.path(), Some(&audio.metadata)).unwrap();
        assert_eq!(
            serde_json::to_vec(&resumed.metadata).unwrap(),
            serde_json::to_vec(&audio.metadata).unwrap()
        );
        bitwise_equal(
            &resumed.get_window(319, 47001).unwrap(),
            &reference.samples[319..47001],
        );
        // Sessions created before snapshots recorded the original input path.
        // Resume preserves that historical command without running conversion.
        let mut historical = audio.metadata.clone();
        let command = historical.conversion_command.as_mut().unwrap();
        let input_index = command.iter().position(|arg| arg == "-i").unwrap() + 1;
        command[input_index] = historical.source.path.clone();
        let legacy_resumed =
            PreparedAudio::open(&ogg, tmp.path(), tmp.path(), Some(&historical)).unwrap();
        assert_eq!(
            serde_json::to_vec(&legacy_resumed.metadata).unwrap(),
            serde_json::to_vec(&historical).unwrap()
        );
        bitwise_equal(
            &legacy_resumed.get_window(319, 47001).unwrap(),
            &reference.samples[319..47001],
        );
        let cache = PathBuf::from(&audio.metadata.wav.path);
        assert!(
            cache
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("prepared-")
        );
        let mut forged = audio.metadata.clone();
        forged.wav.path = source_wav.display().to_string();
        assert!(PreparedAudio::open(&ogg, tmp.path(), tmp.path(), Some(&forged)).is_err());
        let mut file = fs::OpenOptions::new().write(true).open(&cache).unwrap();
        file.seek(SeekFrom::Start(audio.layout.data_offset + 10000))
            .unwrap();
        file.write_all(&[1, 2, 3, 4]).unwrap();
        file.sync_all().unwrap();
        assert!(resumed.get_window(0, 48000).is_err());
        assert!(PreparedAudio::open(&ogg, tmp.path(), tmp.path(), Some(&audio.metadata)).is_err());
    }
}
