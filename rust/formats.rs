//! Explicit, bounded receiver integration formats. Format parsing and catalogue
//! compatibility are not telemetry validation or modulation identification.
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256, Sha512};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

const MAX_METADATA_BYTES: u64 = 4 * 1024 * 1024;
const MAX_KISS_BYTES: usize = 64 * 1024 * 1024;
const MAX_KISS_RECORD_BYTES: usize = 1024 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScalarEncoding {
    I8,
    U8,
    I16Le,
    I16Be,
    F32Le,
    F32Be,
    F64Le,
    F64Be,
}
impl ScalarEncoding {
    pub fn scalar_bytes(self) -> u64 {
        match self {
            Self::I8 | Self::U8 => 1,
            Self::I16Le | Self::I16Be => 2,
            Self::F32Le | Self::F32Be => 4,
            Self::F64Le | Self::F64Be => 8,
        }
    }
    pub fn decode_scalar(self, bytes: &[u8]) -> Result<f64, String> {
        if bytes.len() != self.scalar_bytes() as usize {
            return Err("scalar byte length mismatch".into());
        }
        Ok(match self {
            Self::I8 => bytes[0] as i8 as f64,
            Self::U8 => bytes[0] as f64,
            Self::I16Le => i16::from_le_bytes(bytes.try_into().unwrap()) as f64,
            Self::I16Be => i16::from_be_bytes(bytes.try_into().unwrap()) as f64,
            Self::F32Le => f32::from_le_bytes(bytes.try_into().unwrap()) as f64,
            Self::F32Be => f32::from_be_bytes(bytes.try_into().unwrap()) as f64,
            Self::F64Le => f64::from_le_bytes(bytes.try_into().unwrap()),
            Self::F64Be => f64::from_be_bytes(bytes.try_into().unwrap()),
        })
    }
    fn integer_endpoints(self) -> Option<(f64, f64)> {
        match self {
            Self::I8 => Some((-128.0, 127.0)),
            Self::U8 => Some((0.0, 255.0)),
            Self::I16Le | Self::I16Be => Some((-32768.0, 32767.0)),
            _ => None,
        }
    }
    fn sigmf_name(self) -> &'static str {
        match self {
            Self::I8 => "ci8",
            Self::U8 => "cu8",
            Self::I16Le => "ci16_le",
            Self::I16Be => "ci16_be",
            Self::F32Le => "cf32_le",
            Self::F32Be => "cf32_be",
            Self::F64Le => "cf64_le",
            Self::F64Be => "cf64_be",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Interleaving {
    Iq,
    Qi,
    NativeComplex,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct IqCapture {
    pub sample_start: u64,
    pub global_index: Option<u64>,
    pub frequency_hz: Option<f64>,
    pub datetime: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ClippingMetrics {
    pub scalar_count: u64,
    pub lower_endpoint_count: u64,
    pub upper_endpoint_count: u64,
    pub endpoint_fraction: f64,
    pub nonfinite_scalar_count: u64,
    pub status: String,
    pub ab_window_sha256: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RawIqMetadata {
    pub metadata_path: Option<PathBuf>,
    pub data_path: PathBuf,
    pub source_datatype: String,
    pub datatype: String,
    pub scalar_encoding: ScalarEncoding,
    pub interleaving: Interleaving,
    pub q_sign: i8,
    pub scalar_zero: f64,
    pub iq_scale: f64,
    pub sample_rate_hz: f64,
    pub byte_offset: u64,
    pub sample_index_offset: u64,
    pub complex_sample_count: u64,
    pub size_bytes: u64,
    pub captures: Vec<IqCapture>,
    pub verified_sha256: String,
    pub verified_sha512: Option<String>,
    pub clipping: Option<ClippingMetrics>,
    pub source_contract: Value,
}
fn read_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let mut bytes = vec![];
    crate::input::open_regular(path)?
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > limit {
        return Err(format!("{} exceeds {limit} bytes", path.display()));
    }
    Ok(bytes)
}
fn object<'a>(value: &'a Value, path: &str) -> Result<&'a Map<String, Value>, String> {
    value
        .as_object()
        .ok_or_else(|| format!("{path} must be an object"))
}
fn text<'a>(value: &'a Value, path: &str) -> Result<&'a str, String> {
    value
        .as_str()
        .filter(|v| !v.trim().is_empty())
        .ok_or_else(|| format!("{path} must be a non-empty string"))
}
fn number(value: &Value, path: &str, positive: bool) -> Result<f64, String> {
    value
        .as_f64()
        .filter(|v| v.is_finite() && (!positive || *v > 0.0))
        .ok_or_else(|| {
            format!(
                "{path} must be finite{}",
                if positive { " and positive" } else { "" }
            )
        })
}
fn integer(value: &Value, path: &str, positive: bool) -> Result<u64, String> {
    value
        .as_u64()
        .filter(|v| !positive || *v > 0)
        .ok_or_else(|| {
            format!(
                "{path} must be {}integer",
                if positive {
                    "a positive "
                } else {
                    "a non-negative "
                }
            )
        })
}
fn keys(value: &Value, allowed: &[&str], path: &str) -> Result<(), String> {
    for key in object(value, path)?.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(format!("unexpected {path} field: {key}"));
        }
    }
    Ok(())
}
fn hash_text(value: &Value, path: &str, length: usize, lowercase: bool) -> Result<String, String> {
    let value = text(value, path)?;
    if value.len() != length
        || !value
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && (!lowercase || !b.is_ascii_uppercase()))
    {
        return Err(format!("{path} must be {length} hexadecimal characters"));
    }
    Ok(value.to_ascii_lowercase())
}
fn timestamp(value: &Value, path: &str) -> Result<String, String> {
    let value = text(value, path)?;
    chrono::DateTime::parse_from_rfc3339(value)
        .map_err(|_| format!("{path} must be an RFC3339 timestamp with timezone"))?;
    Ok(value.into())
}
fn hashes(path: &Path) -> Result<(u64, String, String), String> {
    let mut file = crate::input::open_regular(path)?;
    let before = file.metadata().map_err(|e| e.to_string())?;
    if !before.is_file() {
        return Err("IQ source must be a regular file".into());
    }
    let (mut sha256, mut sha512) = (Sha256::new(), Sha512::new());
    let mut buffer = vec![0; 1024 * 1024];
    let mut count = 0;
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        count += n as u64;
        sha256.update(&buffer[..n]);
        sha512.update(&buffer[..n]);
    }
    let after = file.metadata().map_err(|e| e.to_string())?;
    if count != before.len()
        || before.len() != after.len()
        || before.modified().ok() != after.modified().ok()
    {
        return Err("IQ source changed during hashing".into());
    }
    Ok((
        count,
        hex::encode(sha256.finalize()),
        hex::encode(sha512.finalize()),
    ))
}
fn range_sha256(path: &Path, offset: u64, count: u64) -> Result<String, String> {
    let mut file = crate::input::open_regular(path)?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|e| e.to_string())?;
    let mut remaining = count;
    let mut digest = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    while remaining > 0 {
        let need = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..need])
            .map_err(|e| format!("short IQ range: {e}"))?;
        digest.update(&buffer[..need]);
        remaining -= need as u64;
    }
    Ok(hex::encode(digest.finalize()))
}
fn expected_size(offset: u64, count: u64, encoding: ScalarEncoding) -> Result<u64, String> {
    count
        .checked_mul(encoding.scalar_bytes() * 2)
        .and_then(|n| offset.checked_add(n))
        .ok_or("IQ shape byte count overflow".into())
}

/// SigMF complex scalar widths are explicit. Complex128 means two f64 values;
/// no reinterpretation based on filename, baud, apparent duration, or catalogue.
pub fn parse_sigmf(metadata_path: &Path) -> Result<RawIqMetadata, String> {
    let metadata_path = metadata_path.canonicalize().map_err(|e| e.to_string())?;
    let document: Value =
        serde_json::from_slice(&read_bounded(&metadata_path, MAX_METADATA_BYTES)?)
            .map_err(|e| e.to_string())?;
    let global = &document["global"];
    object(global, "global")?;
    let version = text(&global["core:version"], "global.core:version")?;
    if !version.starts_with("1.") {
        return Err("unsupported SigMF major version".into());
    }
    if global["core:metadata_only"] == true {
        return Err("metadata-only SigMF has no decode-ready dataset".into());
    }
    if let Some(extensions) = global.get("core:extensions") {
        for extension in extensions
            .as_array()
            .ok_or("core:extensions must be an array")?
        {
            text(&extension["name"], "extension.name")?;
            text(&extension["version"], "extension.version")?;
            match extension["optional"].as_bool() {
                Some(true) => {}
                Some(false) => {
                    return Err(
                        "required SigMF extension is not implemented by this core reader".into(),
                    );
                }
                None => return Err("extension.optional must be explicit boolean".into()),
            }
        }
    }
    let datatype = text(&global["core:datatype"], "global.core:datatype")?;
    let encoding = match datatype {
        "ci8" => ScalarEncoding::I8,
        "cu8" => ScalarEncoding::U8,
        "ci16_le" => ScalarEncoding::I16Le,
        "ci16_be" => ScalarEncoding::I16Be,
        "cf32_le" => ScalarEncoding::F32Le,
        "cf32_be" => ScalarEncoding::F32Be,
        "cf64_le" => ScalarEncoding::F64Le,
        "cf64_be" => ScalarEncoding::F64Be,
        _ => return Err(format!("unsupported explicit SigMF datatype: {datatype}")),
    };
    let sample_rate = number(&global["core:sample_rate"], "global.core:sample_rate", true)?;
    let expected_sha512 = hash_text(&global["core:sha512"], "global.core:sha512", 128, false)?;
    if let Some(channels) = global.get("core:num_channels")
        && integer(channels, "core:num_channels", true)? != 1
    {
        return Err("multichannel SigMF is not supported by this receiver".into());
    }
    // core:offset is an absolute sample-index origin, never a byte offset.
    let sample_index_offset = match global.get("core:offset") {
        Some(v) => integer(v, "core:offset", false)?,
        None => 0,
    };
    let offset = 0;
    for field in ["core:trailing_bytes", "core:header_bytes"] {
        if global.get(field).is_some_and(|v| v != 0) {
            return Err(format!(
                "nonzero {field} requires a non-contiguous SigMF reader"
            ));
        }
    }
    let data_path = if let Some(dataset) = global.get("core:dataset") {
        let name = text(dataset, "core:dataset")?;
        if name.contains("://") {
            return Err("SigMF dataset must be a local path".into());
        }
        let path = Path::new(name);
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            metadata_path.parent().unwrap().join(path)
        }
    } else {
        metadata_path.with_extension("sigmf-data")
    }
    .canonicalize()
    .map_err(|e| e.to_string())?;
    let (size, sha256, sha512) = hashes(&data_path)?;
    if sha512 != expected_sha512 {
        return Err("SigMF data SHA-512 does not match metadata".into());
    }
    let bytes = size
        .checked_sub(offset)
        .ok_or("SigMF offset is beyond the data file")?;
    let width = encoding.scalar_bytes() * 2;
    if bytes == 0 || bytes % width != 0 {
        return Err(
            "SigMF data must contain a non-empty integral number of complex samples".into(),
        );
    }
    let count = bytes / width;
    let raw_captures = document["captures"]
        .as_array()
        .ok_or("SigMF captures must be an array")?;
    let mut captures = vec![];
    let mut previous = None;
    for raw in raw_captures {
        object(raw, "capture")?;
        let absolute_start = integer(
            &raw["core:sample_start"],
            "capture.core:sample_start",
            false,
        )?;
        let start = absolute_start
            .checked_sub(sample_index_offset)
            .ok_or("capture starts before core:offset sample-index origin")?;
        if start >= count || previous.is_some_and(|last| start <= last) {
            return Err("SigMF capture starts must increase and lie inside the dataset".into());
        }
        if let Some(v) = raw.get("core:header_bytes")
            && integer(v, "core:header_bytes", false)? != 0
        {
            return Err(
                "nonzero capture core:header_bytes requires a non-contiguous reader".into(),
            );
        }
        let global_index = raw
            .get("core:global_index")
            .map(|v| integer(v, "capture.core:global_index", false))
            .transpose()?;
        let frequency = raw
            .get("core:frequency")
            .map(|v| number(v, "capture.core:frequency", false))
            .transpose()?;
        let datetime = raw
            .get("core:datetime")
            .map(|v| timestamp(v, "capture.core:datetime"))
            .transpose()?;
        if datetime.as_ref().is_some_and(|v| !v.ends_with('Z')) {
            return Err("SigMF capture datetime must use UTC Z suffix".into());
        }
        captures.push(IqCapture {
            sample_start: start,
            global_index,
            frequency_hz: frequency,
            datetime,
        });
        previous = Some(start);
    }
    // SigMF explicitly defines [] as one segment at the start of the Dataset.
    if captures.is_empty() {
        captures.push(IqCapture {
            sample_start: 0,
            global_index: None,
            frequency_hz: None,
            datetime: None,
        });
    }
    if let Some(annotations) = document.get("annotations") {
        for annotation in annotations
            .as_array()
            .ok_or("SigMF annotations must be an array")?
        {
            let start = integer(
                &annotation["core:sample_start"],
                "annotation.core:sample_start",
                false,
            )?
            .checked_sub(sample_index_offset)
            .ok_or("annotation starts before sample-index origin")?;
            let length = annotation
                .get("core:sample_count")
                .map(|v| integer(v, "annotation.core:sample_count", false))
                .transpose()?
                .unwrap_or(0);
            if start > count || start.checked_add(length).is_none_or(|end| end > count) {
                return Err("SigMF annotation extends outside the data".into());
            }
        }
    }
    // Normalization is a format convention only, independent of observed data.
    let (zero, scale) = match encoding {
        ScalarEncoding::I8 => (0.0, 128.0),
        ScalarEncoding::U8 => (128.0, 128.0),
        ScalarEncoding::I16Le | ScalarEncoding::I16Be => (0.0, 32768.0),
        _ => (0.0, 1.0),
    };
    Ok(RawIqMetadata {
        metadata_path: Some(metadata_path),
        data_path,
        source_datatype: datatype.into(),
        datatype: datatype.into(),
        scalar_encoding: encoding,
        interleaving: Interleaving::Iq,
        q_sign: 1,
        scalar_zero: zero,
        iq_scale: scale,
        sample_rate_hz: sample_rate,
        byte_offset: offset,
        sample_index_offset,
        complex_sample_count: count,
        size_bytes: size,
        captures,
        verified_sha256: sha256,
        verified_sha512: Some(sha512),
        clipping: None,
        source_contract: document,
    })
}

/// Decode-readiness validation of raw-iq-input-v1. Blind preregistration is a
/// separate scientific record; this function never asserts blind-test readiness.
pub fn validate_raw_manifest(document: &Value, base_dir: &Path) -> Result<RawIqMetadata, String> {
    keys(
        document,
        &[
            "schema_version",
            "source",
            "encoding",
            "capture",
            "doppler",
            "satellite",
            "signal",
            "clipping",
        ],
        "manifest",
    )?;
    if document["schema_version"] != "raw-iq-input-v1" {
        return Err("unsupported_schema_version".into());
    }
    let source = &document["source"];
    keys(source, &["path", "sha256", "size_bytes"], "source")?;
    let source_path = Path::new(text(&source["path"], "source.path")?);
    let data_path = if source_path.is_absolute() {
        source_path.to_path_buf()
    } else {
        base_dir.join(source_path)
    }
    .canonicalize()
    .map_err(|e| e.to_string())?;
    let expected_sha = hash_text(&source["sha256"], "source.sha256", 64, true)?;
    let declared_size = integer(&source["size_bytes"], "source.size_bytes", true)?;
    let raw = &document["encoding"];
    keys(
        raw,
        &[
            "dtype",
            "interleaving",
            "q_sign",
            "scalar_zero",
            "iq_scale",
            "byte_offset",
            "complex_sample_count",
            "evidence",
        ],
        "encoding",
    )?;
    let dtype = text(&raw["dtype"], "encoding.dtype")?;
    let (encoding, native) = match dtype {
        "int8" => (ScalarEncoding::I8, false),
        "uint8" => (ScalarEncoding::U8, false),
        "int16_le" => (ScalarEncoding::I16Le, false),
        "int16_be" => (ScalarEncoding::I16Be, false),
        "float32_le" => (ScalarEncoding::F32Le, false),
        "float32_be" => (ScalarEncoding::F32Be, false),
        "complex64_le" => (ScalarEncoding::F32Le, true),
        "complex64_be" => (ScalarEncoding::F32Be, true),
        _ => return Err("missing_or_unsupported_dtype".into()),
    };
    let interleaving = match text(&raw["interleaving"], "encoding.interleaving")? {
        "iq" => Interleaving::Iq,
        "qi" => Interleaving::Qi,
        "native_complex" => Interleaving::NativeComplex,
        _ => return Err("invalid interleaving".into()),
    };
    if native != (interleaving == Interleaving::NativeComplex) {
        return Err("dtype_interleaving_mismatch".into());
    }
    let q_sign = raw["q_sign"]
        .as_i64()
        .filter(|v| [-1, 1].contains(v))
        .ok_or("q_sign must be explicit integer -1 or 1")? as i8;
    let zero = number(&raw["scalar_zero"], "encoding.scalar_zero", false)?;
    let scale = number(&raw["iq_scale"], "encoding.iq_scale", true)?;
    let offset = integer(&raw["byte_offset"], "encoding.byte_offset", false)?;
    let count = integer(
        &raw["complex_sample_count"],
        "encoding.complex_sample_count",
        true,
    )?;
    text(&raw["evidence"], "encoding.evidence")?;
    let capture = &document["capture"];
    keys(
        capture,
        &[
            "sample_rate_hz",
            "center_frequency_hz",
            "start_utc",
            "clock_reference",
            "evidence",
        ],
        "capture",
    )?;
    let sample_rate = number(&capture["sample_rate_hz"], "capture.sample_rate_hz", true)?;
    let frequency = number(
        &capture["center_frequency_hz"],
        "capture.center_frequency_hz",
        true,
    )?;
    let start = timestamp(&capture["start_utc"], "capture.start_utc")?;
    text(&capture["clock_reference"], "capture.clock_reference")?;
    text(&capture["evidence"], "capture.evidence")?;
    let doppler = &document["doppler"];
    keys(doppler, &["state", "evidence"], "doppler")?;
    let state = text(&doppler["state"], "doppler.state")?;
    if state != "pre_correction" && state != "post_correction" {
        return Err("unknown_or_invalid_doppler_state".into());
    }
    text(&doppler["evidence"], "doppler.evidence")?;
    let satellite = &document["satellite"];
    keys(satellite, &["norad_id", "evidence"], "satellite")?;
    integer(&satellite["norad_id"], "satellite.norad_id", true)?;
    text(&satellite["evidence"], "satellite.evidence")?;
    let signal = &document["signal"];
    keys(signal, &["modulation", "baud", "evidence"], "signal")?;
    if text(&signal["modulation"], "signal.modulation")?
        .trim()
        .eq_ignore_ascii_case("unknown")
    {
        return Err("missing_or_unknown_modulation".into());
    }
    number(&signal["baud"], "signal.baud", true)?;
    text(&signal["evidence"], "signal.evidence")?;
    let clipping = &document["clipping"];
    keys(
        clipping,
        &[
            "lower_endpoint",
            "upper_endpoint",
            "endpoint_source",
            "max_endpoint_fraction_for_unclipped_claim",
            "if_exceeded",
            "ab",
        ],
        "clipping",
    )?;
    let lower = number(
        &clipping["lower_endpoint"],
        "clipping.lower_endpoint",
        false,
    )?;
    let upper = number(
        &clipping["upper_endpoint"],
        "clipping.upper_endpoint",
        false,
    )?;
    if lower >= upper {
        return Err("invalid_clipping_endpoint_order".into());
    }
    let endpoint_source = text(&clipping["endpoint_source"], "clipping.endpoint_source")?;
    let limit = number(
        &clipping["max_endpoint_fraction_for_unclipped_claim"],
        "clipping.max_endpoint_fraction_for_unclipped_claim",
        false,
    )?;
    if !(0.0..=1.0).contains(&limit) {
        return Err("clipping fraction limit must be in 0..1".into());
    }
    let action = text(&clipping["if_exceeded"], "clipping.if_exceeded")?;
    if action != "require_frozen_ab" && action != "block_decode" {
        return Err("invalid clipping action".into());
    }
    if let Some((expected_lower, expected_upper)) = encoding.integer_endpoints()
        && (lower != expected_lower || upper != expected_upper || endpoint_source != "dtype_limits")
    {
        return Err("integer_clipping_endpoints_must_match_dtype_limits".into());
    }
    let ab = &clipping["ab"];
    object(ab, "clipping.ab")?;
    let enabled = ab["enabled"]
        .as_bool()
        .ok_or("ab.enabled must be explicit boolean")?;
    if enabled {
        keys(
            ab,
            &[
                "enabled",
                "input_sha256_a",
                "input_sha256_b",
                "window_start_complex_sample",
                "window_complex_samples",
                "window_sha256_a",
                "window_sha256_b",
                "branch_a",
                "branch_b",
                "branch_b_config_sha256",
                "shared_downstream_config_sha256",
                "acceptance_rule",
            ],
            "clipping.ab",
        )?;
        if ab["input_sha256_a"] != expected_sha || ab["input_sha256_b"] != expected_sha {
            return Err("ab_inputs_not_identical_to_source".into());
        }
        if ab["branch_a"] != "identity" {
            return Err("ab_branch_a_must_be_identity".into());
        }
        if text(&ab["branch_b"], "ab.branch_b")? == "identity" {
            return Err("missing_or_invalid_ab_branch_b".into());
        }
        for name in [
            "window_sha256_a",
            "window_sha256_b",
            "branch_b_config_sha256",
            "shared_downstream_config_sha256",
        ] {
            hash_text(&ab[name], name, 64, true)?;
        }
        text(&ab["acceptance_rule"], "ab.acceptance_rule")?;
        let window_start = integer(
            &ab["window_start_complex_sample"],
            "ab.window_start_complex_sample",
            false,
        )?;
        let window_count = integer(
            &ab["window_complex_samples"],
            "ab.window_complex_samples",
            true,
        )?;
        if window_start
            .checked_add(window_count)
            .is_none_or(|end| end > count)
        {
            return Err("ab_window_outside_source".into());
        }
    } else {
        keys(ab, &["enabled"], "clipping.ab")?;
    }
    let expected = expected_size(offset, count, encoding)?;
    let (size, sha256, _) = hashes(&data_path)?;
    if size != declared_size {
        return Err("source_size_mismatch".into());
    }
    if declared_size != expected {
        return Err("source_shape_size_mismatch".into());
    }
    if sha256 != expected_sha {
        return Err("source_sha256_mismatch".into());
    }
    let mut file = crate::input::open_regular(&data_path)?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|e| e.to_string())?;
    let width = encoding.scalar_bytes() as usize;
    let mut remaining = expected - offset;
    let mut buffer = vec![0; 1024 * 1024];
    let (mut scalar_count, mut lower_count, mut upper_count, mut nonfinite) = (0, 0, 0, 0);
    while remaining > 0 {
        let n = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..n])
            .map_err(|e| e.to_string())?;
        for raw in buffer[..n].chunks_exact(width) {
            let value = encoding.decode_scalar(raw)?;
            scalar_count += 1;
            if !value.is_finite() {
                nonfinite += 1;
            } else {
                lower_count += u64::from(value == lower);
                upper_count += u64::from(value == upper);
            }
        }
        remaining -= n as u64;
    }
    if nonfinite > 0 {
        return Err("nonfinite_iq_scalars".into());
    }
    let fraction = (lower_count + upper_count) as f64 / scalar_count as f64;
    let status = if fraction > limit {
        "exceeds_declared_limit"
    } else {
        "within_declared_limit"
    };
    if fraction > limit {
        if action == "block_decode" {
            return Err("clipping_limit_exceeded".into());
        }
        if !enabled {
            return Err("clipping_requires_enabled_ab".into());
        }
    }
    let ab_window_sha = if enabled {
        let start = integer(&ab["window_start_complex_sample"], "ab.start", false)?;
        let length = integer(&ab["window_complex_samples"], "ab.length", true)?;
        let digest = range_sha256(
            &data_path,
            expected_size(offset, start, encoding)?,
            expected_size(0, length, encoding)?,
        )?;
        if ab["window_sha256_a"] != digest || ab["window_sha256_b"] != digest {
            return Err("ab_window_hash_mismatch".into());
        }
        Some(digest)
    } else {
        None
    };
    Ok(RawIqMetadata {
        metadata_path: None,
        data_path,
        source_datatype: dtype.into(),
        datatype: encoding.sigmf_name().into(),
        scalar_encoding: encoding,
        interleaving,
        q_sign,
        scalar_zero: zero,
        iq_scale: scale,
        sample_rate_hz: sample_rate,
        byte_offset: offset,
        sample_index_offset: 0,
        complex_sample_count: count,
        size_bytes: size,
        captures: vec![IqCapture {
            sample_start: 0,
            global_index: None,
            frequency_hz: Some(frequency),
            datetime: Some(start),
        }],
        verified_sha256: sha256,
        verified_sha512: None,
        clipping: Some(ClippingMetrics {
            scalar_count,
            lower_endpoint_count: lower_count,
            upper_endpoint_count: upper_count,
            endpoint_fraction: fraction,
            nonfinite_scalar_count: nonfinite,
            status: status.into(),
            ab_window_sha256: ab_window_sha,
        }),
        source_contract: document.clone(),
    })
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct KissTimestamp {
    pub timestamp_ms: u64,
    pub port: u8,
    pub record_index: usize,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct KissDataFrame {
    pub payload: Vec<u8>,
    pub port: u8,
    pub record_index: usize,
    pub timestamp_ms: Option<u64>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct KissOtherCommand {
    pub command: u8,
    pub port: u8,
    pub payload: Vec<u8>,
    pub record_index: usize,
}
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct KissResult {
    pub data_frames: Vec<KissDataFrame>,
    pub timestamp_commands: Vec<KissTimestamp>,
    pub other_commands: Vec<KissOtherCommand>,
    pub malformed_records: usize,
}
pub fn parse_kiss(data: &[u8]) -> Result<KissResult, String> {
    if data.len() > MAX_KISS_BYTES {
        return Err("KISS stream exceeds 64 MiB parser limit".into());
    }
    let mut result = KissResult::default();
    let mut record = vec![];
    let (mut escaped, mut invalid) = (false, false);
    let mut index = 0;
    let mut timestamp = None;
    for value in data {
        if *value == 0xc0 {
            if escaped {
                invalid = true;
                escaped = false;
            }
            if !record.is_empty() || invalid {
                let current = index;
                index += 1;
                if invalid || record.is_empty() {
                    result.malformed_records += 1;
                } else {
                    let control = record[0];
                    let port = control >> 4;
                    let command = control & 15;
                    let payload = record[1..].to_vec();
                    match command {
                        0 if !payload.is_empty() => result.data_frames.push(KissDataFrame {
                            payload,
                            port,
                            record_index: current,
                            timestamp_ms: timestamp,
                        }),
                        9 if payload.len() == 8 => {
                            let milliseconds =
                                u64::from_be_bytes(payload.as_slice().try_into().unwrap());
                            timestamp = Some(milliseconds);
                            result.timestamp_commands.push(KissTimestamp {
                                timestamp_ms: milliseconds,
                                port,
                                record_index: current,
                            });
                        }
                        0 | 9 => result.malformed_records += 1,
                        _ => result.other_commands.push(KissOtherCommand {
                            command,
                            port,
                            payload,
                            record_index: current,
                        }),
                    }
                }
            }
            record.clear();
            invalid = false;
        } else if invalid {
            continue;
        } else if escaped {
            match *value {
                0xdc => record.push(0xc0),
                0xdd => record.push(0xdb),
                _ => invalid = true,
            }
            escaped = false;
        } else if *value == 0xdb {
            escaped = true;
        } else {
            record.push(*value);
        }
        if record.len() > MAX_KISS_RECORD_BYTES {
            return Err("KISS record exceeds 1 MiB parser limit".into());
        }
    }
    if escaped || invalid || !record.is_empty() {
        result.malformed_records += 1;
    }
    Ok(result)
}
pub fn encode_kiss_record(port: u8, command: u8, payload: &[u8]) -> Result<Vec<u8>, String> {
    if port > 15 || command > 15 {
        return Err("KISS port and command must be 0..15".into());
    }
    if payload.len() >= MAX_KISS_RECORD_BYTES {
        return Err("KISS record exceeds encoder bound".into());
    }
    if command == 0 && payload.is_empty() {
        return Err("KISS data must be non-empty".into());
    }
    if command == 9 && payload.len() != 8 {
        return Err("KISS timestamp requires 8 bytes".into());
    }
    let mut result = vec![0xc0];
    for value in std::iter::once((port << 4) | command).chain(payload.iter().copied()) {
        match value {
            0xc0 => result.extend([0xdb, 0xdc]),
            0xdb => result.extend([0xdb, 0xdd]),
            _ => result.push(value),
        }
    }
    result.push(0xc0);
    Ok(result)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SatYamlTransmitter {
    pub transmitter_id: String,
    pub modulation: String,
    pub framing: String,
    pub fec: Vec<String>,
    pub metadata: Map<String, Value>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SatYamlProfile {
    pub selector: String,
    pub selector_kind: String,
    pub name: String,
    pub norad_id: u64,
    pub transmitters: Vec<SatYamlTransmitter>,
    pub modulations: Vec<String>,
    pub framing: Vec<String>,
    pub fec: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SatYamlDiagnostic {
    pub path: String,
    pub code: String,
    pub message: String,
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SatYamlRegistry {
    pub profiles: Vec<SatYamlProfile>,
    pub name_index: BTreeMap<String, String>,
    pub norad_index: BTreeMap<u64, String>,
    pub diagnostics: Vec<SatYamlDiagnostic>,
    pub conflicted_names: Vec<String>,
    pub conflicted_norads: Vec<u64>,
}
impl SatYamlRegistry {
    pub fn get_by_name(&self, name: &str) -> Option<&SatYamlProfile> {
        let selector = self
            .name_index
            .get(&caseless::default_case_fold_str(name))?;
        self.profiles.iter().find(|p| &p.selector == selector)
    }
    pub fn get_by_norad(&self, norad: u64) -> Option<&SatYamlProfile> {
        let selector = self.norad_index.get(&norad)?;
        self.profiles.iter().find(|p| &p.selector == selector)
    }
}
fn sorted_unique<I: IntoIterator<Item = String>>(items: I) -> Vec<String> {
    items
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}
pub fn parse_satyaml_document(document: &Value, selector: &str) -> Result<SatYamlProfile, String> {
    object(document, "SatYAML root")?;
    let name = text(&document["name"], "$.name")?.to_string();
    let norad = integer(&document["norad"], "$.norad", true)?;
    if selector.trim().is_empty() {
        return Err("SatYAML selector must be non-empty".into());
    }
    let tx = object(&document["transmitters"], "$.transmitters")?;
    if tx.is_empty() {
        return Err("$.transmitters must be non-empty".into());
    }
    let mut records: Vec<_> = tx.iter().collect();
    records.sort_by_key(|(id, _)| caseless::default_case_fold_str(id));
    let mut transmitters = vec![];
    for (id, raw) in records {
        if id.trim().is_empty() {
            return Err("transmitter IDs must be non-empty strings".into());
        }
        let location = format!("$.transmitters.{id}");
        let value = object(raw, &location)?;
        let modulation = text(&raw["modulation"], &format!("{location}.modulation"))?.to_string();
        let framing = text(&raw["framing"], &format!("{location}.framing"))?.to_string();
        let fec = match raw.get("fec") {
            None | Some(Value::Null) => vec![],
            Some(Value::String(value)) if !value.trim().is_empty() => vec![value.clone()],
            Some(Value::Array(values)) if !values.is_empty() => {
                let mut seen = BTreeSet::new();
                let mut output = vec![];
                for item in values {
                    let item = text(item, &format!("{location}.fec"))?.to_string();
                    if !seen.insert(item.clone()) {
                        return Err(format!("{location}.fec values must be unique"));
                    }
                    output.push(item);
                }
                output
            }
            _ => {
                return Err(format!(
                    "{location}.fec must be a non-empty string or list of unique non-empty strings"
                ));
            }
        };
        let metadata = value
            .iter()
            .filter(|(key, _)| !["modulation", "framing", "fec"].contains(&key.as_str()))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        transmitters.push(SatYamlTransmitter {
            transmitter_id: id.clone(),
            modulation,
            framing,
            fec,
            metadata,
        });
    }
    Ok(SatYamlProfile {
        selector: selector.into(),
        selector_kind: "satyaml".into(),
        name,
        norad_id: norad,
        modulations: sorted_unique(transmitters.iter().map(|t| t.modulation.clone())),
        framing: sorted_unique(transmitters.iter().map(|t| t.framing.clone())),
        fec: sorted_unique(transmitters.iter().flat_map(|t| t.fec.iter().cloned())),
        transmitters,
    })
}
fn safe_yaml_document(input: &str) -> Result<Value, String> {
    let mut options = serde_saphyr::Options::default();
    options.reject_unsupported_tags = true;
    serde_saphyr::from_str_with_options(input, options)
        .map_err(|e| format!("safe YAML parse failed: {e}"))
}

/// YAML parsing uses a bounded data-only Serde implementation, no Python tags,
/// object construction, executable imports, or file-inclusion feature.
pub fn parse_satyaml(path: &Path) -> Result<SatYamlProfile, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if metadata.file_type().is_symlink() {
        return Err("symbolic links are not loaded".into());
    }
    let path = path.canonicalize().map_err(|e| e.to_string())?;
    let bytes = read_bounded(&path, 1024 * 1024)?;
    let input = std::str::from_utf8(&bytes).map_err(|e| e.to_string())?;
    let document = safe_yaml_document(input)?;
    parse_satyaml_document(&document, &path.to_string_lossy())
}
pub fn build_satyaml_registry(
    roots: &[PathBuf],
    maximum_file_bytes: u64,
) -> Result<SatYamlRegistry, String> {
    if maximum_file_bytes == 0 || maximum_file_bytes > MAX_METADATA_BYTES {
        return Err("SatYAML file bound must be 1..4 MiB".into());
    }
    let mut registry = SatYamlRegistry::default();
    let mut paths = BTreeSet::new();
    let mut pending: Vec<(PathBuf, bool)> = roots.iter().cloned().map(|p| (p, true)).collect();
    let mut visited = 0;
    while let Some((path, is_root)) = pending.pop() {
        visited += 1;
        if visited > 100_000 {
            return Err("SatYAML discovery exceeds 100000 entries".into());
        }
        let metadata = match fs::symlink_metadata(&path) {
            Ok(v) => v,
            Err(_) => {
                registry.diagnostics.push(SatYamlDiagnostic {
                    path: path.display().to_string(),
                    code: "path_error".into(),
                    message: "path does not exist or cannot be inspected".into(),
                });
                continue;
            }
        };
        if metadata.file_type().is_symlink() {
            registry.diagnostics.push(SatYamlDiagnostic {
                path: path.display().to_string(),
                code: "path_error".into(),
                message: "symbolic links are not loaded".into(),
            });
            continue;
        }
        if metadata.is_dir() {
            match fs::read_dir(&path) {
                Ok(entries) => {
                    for entry in entries {
                        match entry {
                            Ok(entry) => pending.push((entry.path(), false)),
                            Err(e) => registry.diagnostics.push(SatYamlDiagnostic {
                                path: path.display().to_string(),
                                code: "read_error".into(),
                                message: e.to_string(),
                            }),
                        }
                    }
                }
                Err(e) => registry.diagnostics.push(SatYamlDiagnostic {
                    path: path.display().to_string(),
                    code: "read_error".into(),
                    message: e.to_string(),
                }),
            }
            continue;
        }
        let extension = path.extension().and_then(|s| s.to_str()).unwrap_or("");
        if !metadata.is_file()
            || if is_root {
                !extension.eq_ignore_ascii_case("yml")
            } else {
                extension != "yml"
            }
        {
            if is_root {
                registry.diagnostics.push(SatYamlDiagnostic {
                    path: path.display().to_string(),
                    code: "path_error".into(),
                    message: "expected a .yml file or directory".into(),
                });
            }
            continue;
        }
        match path.canonicalize() {
            Ok(path) => {
                paths.insert(path);
            }
            Err(e) => registry.diagnostics.push(SatYamlDiagnostic {
                path: path.display().to_string(),
                code: "read_error".into(),
                message: e.to_string(),
            }),
        }
    }
    for path in paths {
        let bytes = match read_bounded(&path, maximum_file_bytes) {
            Ok(bytes) => bytes,
            Err(e) => {
                registry.diagnostics.push(SatYamlDiagnostic {
                    path: path.display().to_string(),
                    code: if e.contains("exceeds") {
                        "size_error"
                    } else {
                        "read_error"
                    }
                    .into(),
                    message: e,
                });
                continue;
            }
        };
        let input = match std::str::from_utf8(&bytes) {
            Ok(v) => v,
            Err(e) => {
                registry.diagnostics.push(SatYamlDiagnostic {
                    path: path.display().to_string(),
                    code: "read_error".into(),
                    message: e.to_string(),
                });
                continue;
            }
        };
        let document = match safe_yaml_document(input) {
            Ok(v) => v,
            Err(e) => {
                registry.diagnostics.push(SatYamlDiagnostic {
                    path: path.display().to_string(),
                    code: "yaml_error".into(),
                    message: e.to_string(),
                });
                continue;
            }
        };
        match parse_satyaml_document(&document, &path.to_string_lossy()) {
            Ok(profile) => registry.profiles.push(profile),
            Err(e) => registry.diagnostics.push(SatYamlDiagnostic {
                path: path.display().to_string(),
                code: "schema_error".into(),
                message: e,
            }),
        }
    }
    registry.profiles.sort_by(|a, b| {
        caseless::default_case_fold_str(&a.name)
            .cmp(&caseless::default_case_fold_str(&b.name))
            .then(a.norad_id.cmp(&b.norad_id))
            .then(a.selector.cmp(&b.selector))
    });
    let mut names: BTreeMap<String, Vec<&SatYamlProfile>> = BTreeMap::new();
    let mut norads: BTreeMap<u64, Vec<&SatYamlProfile>> = BTreeMap::new();
    for p in &registry.profiles {
        names
            .entry(caseless::default_case_fold_str(&p.name))
            .or_default()
            .push(p);
        norads.entry(p.norad_id).or_default().push(p);
    }
    for (name, matches) in names {
        if matches.len() == 1 {
            registry
                .name_index
                .insert(name, matches[0].selector.clone());
        } else {
            registry.conflicted_names.push(name);
            registry.diagnostics.push(SatYamlDiagnostic {
                path: matches[0].selector.clone(),
                code: "duplicate_name".into(),
                message: format!(
                    "profile name {:?} is declared by: {}",
                    matches[0].name,
                    matches
                        .iter()
                        .map(|p| p.selector.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            });
        }
    }
    for (norad, matches) in norads {
        if matches.len() == 1 {
            registry
                .norad_index
                .insert(norad, matches[0].selector.clone());
        } else {
            registry.conflicted_norads.push(norad);
            registry.diagnostics.push(SatYamlDiagnostic {
                path: matches[0].selector.clone(),
                code: "duplicate_norad".into(),
                message: format!(
                    "NORAD {norad} is declared by: {}",
                    matches
                        .iter()
                        .map(|p| p.selector.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            });
        }
    }
    registry.diagnostics.sort();
    Ok(registry)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CatalogObservation {
    pub observation_id: u64,
    pub satellite_id: String,
    pub frequency_hz: f64,
    pub mode: Option<String>,
    pub iq_url: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CatalogTransmitter {
    pub transmitter_uuid: String,
    pub satellite_id: String,
    pub norad_id: u64,
    pub frequency_hz: f64,
    pub mode: Option<String>,
    pub baud: Option<f64>,
    pub status: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RoutingStatus {
    Routed,
    UnmatchedSatelliteUuid,
    UnmatchedCatalogFrequency,
    AmbiguousCatalogNorad,
    UnmatchedSatyamlProfile,
    AmbiguousSatyamlProfile,
    UnmatchedProfileFrequency,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParameterDisagreement {
    pub field: String,
    pub observed: Vec<Value>,
    pub profile: Vec<Value>,
    pub comparison: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ObservationRoute {
    pub observation: CatalogObservation,
    pub status: RoutingStatus,
    pub frequency_tolerance_hz: f64,
    pub catalog_transmitters: Vec<CatalogTransmitter>,
    pub norad_id: Option<u64>,
    pub profile: Option<SatYamlProfile>,
    pub profile_transmitter_ids: Vec<String>,
    pub disagreements: Vec<ParameterDisagreement>,
}
fn positive(value: f64) -> bool {
    value.is_finite() && value > 0.0
}
fn optional_text(value: &Option<String>) -> bool {
    value.as_ref().is_none_or(|v| !v.trim().is_empty())
}
fn disagreements(
    observation: &CatalogObservation,
    catalog: &[CatalogTransmitter],
    profile: &[&SatYamlTransmitter],
) -> Vec<ParameterDisagreement> {
    let mut result = vec![];
    let mut observed = BTreeMap::new();
    for mode in std::iter::once(&observation.mode)
        .chain(catalog.iter().map(|c| &c.mode))
        .flatten()
    {
        observed.insert(caseless::default_case_fold_str(mode), mode.clone());
    }
    let mut profile_modes = BTreeMap::new();
    for tx in profile {
        profile_modes.insert(
            caseless::default_case_fold_str(&tx.modulation),
            tx.modulation.clone(),
        );
    }
    if !observed.is_empty()
        && !profile_modes.is_empty()
        && !observed.keys().all(|k| profile_modes.contains_key(k))
    {
        result.push(ParameterDisagreement {
            field: "mode".into(),
            observed: observed.into_values().map(Value::String).collect(),
            profile: profile_modes.into_values().map(Value::String).collect(),
            comparison: "exact_no_aliases".into(),
        });
    }
    let mut catalog_bauds: Vec<f64> = catalog.iter().filter_map(|c| c.baud).collect();
    catalog_bauds.sort_by(f64::total_cmp);
    catalog_bauds.dedup();
    let mut profile_bauds: Vec<f64> = profile
        .iter()
        .filter_map(|t| {
            t.metadata
                .get("baudrate")
                .and_then(Value::as_f64)
                .filter(|v| positive(*v))
        })
        .collect();
    profile_bauds.sort_by(f64::total_cmp);
    profile_bauds.dedup();
    if !catalog_bauds.is_empty()
        && !profile_bauds.is_empty()
        && catalog_bauds.iter().any(|a| {
            !profile_bauds
                .iter()
                .any(|b| (a - b).abs() <= 1e-9 * a.abs().max(b.abs()))
        })
    {
        result.push(ParameterDisagreement {
            field: "baud".into(),
            observed: catalog_bauds.into_iter().map(Value::from).collect(),
            profile: profile_bauds.into_iter().map(Value::from).collect(),
            comparison: "exact_no_aliases".into(),
        });
    }
    result
}
/// Identity is UUID plus explicitly bounded frequency, then unique NORAD.
/// Names, mode aliases, and visible signal are never used to establish identity.
pub fn route_catalog_observations(
    observations: &[CatalogObservation],
    transmitters: &[CatalogTransmitter],
    registry: &SatYamlRegistry,
    tolerance: f64,
) -> Result<Vec<ObservationRoute>, String> {
    if !tolerance.is_finite() || tolerance < 0.0 {
        return Err("frequency_tolerance_hz must be finite and non-negative".into());
    }
    for tx in transmitters {
        if tx.transmitter_uuid.trim().is_empty()
            || tx.satellite_id.trim().is_empty()
            || tx.norad_id == 0
            || !positive(tx.frequency_hz)
            || tx.baud.is_some_and(|b| !positive(b))
            || !optional_text(&tx.mode)
            || !optional_text(&tx.status)
        {
            return Err("invalid catalogue transmitter contract".into());
        }
    }
    let mut by_satellite: BTreeMap<&str, Vec<&CatalogTransmitter>> = BTreeMap::new();
    for tx in transmitters {
        by_satellite.entry(&tx.satellite_id).or_default().push(tx);
    }
    for values in by_satellite.values_mut() {
        values.sort_by_key(|v| &v.transmitter_uuid);
    }
    let mut routes = vec![];
    let mut ids = BTreeSet::new();
    for obs in observations {
        if obs.observation_id == 0
            || obs.satellite_id.trim().is_empty()
            || obs.iq_url.trim().is_empty()
            || !positive(obs.frequency_hz)
            || !optional_text(&obs.mode)
        {
            return Err("invalid catalogue observation contract".into());
        }
        if !ids.insert(obs.observation_id) {
            return Err("observation IDs must be unique".into());
        }
        let mut route = ObservationRoute {
            observation: obs.clone(),
            status: RoutingStatus::UnmatchedSatelliteUuid,
            frequency_tolerance_hz: tolerance,
            catalog_transmitters: vec![],
            norad_id: None,
            profile: None,
            profile_transmitter_ids: vec![],
            disagreements: vec![],
        };
        let Some(satellites) = by_satellite.get(obs.satellite_id.as_str()) else {
            routes.push(route);
            continue;
        };
        let matched: Vec<CatalogTransmitter> = satellites
            .iter()
            .filter(|t| (t.frequency_hz - obs.frequency_hz).abs() <= tolerance)
            .map(|t| (*t).clone())
            .collect();
        if matched.is_empty() {
            route.status = RoutingStatus::UnmatchedCatalogFrequency;
            routes.push(route);
            continue;
        }
        let norads: BTreeSet<u64> = matched.iter().map(|t| t.norad_id).collect();
        route.catalog_transmitters = matched;
        if norads.len() != 1 {
            route.status = RoutingStatus::AmbiguousCatalogNorad;
            routes.push(route);
            continue;
        }
        let norad = *norads.first().unwrap();
        route.norad_id = Some(norad);
        if registry.conflicted_norads.contains(&norad) {
            route.status = RoutingStatus::AmbiguousSatyamlProfile;
            routes.push(route);
            continue;
        }
        let Some(profile) = registry.get_by_norad(norad) else {
            route.status = RoutingStatus::UnmatchedSatyamlProfile;
            routes.push(route);
            continue;
        };
        route.profile = Some(profile.clone());
        let profile_matches: Vec<&SatYamlTransmitter> = profile
            .transmitters
            .iter()
            .filter(|t| {
                t.metadata
                    .get("frequency")
                    .and_then(Value::as_f64)
                    .is_some_and(|f| positive(f) && (f - obs.frequency_hz).abs() <= tolerance)
            })
            .collect();
        route.disagreements = disagreements(
            obs,
            &route.catalog_transmitters,
            &if profile_matches.is_empty() {
                profile.transmitters.iter().collect()
            } else {
                profile_matches.clone()
            },
        );
        if profile_matches.is_empty() {
            route.status = RoutingStatus::UnmatchedProfileFrequency;
        } else {
            route.status = RoutingStatus::Routed;
            route.profile_transmitter_ids = profile_matches
                .into_iter()
                .map(|p| p.transmitter_id.clone())
                .collect();
            route.profile_transmitter_ids.sort();
        }
        routes.push(route);
    }
    routes.sort_by_key(|r| r.observation.observation_id);
    Ok(routes)
}

#[cfg(test)]
#[path = "tests/formats_tests.rs"]
mod tests;
