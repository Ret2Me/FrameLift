//! Matched in-memory decoding boundaries; timings are not publication claims.
use crate::{input, receiver};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::time::Instant;

pub fn audio(
    path: &Path,
    output: &Path,
    base: &receiver::DecodeConfig,
    workers: &[usize],
    repetitions: usize,
    seconds: Option<f64>,
) -> Result<Value, String> {
    if workers.is_empty()
        || workers.len() > 8
        || workers.iter().any(|n| !(1..=64).contains(n))
        || !(1..=10).contains(&repetitions)
    {
        return Err("benchmark requires1..8 worker settings(1..64),1..10 repetitions".into());
    }
    if seconds.is_some_and(|s| !s.is_finite() || s <= 0.0 || s > 1800.0) {
        return Err("invalid benchmark segment duration".into());
    }
    let out = input::existing_new_dir(output)?;
    let audio = input::load_audio(path, &out)?;
    let count = seconds.map_or(audio.samples.len(), |s| {
        ((s * audio.sample_rate as f64) as usize).min(audio.samples.len())
    });
    let samples = &audio.samples[..count];
    let mut digest = Sha256::new();
    for sample in samples {
        digest.update(sample.to_le_bytes());
    }
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({"source":audio.source_identity,"wav":audio.wav_identity,"executable":executable,
        "sample_rate_hz":audio.sample_rate,"sample_count":count,"pcm_f64le_sha256":hex::encode(digest.finalize()),
        "config":base,"workers":workers,"repetitions":repetitions,"boundary":"loaded finite samples -> frames, ordered journal and stage diagnostics; no file I/O or noise controls"}),
    )?;
    let mut reference: Option<Value> = None;
    let mut rows = Vec::new();
    // Rotate order per repetition to reduce systematic warm-cache/order bias.
    for repetition in 0..repetitions {
        for index in 0..workers.len() {
            let threads = workers[(index + repetition) % workers.len()];
            let mut config = base.clone();
            config.threads = threads;
            let wall = Instant::now();
            let cpu = cpu_seconds()?;
            let (frames, windows, stages) =
                receiver::decode_samples(samples, audio.sample_rate, &config)?;
            let wall_seconds = wall.elapsed().as_secs_f64();
            let cpu_seconds = cpu_seconds()? - cpu;
            let content = json!({"frames":frames,"windows":windows});
            let equal = reference.as_ref().is_none_or(|r| r == &content);
            if reference.is_none() {
                input::write_json_new(&out.join("reference-output.json"), &content)?;
                reference = Some(content);
            }
            let no_failure = windows.iter().all(|w| w.failures.is_empty());
            let row = json!({"repetition":repetition,"threads":threads,"wall_seconds":wall_seconds,"process_cpu_seconds":cpu_seconds,
                "unique_pdu_count":frames.len(),"window_count":windows.len(),"exact_output_equal":equal,"no_window_failure":no_failure,"stage_worker_wall_seconds_sum":stages});
            input::write_json_new(&out.join(format!("run-{repetition}-{index}.json")), &row)?;
            rows.push(row);
        }
    }
    let result = json!({"schema":"rust-matched-audio-benchmark-v1","pass":rows.iter().all(|r|r["exact_output_equal"]==true&&r["no_window_failure"]==true),
        "runs":rows,"sample_rate_hz":audio.sample_rate,"sample_count":count,"single_input_shared_host":true,
        "note":"Thread equality includes full frame bytes, FCS, floating scores and ordered provenance. Host load is not isolated."});
    input::write_json_new(&out.join("result.json"), &result)?;
    Ok(result)
}

fn cpu_seconds() -> Result<f64, String> {
    #[cfg(unix)]
    {
        let mut time = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        // SAFETY: time points to writable timespec storage for clock_gettime.
        if unsafe { libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut time) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(time.tv_sec as f64 + time.tv_nsec as f64 * 1e-9)
    }
    #[cfg(not(unix))]
    {
        Err("process CPU benchmark requires POSIX clock_gettime".into())
    }
}
