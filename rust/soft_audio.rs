//! Bounded development replay of BCJR against the unchanged MLSE audio lane.
use crate::{adaptive, input, uncertainty_audio};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::Path;

/// Keep this pilot separate from frozen progressive sessions. A new output
/// directory is required; source identity and the exact crop precede decoding.
pub fn replay(
    path: &Path,
    output: &Path,
    duration: f64,
    baud: f64,
    threads: usize,
) -> Result<Value, String> {
    if !duration.is_finite()
        || !(1.0..=120.0).contains(&duration)
        || !baud.is_finite()
        || !(100.0..=100_000.0).contains(&baud)
        || !(1..=16).contains(&threads)
    {
        return Err(
            "BCJR audio pilot requires 1..=120 seconds, 100..=100000 baud and 1..=16 threads"
                .into(),
        );
    }
    let out = input::existing_new_dir(output)?;
    let result = (|| {
        let audio = input::load_audio(path, &out)?;
        let end = (duration * audio.sample_rate as f64).round() as usize;
        let samples = audio
            .samples
            .get(..end)
            .ok_or("requested pilot duration exceeds recording")?;
        let mut hash = Sha256::new();
        for sample in samples {
            hash.update(sample.to_bits().to_le_bytes());
        }
        let config = adaptive::AdaptiveConfig { baud, threads };
        input::write_json_new(
            &out.join("plan.json"),
            &json!({
                "schema":"framelift-bcjr-audio-pilot-v1", "development_only":true,
                "source":audio.source_identity, "decoded_wav":audio.wav_identity,
                "conversion_command":audio.conversion_command,
                "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
                "crop_start_sample":0,"crop_end_sample":end,"sample_rate":audio.sample_rate,
                "pcm_f64_le_sha256":hex::encode(hash.finalize()),"baud":baud,"threads":threads,
                "reference":"adaptive-plus-innovations; NOT full progressive",
                "lanes":["matched-point","bcjr-gaussian"],
                "noise":"transferred source residual variance, scaled with gain squared; not field-calibrated",
                "same_anchor_timing_gain_bank":true,"target_truth_used":false,
                "crc_guided_optimization":false,"fec_feedback":false,"gpu":false,
                "timing_scope":"shared-host diagnostics, no isolated speedup claim"
            }),
        )?;
        let report = uncertainty_audio::decode_bcjr_samples(samples, audio.sample_rate, &config)?;
        let lane_frames: std::collections::BTreeMap<_, _> = report
            .lane_full_frames
            .iter()
            .map(|(name, frames)| (name, frames.len()))
            .collect();
        let summary = json!({
            "schema":"framelift-bcjr-audio-summary-v1","status":"complete","publication_ready":false,
            "reference_frames":report.reference.union_full_frames.len(),"lane_frames":lane_frames,
            "union_frames":report.union_full_frames.len(),
            "added_union":report.added_union_vs_reference.len(),"lost_union":report.lost_union_vs_reference.len(),
            "added_vs_matched_point":report.added_vs_matched_point,"lost_vs_matched_point":report.lost_vs_matched_point,
            "sources":report.sources.len(),"source_rejections":report.source_rejections.len(),
            "trials":report.trials.len(),"skips":report.skips.len(),"decode_seconds":report.total_decode_seconds
        });
        input::write_json_new(&out.join("report.json"), &report)?;
        input::write_json_new(&out.join("summary.json"), &summary)?;
        Ok(summary)
    })();
    if let Err(reason) = &result {
        input::write_json_new(
            &out.join("failure.json"),
            &json!({"status":"failed","reason":reason}),
        )?;
    }
    result
}
