//! Experimental Vorbis side-information, not an estimate of quantization error.
//!
//! Packet PTS/duration do not always describe the PCM span produced by FFmpeg
//! at a Vorbis block transition. We retain those fields as evidence but locate
//! features by cumulative *decoded* frame lengths, paired with their packets.
//! Callers must use an unresampled, untrimmed mono decode of the same source.
use crate::input;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Read;
use std::path::Path;

const MAX_PROBE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_PACKETS: usize = 250_000;
const MAX_CALIBRATION_SAMPLES: usize = 131_072;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodecPacket {
    pub packet_index: usize,
    pub source_pts: i64,
    pub source_duration: u64,
    pub source_position: u64,
    pub encoded_bytes: u64,
    pub decoded_start: usize,
    pub decoded_samples: usize,
    /// Change in adjacent decoded output lengths, NOT a parsed mode/block flag.
    pub duration_transition: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodecMetadata {
    pub schema: String,
    pub source: input::Identity,
    pub sample_rate: u32,
    pub decoded_samples: usize,
    pub packets: Vec<CodecPacket>,
    pub priming_packets: usize,
    pub timestamp_duration_disagreements: usize,
    pub probe_command: Vec<String>,
    pub coordinate_contract: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct CodecFeature {
    pub packet_index: usize,
    pub log_bits_per_sample: f64,
    pub duration_transition: bool,
}

impl CodecMetadata {
    /// Bounded 30-second FFprobe decode; source/probe JSON <=64 MiB each.
    /// Frame counts must exactly match the independently decoded PCM length.
    pub fn probe(source: &Path, sample_rate: u32, decoded_samples: usize) -> Result<Self, String> {
        if sample_rate == 0
            || decoded_samples == 0
            || decoded_samples as u64 > input::MAX_LOADED_AUDIO_SAMPLES
            || decoded_samples as f64 / sample_rate as f64 > input::MAX_AUDIO_SECONDS
        {
            return Err("codec sideinfo: invalid or oversized decoded audio contract".into());
        }
        let mut file = input::open_regular(source)?;
        if file.metadata().map_err(|e| e.to_string())?.len() > MAX_PROBE_BYTES {
            return Err("codec sideinfo: original OGG exceeds64MiB".into());
        }
        let mut magic = [0; 4];
        file.read_exact(&mut magic).map_err(|e| e.to_string())?;
        if &magic != b"OggS" {
            return Err("codec sideinfo: requires original Ogg/Vorbis, not WAV or IQ".into());
        }
        let identity = input::identity(source)?;
        let scratch = tempfile::tempdir().map_err(|e| e.to_string())?;
        let output = scratch.path().join("packets-and-frames.json");
        let args = vec![
            "-v".into(),
            "error".into(),
            "-threads".into(),
            "1".into(),
            "-show_entries".into(),
            "stream=index,codec_name,codec_type,sample_rate,channels,time_base,start_pts:packet=stream_index,pts,duration,size,pos:frame=stream_index,pts,nb_samples,pkt_size,pkt_pos".into(),
            "-of".into(),
            "json=compact=1".into(),
            "-o".into(),
            output.display().to_string(),
            identity.path.clone(),
        ];
        input::external_with_file_limit("ffprobe", &args, 30, MAX_PROBE_BYTES)?;
        let bytes = input::read_bytes_bounded(&output, MAX_PROBE_BYTES)?;
        let raw: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        let after = input::identity(source)?;
        if identity.path != after.path
            || identity.sha256 != after.sha256
            || identity.bytes != after.bytes
        {
            return Err("codec sideinfo: original source changed during probe".into());
        }
        let mut result = Self::parse(&raw, identity, sample_rate, decoded_samples)?;
        result.probe_command = std::iter::once("ffprobe".into()).chain(args).collect();
        Ok(result)
    }

    fn parse(
        raw: &Value,
        source: input::Identity,
        sample_rate: u32,
        decoded_samples: usize,
    ) -> Result<Self, String> {
        let streams = raw["streams"]
            .as_array()
            .ok_or("codec sideinfo: missing streams")?;
        if streams.len() != 1 {
            return Err("codec sideinfo: requires exactly one logical stream".into());
        }
        let stream = &streams[0];
        if stream["codec_name"] != "vorbis"
            || stream["codec_type"] != "audio"
            || number_u64(stream, "channels")? != 1
            || number_u64(stream, "sample_rate")? != u64::from(sample_rate)
            || stream["time_base"] != format!("1/{sample_rate}")
            || number_i64(stream, "start_pts")? != 0
        {
            return Err(
                "codec sideinfo: unsupported codec/channels/rate/timebase/start offset".into(),
            );
        }
        let stream_index = number_u64(stream, "index")?;
        let rows = raw["packets_and_frames"]
            .as_array()
            .ok_or("codec sideinfo: requires jointly ordered packet/frame records")?;
        if rows.is_empty() || rows.len() > MAX_PACKETS * 2 + 1 {
            return Err("codec sideinfo: missing or excessive packet/frame records".into());
        }
        let mut packets: Vec<CodecPacket> = Vec::new();
        let mut pending: Option<&Value> = None;
        let mut packet_index = 0usize;
        let mut priming_packets = 0usize;
        let mut offset = 0usize;
        let mut disagreements = 0usize;
        let mut previous_pts = None;
        for row in rows {
            if number_u64(row, "stream_index")? != stream_index {
                return Err("codec sideinfo: unexpected stream change".into());
            }
            match row["type"].as_str() {
                Some("packet") => {
                    if let Some(old) = pending.take() {
                        // A sole first packet primes the overlap. FFmpeg's
                        // own muxer may timestamp it zero; other encoders use
                        // a negative PTS ending at zero. Neither emits PCM.
                        let old_pts = number_i64(old, "pts")?;
                        let old_duration = i64::try_from(number_u64(old, "duration")?)
                            .map_err(|e| e.to_string())?;
                        if packet_index != 1
                            || !packets.is_empty()
                            || priming_packets != 0
                            || !(old_pts == 0 || (old_pts < 0 && old_pts + old_duration == 0))
                        {
                            return Err(format!(
                                "codec sideinfo: ambiguous packet without decoded frame (packet_count={packet_index}, frames={}, pts={}, duration={})",
                                packets.len(),
                                number_i64(old, "pts")?,
                                number_u64(old, "duration")?
                            ));
                        }
                        priming_packets += 1;
                    }
                    let pts = number_i64(row, "pts")?;
                    if previous_pts.is_some_and(|old| pts <= old) {
                        return Err("codec sideinfo: nonincreasing packet timestamps".into());
                    }
                    previous_pts = Some(pts);
                    let duration = number_u64(row, "duration")?;
                    let size = number_u64(row, "size")?;
                    let position = number_u64(row, "pos")?;
                    if duration == 0
                        || duration > 8192
                        || size == 0
                        || size > source.bytes
                        || position >= source.bytes
                    {
                        return Err("codec sideinfo: invalid packet duration/size/position".into());
                    }
                    pending = Some(row);
                    packet_index += 1;
                    if packet_index > MAX_PACKETS {
                        return Err("codec sideinfo: packet limit exceeded".into());
                    }
                }
                Some("frame") => {
                    let packet = pending
                        .take()
                        .ok_or("codec sideinfo: unpaired decoded frame")?;
                    if number_i64(row, "pts")? != number_i64(packet, "pts")?
                        || number_u64(row, "pkt_size")? != number_u64(packet, "size")?
                        || number_u64(row, "pkt_pos")? != number_u64(packet, "pos")?
                    {
                        return Err("codec sideinfo: packet/frame provenance mismatch".into());
                    }
                    let count = usize::try_from(number_u64(row, "nb_samples")?)
                        .map_err(|e| e.to_string())?;
                    if count == 0 || count > 8192 {
                        return Err("codec sideinfo: unsupported decoded frame sample count".into());
                    }
                    let end = offset
                        .checked_add(count)
                        .ok_or("codec sideinfo: sample overflow")?;
                    if end > decoded_samples {
                        return Err(
                            "codec sideinfo: decoded frame count exceeds expected PCM".into()
                        );
                    }
                    let pts = number_i64(packet, "pts")?;
                    let duration = number_u64(packet, "duration")?;
                    if pts != offset as i64 || duration != count as u64 {
                        disagreements += 1;
                    }
                    packets.push(CodecPacket {
                        packet_index: packet_index - 1,
                        source_pts: pts,
                        source_duration: duration,
                        source_position: number_u64(packet, "pos")?,
                        encoded_bytes: number_u64(packet, "size")?,
                        decoded_start: offset,
                        decoded_samples: count,
                        duration_transition: false,
                    });
                    offset = end;
                }
                _ => return Err("codec sideinfo: unknown packet/frame record".into()),
            }
        }
        if pending.is_some() || packets.is_empty() || offset != decoded_samples {
            return Err("codec sideinfo: incomplete or mismatched PCM mapping".into());
        }
        for i in 0..packets.len() {
            packets[i].duration_transition = (i > 0
                && packets[i - 1].decoded_samples != packets[i].decoded_samples)
                || (i + 1 < packets.len()
                    && packets[i + 1].decoded_samples != packets[i].decoded_samples);
        }
        Ok(Self {
            schema: "vorbis-packet-reliability-evidence-v1".into(),
            source,
            sample_rate,
            decoded_samples,
            packets,
            priming_packets,
            timestamp_duration_disagreements: disagreements,
            probe_command: Vec::new(),
            coordinate_contract: "zero-based cumulative FFmpeg decoded mono samples; no trim/resample/downmix; length exactly verified; packet PTS retained but not used as PCM index".into(),
        })
    }

    pub fn feature_at(&self, absolute_sample: f64) -> Result<CodecFeature, String> {
        if !absolute_sample.is_finite()
            || absolute_sample < 0.0
            || absolute_sample >= self.decoded_samples as f64
        {
            return Err("codec sideinfo: sample coordinate outside decoded source".into());
        }
        let index = self
            .packets
            .partition_point(|p| (p.decoded_start as f64) <= absolute_sample);
        let packet = index
            .checked_sub(1)
            .and_then(|i| self.packets.get(i))
            .ok_or("codec sideinfo: uncovered sample")?;
        let end = packet
            .decoded_start
            .checked_add(packet.decoded_samples)
            .ok_or("codec sideinfo: packet mapping overflow")?;
        if absolute_sample >= end as f64 || packet.decoded_samples == 0 || packet.encoded_bytes == 0
        {
            return Err("codec sideinfo: invalid or gapped packet mapping".into());
        }
        Ok(CodecFeature {
            packet_index: packet.packet_index,
            log_bits_per_sample: (8.0 * packet.encoded_bytes as f64
                / packet.decoded_samples as f64)
                .ln(),
            duration_transition: packet.duration_transition,
        })
    }
}

fn number_i64(value: &Value, key: &str) -> Result<i64, String> {
    value[key]
        .as_i64()
        .or_else(|| value[key].as_str().and_then(|s| s.parse().ok()))
        .ok_or_else(|| format!("codec sideinfo: missing/noninteger {key}"))
}

fn number_u64(value: &Value, key: &str) -> Result<u64, String> {
    value[key]
        .as_u64()
        .or_else(|| value[key].as_str().and_then(|s| s.parse().ok()))
        .ok_or_else(|| format!("codec sideinfo: missing/nonunsigned {key}"))
}

/// Residuals and zero-based original decoded PCM coordinates. Caller must
/// establish CRC-valid anchor provenance, signal-model independence and the
/// target guard. This API verifies sample ordering and train/check disjointness.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CalibrationBlock {
    pub id: String,
    pub absolute_samples: Vec<f64>,
    pub residuals: Vec<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VarianceModel {
    pub source_sha256: String,
    pub density_mean: f64,
    pub density_scale: f64,
    pub coefficients: [f64; 3],
    pub log_normalizer: f64,
    pub constant_variance: f64,
}

impl VarianceModel {
    /// Dimensionless candidate variance relative to the training mean, [.25,4].
    pub fn multiplier(
        &self,
        metadata: &CodecMetadata,
        absolute_sample: f64,
    ) -> Result<f64, String> {
        if self.source_sha256 != metadata.source.sha256 {
            return Err("codec reliability: model belongs to another original source".into());
        }
        if !self.density_mean.is_finite()
            || !self.density_scale.is_finite()
            || self.density_scale <= 0.0
            || !self.log_normalizer.is_finite()
            || !self.constant_variance.is_finite()
            || self.constant_variance <= 0.0
            || self.coefficients.iter().any(|v| !v.is_finite())
        {
            return Err("codec reliability: invalid serialized variance model".into());
        }
        let feature = metadata.feature_at(absolute_sample)?;
        let value = self.multiplier_feature(feature);
        if !value.is_finite() {
            return Err("codec reliability: nonfinite variance prediction".into());
        }
        Ok(value)
    }

    fn multiplier_feature(&self, feature: CodecFeature) -> f64 {
        let x = feature_vector(feature, self.density_mean, self.density_scale);
        (dot(self.coefficients, x) - self.log_normalizer)
            .clamp(-4f64.ln(), 4f64.ln())
            .exp()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VarianceFit {
    pub accepted: bool,
    pub reason: String,
    /// Absent on rejection: no silent use of an unvalidated candidate.
    pub model: Option<VarianceModel>,
    pub training_ids: Vec<String>,
    pub heldout_ids: Vec<String>,
    pub training_samples: usize,
    pub heldout_samples: usize,
    pub heldout_log_score_gain_per_sample: f64,
    pub heldout_block_gains: Vec<f64>,
}

/// Fixed ridge log-variance regression, trained only on `training`, gated once
/// on untouched `heldout`. Heldout scores never adjust coefficients. A positive
/// score is conditional residual calibration, not proof of packet recovery.
pub fn fit_variance(
    metadata: &CodecMetadata,
    training: &[CalibrationBlock],
    heldout: &[CalibrationBlock],
) -> Result<VarianceFit, String> {
    validate_blocks(metadata, training, heldout)?;
    let train_count: usize = training.iter().map(|b| b.residuals.len()).sum();
    let check_count: usize = heldout.iter().map(|b| b.residuals.len()).sum();
    let mut result = VarianceFit {
        accepted: false,
        reason: String::new(),
        model: None,
        training_ids: training.iter().map(|b| b.id.clone()).collect(),
        heldout_ids: heldout.iter().map(|b| b.id.clone()).collect(),
        training_samples: train_count,
        heldout_samples: check_count,
        heldout_log_score_gain_per_sample: 0.0,
        heldout_block_gains: Vec::new(),
    };
    let mut rows = Vec::with_capacity(train_count);
    let mut packets = BTreeSet::new();
    for block in training {
        for (&position, &residual) in block.absolute_samples.iter().zip(&block.residuals) {
            let feature = metadata.feature_at(position)?;
            packets.insert(feature.packet_index);
            rows.push((feature, residual * residual));
        }
    }
    let mean = rows.iter().map(|(f, _)| f.log_bits_per_sample).sum::<f64>() / train_count as f64;
    let scale = (rows
        .iter()
        .map(|(f, _)| (f.log_bits_per_sample - mean).powi(2))
        .sum::<f64>()
        / train_count as f64)
        .sqrt();
    let variance = rows.iter().map(|(_, energy)| energy).sum::<f64>() / train_count as f64;
    if packets.len() < 4 || scale < 0.02 || !variance.is_finite() || variance < 1e-14 {
        result.reason =
            "insufficient packet diversity, density variation or residual energy".into();
        return Ok(result);
    }
    let mut matrix = [[0.0; 3]; 3];
    let mut rhs = [0.0; 3];
    for &(feature, energy) in &rows {
        let x = feature_vector(feature, mean, scale);
        let y = (energy / variance + 1e-6).ln();
        for i in 0..3 {
            rhs[i] += x[i] * y;
            for j in 0..3 {
                matrix[i][j] += x[i] * x[j];
            }
        }
    }
    // Fixed regularization, not chosen by target CRC or the heldout score.
    matrix[0][0] += 1e-8;
    matrix[1][1] += train_count as f64 * 0.1;
    matrix[2][2] += train_count as f64 * 0.1;
    let coefficients = solve3(matrix, rhs).ok_or("codec reliability: singular regression")?;
    let average = rows
        .iter()
        .map(|(feature, _)| {
            dot(coefficients, feature_vector(*feature, mean, scale))
                .clamp(-20.0, 20.0)
                .exp()
        })
        .sum::<f64>()
        / train_count as f64;
    let model = VarianceModel {
        source_sha256: metadata.source.sha256.clone(),
        density_mean: mean,
        density_scale: scale,
        coefficients,
        log_normalizer: average.ln(),
        constant_variance: variance,
    };
    let mut gain_sum = 0.0;
    for block in heldout {
        let mut block_gain = 0.0;
        for (&position, &residual) in block.absolute_samples.iter().zip(&block.residuals) {
            let m = model.multiplier(metadata, position)?;
            let normalized_energy = residual * residual / variance;
            block_gain += 0.5 * (normalized_energy - normalized_energy / m - m.ln());
        }
        if !block_gain.is_finite() {
            return Err("codec reliability: nonfinite heldout score".into());
        }
        gain_sum += block_gain;
        result
            .heldout_block_gains
            .push(block_gain / block.residuals.len() as f64);
    }
    result.heldout_log_score_gain_per_sample = gain_sum / check_count as f64;
    result.accepted = result.heldout_log_score_gain_per_sample >= 0.01
        && result.heldout_block_gains.iter().all(|gain| *gain >= -0.01);
    if result.accepted {
        result.reason = "fixed residual-likelihood gate passed; not a frame-recovery claim".into();
        result.model = Some(model);
    } else {
        result.reason =
            "codec-conditioned variance did not beat constant variance on heldout residuals".into();
    }
    Ok(result)
}

fn validate_blocks(
    metadata: &CodecMetadata,
    training: &[CalibrationBlock],
    heldout: &[CalibrationBlock],
) -> Result<(), String> {
    if training.is_empty() || heldout.is_empty() || training.len() + heldout.len() > 32 {
        return Err("codec reliability: need1..32 separate training/check blocks".into());
    }
    let mut ids = BTreeSet::new();
    let mut intervals = Vec::new();
    for block in training.iter().chain(heldout) {
        if block.id.is_empty()
            || !ids.insert(&block.id)
            || block.absolute_samples.len() != block.residuals.len()
            || block.residuals.len() < 32
        {
            return Err(
                "codec reliability: invalid, duplicate or undersized calibration block".into(),
            );
        }
        let mut previous = None;
        for (&position, &residual) in block.absolute_samples.iter().zip(&block.residuals) {
            if !residual.is_finite()
                || residual.abs() > 1e100
                || previous.is_some_and(|p| position <= p)
            {
                return Err(
                    "codec reliability: invalid residual or unordered sample coordinates".into(),
                );
            }
            metadata.feature_at(position)?;
            previous = Some(position);
        }
        intervals.push((
            block.absolute_samples[0],
            *block.absolute_samples.last().unwrap(),
        ));
    }
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0));
    if intervals.windows(2).any(|w| w[0].1 >= w[1].0) {
        return Err(
            "codec reliability: calibration blocks overlap; heldout must be disjoint".into(),
        );
    }
    for blocks in [training, heldout] {
        let count: usize = blocks.iter().map(|b| b.residuals.len()).sum();
        if !(128..=MAX_CALIBRATION_SAMPLES).contains(&count) {
            return Err("codec reliability: each partition needs128..131072 samples".into());
        }
    }
    Ok(())
}

fn feature_vector(feature: CodecFeature, mean: f64, scale: f64) -> [f64; 3] {
    [
        1.0,
        ((feature.log_bits_per_sample - mean) / scale).clamp(-4.0, 4.0),
        f64::from(u8::from(feature.duration_transition)),
    ]
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

// Indexed elimination makes the fixed three-row pivot and arithmetic order
// explicit; preserve that order for frozen experiment reproducibility.
#[allow(clippy::needless_range_loop)]
fn solve3(mut matrix: [[f64; 3]; 3], mut rhs: [f64; 3]) -> Option<[f64; 3]> {
    for col in 0..3 {
        let pivot =
            (col..3).max_by(|&a, &b| matrix[a][col].abs().total_cmp(&matrix[b][col].abs()))?;
        if matrix[pivot][col].abs() < 1e-12 {
            return None;
        }
        matrix.swap(col, pivot);
        rhs.swap(col, pivot);
        let divisor = matrix[col][col];
        for k in col..3 {
            matrix[col][k] /= divisor;
        }
        rhs[col] /= divisor;
        for row in 0..3 {
            if row == col {
                continue;
            }
            let factor = matrix[row][col];
            for k in col..3 {
                matrix[row][k] -= factor * matrix[col][k];
            }
            rhs[row] -= factor * rhs[col];
        }
    }
    rhs.iter().all(|v| v.is_finite()).then_some(rhs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn source() -> input::Identity {
        input::Identity {
            path: "fixture.ogg".into(),
            sha256: "a".repeat(64),
            bytes: 100_000,
        }
    }
    fn row_packet(pts: i64, duration: u64, size: u64) -> Value {
        json!({"type":"packet","stream_index":0,"pts":pts,"duration":duration,"size":size.to_string(),"pos":"42"})
    }
    fn row_frame(pts: i64, count: usize, size: u64) -> Value {
        json!({"type":"frame","stream_index":0,"pts":pts,"nb_samples":count,"pkt_size":size.to_string(),"pkt_pos":"42"})
    }
    fn fixture() -> Value {
        json!({"streams":[{"index":0,"codec_name":"vorbis","codec_type":"audio","channels":1,"sample_rate":"48000","time_base":"1/48000","start_pts":0}],"packets_and_frames":[row_packet(-128,128,1),row_packet(0,576,55),row_frame(0,576,55),row_packet(576,1024,173),row_frame(576,1024,173),row_packet(2048,128,62),row_frame(2048,576,62)]})
    }

    #[test]
    fn uses_decoded_count_not_transition_pts_and_duration() {
        let m = CodecMetadata::parse(&fixture(), source(), 48000, 2176).unwrap();
        assert_eq!(m.priming_packets, 1);
        assert_eq!(m.timestamp_duration_disagreements, 1);
        assert_eq!(m.packets[2].decoded_start, 1600);
        assert_eq!(m.feature_at(1600.5).unwrap().packet_index, 3);
        assert!(
            (m.feature_at(1600.5).unwrap().log_bits_per_sample - (8.0f64 * 62.0 / 576.0).ln())
                .abs()
                < 1e-12
        );
        assert!(m.feature_at(2176.0).is_err());
        assert!(m.feature_at(f64::NAN).is_err());
    }

    #[test]
    fn rejects_mismatched_codec_rate_length_and_pairing() {
        assert!(CodecMetadata::parse(&fixture(), source(), 44100, 2176).is_err());
        assert!(CodecMetadata::parse(&fixture(), source(), 48000, 2177).is_err());
        let mut raw = fixture();
        raw["streams"][0]["codec_name"] = json!("opus");
        assert!(CodecMetadata::parse(&raw, source(), 48000, 2176).is_err());
        let mut raw = fixture();
        raw["packets_and_frames"][4]["pkt_size"] = json!("174");
        assert!(CodecMetadata::parse(&raw, source(), 48000, 2176).is_err());
        let mut raw = fixture();
        raw["packets_and_frames"].as_array_mut().unwrap().remove(4);
        assert!(CodecMetadata::parse(&raw, source(), 48000, 2176).is_err());
    }

    fn calibration_fixture() -> CodecMetadata {
        CodecMetadata {
            schema: "fixture".into(),
            source: source(),
            sample_rate: 48000,
            decoded_samples: 8192,
            priming_packets: 0,
            timestamp_duration_disagreements: 0,
            probe_command: Vec::new(),
            coordinate_contract: "fixture".into(),
            packets: (0..64)
                .map(|i| CodecPacket {
                    packet_index: i,
                    source_pts: (i * 128) as i64,
                    source_duration: 128,
                    source_position: 42,
                    encoded_bytes: if i % 2 == 0 { 16 } else { 128 },
                    decoded_start: i * 128,
                    decoded_samples: 128,
                    duration_transition: false,
                })
                .collect(),
        }
    }
    fn block(
        metadata: &CodecMetadata,
        id: &str,
        start: usize,
        heterogeneous: bool,
    ) -> CalibrationBlock {
        let positions: Vec<f64> = (start..start + 2048).map(|i| i as f64).collect();
        let residuals = positions
            .iter()
            .enumerate()
            .map(|(i, &p)| {
                let scale =
                    if heterogeneous && metadata.feature_at(p).unwrap().packet_index % 2 == 0 {
                        0.5
                    } else {
                        1.5
                    };
                if i % 2 == 0 { scale } else { -scale }
            })
            .collect();
        CalibrationBlock {
            id: id.into(),
            absolute_samples: positions,
            residuals,
        }
    }
    #[test]
    fn fits_only_training_and_accepts_predictive_codec_proxy() {
        let m = calibration_fixture();
        let fit = fit_variance(
            &m,
            &[block(&m, "train", 0, true)],
            &[block(&m, "check", 4096, true)],
        )
        .unwrap();
        assert!(fit.accepted, "{}", fit.heldout_log_score_gain_per_sample);
        let model = fit.model.unwrap();
        assert!(model.multiplier(&m, 0.0).unwrap() < model.multiplier(&m, 128.0).unwrap());
        assert!(model.multiplier(&m, 0.0).unwrap() >= 0.25);
        let mut other = m.clone();
        other.source.sha256 = "b".repeat(64);
        assert!(model.multiplier(&other, 0.0).is_err());
    }
    #[test]
    fn rejects_nonpredictive_or_overlapping_calibration() {
        let m = calibration_fixture();
        let fit = fit_variance(
            &m,
            &[block(&m, "train", 0, true)],
            &[block(&m, "check", 4096, false)],
        )
        .unwrap();
        assert!(!fit.accepted);
        assert!(fit.model.is_none());
        assert!(
            fit_variance(
                &m,
                &[block(&m, "train", 0, true)],
                &[block(&m, "check", 1024, true)]
            )
            .is_err()
        );
        assert!(
            fit_variance(
                &m,
                &[block(&m, "same", 0, true)],
                &[block(&m, "same", 4096, true)]
            )
            .is_err()
        );
        let mut bad = block(&m, "bad", 4096, true);
        bad.residuals[0] = f64::NAN;
        assert!(fit_variance(&m, &[block(&m, "train", 0, true)], &[bad]).is_err());
    }
    #[test]
    fn real_vorbis_probe_matches_separately_decoded_pcm() {
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("signal.wav");
        let ogg = dir.path().join("signal.ogg");
        let mut writer = hound::WavWriter::create(
            &wav,
            hound::WavSpec {
                channels: 1,
                sample_rate: 48000,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .unwrap();
        for i in 0..48000 {
            writer
                .write_sample((12000.0 * (i as f64 * 0.14).sin()) as i16)
                .unwrap();
        }
        writer.finalize().unwrap();
        input::external(
            "ffmpeg",
            &[
                "-nostdin",
                "-v",
                "error",
                "-i",
                wav.to_str().unwrap(),
                "-c:a",
                "libvorbis",
                "-q:a",
                "2",
                ogg.to_str().unwrap(),
            ]
            .map(String::from),
            10,
        )
        .unwrap();
        let scratch = tempfile::tempdir().unwrap();
        let audio = input::load_audio(&ogg, scratch.path()).unwrap();
        let metadata = CodecMetadata::probe(&ogg, audio.sample_rate, audio.samples.len()).unwrap();
        assert_eq!(metadata.decoded_samples, audio.samples.len());
        assert!(metadata.packets.len() > 10);
        assert!(metadata.feature_at(0.0).is_ok());
        assert!(CodecMetadata::probe(&wav, 48000, 48000).is_err());
    }

    /// Repository-local development recording, deliberately not a portable test.
    #[test]
    #[ignore = "requires preserved development observation14967362"]
    fn development_14967362_original_mapping() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        let path =
            root.join("work/satnogs-today20-20260910-v1/acquisition/obs-14967362/capture.ogg");
        let metadata = CodecMetadata::probe(&path, 48000, 32_929_856).unwrap();
        assert!(metadata.packets.len() > 1000);
        assert_eq!(metadata.decoded_samples, 32_929_856);
        assert!(metadata.timestamp_duration_disagreements > 0);
        assert_eq!(metadata.packets[2].decoded_start, 1600);
        assert_eq!(metadata.packets[2].source_pts, 2048);
        assert_eq!(metadata.packets[2].decoded_samples, 576);
        println!(
            "original14967362: packets={}, samples={}, timestamp_disagreements={}, source_sha256={}",
            metadata.packets.len(),
            metadata.decoded_samples,
            metadata.timestamp_duration_disagreements,
            metadata.source.sha256
        );
    }
}
