//! Fail-closed numerical backend qualification, separate from scientific yield.
//! The serial oracle deliberately does not call the production FIR engine.
use super::current;
use crate::input;
use num_complex::Complex64;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::time::Instant;

/// Controlled end-to-end benchmark. The parent never initializes CUDA; every
/// probe and decoder is a separate exec of the exact same hashed binary.
pub fn benchmark_pair(
    source: &Path,
    out: &Path,
    candidate: super::Backend,
    candidate_threads: usize,
    threads: usize,
    repeats: usize,
    resources: &super::Options,
) -> Result<Value, String> {
    if !(1..=10).contains(&repeats) || !(1..=16).contains(&threads) {
        return Err("benchmark repeats 1..10 and decoder threads 1..16 required".into());
    }
    let options = super::Options {
        backend: candidate,
        cpu_threads: candidate_threads,
        ..resources.clone()
    };
    options.validate()?;
    let source = source.canonicalize().map_err(|e| e.to_string())?;
    let source_id = input::identity(&source)?;
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let executable_id = input::identity(&executable)?;
    std::fs::create_dir(out)
        .map_err(|e| format!("benchmark requires a new output directory: {e}"))?;
    let out = out.canonicalize().map_err(|e| e.to_string())?;
    let config = json!({"source":source_id,"executable":executable_id,"candidate":options,"decoder_threads":threads,"repeats":repeats,
        "mode":"full","hypothesis_bank":"unchanged; blind and multi-anchor enabled","ordering":"alternating CPU-first/candidate-first per repeat",
        "per_child_timeout_seconds":3600,"scope":"development same-source backend regression, not independent holdout"});
    input::write_json_new(&out.join("registration.json"), &config)?;
    let mut attempts = Vec::new();
    let mut audits = Vec::new();
    let execute = (|| -> Result<(), String> {
        // Fail before spending time on the reference if the selected backend is unavailable.
        let candidate_name = if candidate == super::Backend::Cuda {
            "cuda"
        } else {
            "cpu"
        };
        let resource_args = vec![
            "--cuda-device".into(),
            options.cuda_device.to_string(),
            "--cuda-streams".into(),
            options.cuda_streams.to_string(),
            "--cuda-buffer-mib".into(),
            options.cuda_buffer_mib.to_string(),
        ];
        let mut probe = vec![
            "--compute".into(),
            candidate_name.into(),
            "--compute-threads".into(),
            candidate_threads.to_string(),
            "compute-info".into(),
        ];
        probe.extend(resource_args.clone());
        let probe_start = Instant::now();
        let probe_output = input::external_with_file_limit(
            executable.to_str().ok_or("non-UTF8 executable path")?,
            &probe,
            120,
            64 * 1024 * 1024,
        )?;
        let info: Value = serde_json::from_slice(&probe_output).map_err(|e| e.to_string())?;
        input::write_json_new(
            &out.join("candidate-probe.json"),
            &json!({"wall_seconds":probe_start.elapsed().as_secs_f64(),"compute":info}),
        )?;
        for repeat in 0..repeats {
            let cpu_dir = out.join(format!("r{repeat}-cpu"));
            let candidate_dir = out.join(format!("r{repeat}-candidate"));
            for is_candidate in if repeat % 2 == 0 {
                [false, true]
            } else {
                [true, false]
            } {
                let target = if is_candidate {
                    &candidate_dir
                } else {
                    &cpu_dir
                };
                let backend = if is_candidate { candidate_name } else { "cpu" };
                let workers = if is_candidate { candidate_threads } else { 1 };
                let mut args = vec![
                    "--compute".into(),
                    backend.into(),
                    "--compute-threads".into(),
                    workers.to_string(),
                    "decode-progressive".into(),
                    "--input".into(),
                    source.to_str().ok_or("non-UTF8 source")?.into(),
                    "--output".into(),
                    target.to_str().ok_or("non-UTF8 output")?.into(),
                    "--mode".into(),
                    "full".into(),
                    "--threads".into(),
                    threads.to_string(),
                ];
                args.extend(resource_args.clone());
                let begin = Instant::now();
                let output = input::external_with_file_limit(
                    executable.to_str().unwrap(),
                    &args,
                    3600,
                    2 * 1024 * 1024 * 1024,
                )?;
                let seconds = begin.elapsed().as_secs_f64();
                let receipt: Value = serde_json::from_slice(&output).map_err(|e| e.to_string())?;
                let attempt = json!({"repeat":repeat,"candidate":is_candidate,"argv":args,"outer_wall_seconds":seconds,"receipt":receipt});
                input::write_json_new(
                    &out.join(format!(
                        "r{repeat}-{}.json",
                        if is_candidate { "candidate" } else { "cpu" }
                    )),
                    &attempt,
                )?;
                attempts.push(attempt);
                if receipt["complete"] != true
                    || receipt["worker_success"] != true
                    || receipt["cancelled"] != false
                    || receipt["budget_exhausted"] != false
                {
                    return Err(
                        "benchmark decoder incomplete, failed, cancelled or timed out".into(),
                    );
                }
            }
            let audit_path = out.join(format!("r{repeat}-parity.json"));
            crate::progressive::compute_audit::run(&cpu_dir, &candidate_dir, &audit_path)?;
            audits.push(input::identity(&audit_path)?);
        }
        if input::identity(&source)?.sha256 != source_id.sha256
            || input::identity(&executable)?.sha256 != executable_id.sha256
        {
            return Err("input or executable changed during benchmark".into());
        }
        Ok(())
    })();
    let failure = execute.err();
    let report = json!({"schema":"telemetry-compute-end-to-end-benchmark-v1","pass":failure.is_none(),
        "status":if failure.is_none() {"passed"} else {"failed"},"error":failure,"registration":config,
        "attempts":attempts,"parity_audits":audits,"shared_host_load_not_excluded":true,
        "timing_scope":"outer process wall time including startup/JIT, input conversion, hashes, all DSP tasks, checkpoint IO and teardown; probe reported separately",
        "publication_ready":false,"deployment_ready":false});
    input::write_json_new(&out.join("summary.json"), &report)?;
    if let Some(error) = failure {
        return Err(format!(
            "benchmark failed: {error}; evidence {}",
            out.display()
        ));
    }
    Ok(report)
}

fn digest(values: &[f64]) -> String {
    let mut hash = Sha256::new();
    for v in values {
        hash.update(v.to_bits().to_le_bytes());
    }
    hex::encode(hash.finalize())
}
fn oracle(values: &[f64], taps: &[f64], d: usize, centered: bool) -> Vec<f64> {
    if !centered && values.len() < taps.len() {
        return vec![];
    }
    let first = if centered {
        (taps.len() - 1) / 2
    } else {
        taps.len() - 1
    };
    let n = if centered {
        values.len()
    } else {
        (values.len() - taps.len()) / d + 1
    };
    (0..n)
        .map(|i| {
            let mut sum = 0.0;
            for (j, tap) in taps.iter().enumerate() {
                if let Some(k) = (first + i * d).checked_sub(j).filter(|&k| k < values.len()) {
                    sum += tap * values[k];
                }
            }
            sum
        })
        .collect()
}

fn check(
    name: &str,
    values: &[f64],
    taps: &[f64],
    d: usize,
    complex: bool,
    centered: bool,
    repeats: usize,
) -> Result<Value, String> {
    let imaginary: Vec<f64> = values.iter().rev().map(|x| -x).collect();
    let iq: Vec<_> = values
        .iter()
        .zip(&imaginary)
        .map(|(&re, &im)| Complex64::new(re, im))
        .collect();
    let reference = || {
        let re = oracle(values, taps, d, centered);
        if complex {
            let im = oracle(&imaginary, taps, d, centered);
            re.into_iter()
                .zip(im)
                .flat_map(|(re, im)| [re, im])
                .collect()
        } else {
            re
        }
    };
    let accelerated = || -> Result<Vec<f64>, String> {
        if complex {
            Ok(current()
                .fir_complex(&iq, taps, d, centered)?
                .iter()
                .flat_map(|x| [x.re, x.im])
                .collect())
        } else {
            current().fir_real(values, taps, d)
        }
    };
    let cold_start = Instant::now();
    let cold = accelerated()?;
    let cold_seconds = cold_start.elapsed().as_secs_f64();
    let expected = reference();
    let expected_sha256 = digest(&expected);
    let mut pass = digest(&cold) == expected_sha256;
    let mut timings = Vec::new();
    for i in 0..repeats {
        // Alternate execution order to reduce systematic warm/cache bias.
        let mut reference_seconds = 0.0;
        let mut accelerated_seconds = 0.0;
        for candidate in if i % 2 == 0 {
            [false, true]
        } else {
            [true, false]
        } {
            let start = Instant::now();
            let actual = if candidate {
                accelerated()?
            } else {
                reference()
            };
            let seconds = start.elapsed().as_secs_f64();
            // Hashing intentionally outside the timed section.
            pass &= digest(&actual) == expected_sha256 && actual.len() == expected.len();
            if candidate {
                accelerated_seconds = seconds;
            } else {
                reference_seconds = seconds;
            }
        }
        timings.push(
            json!({"serial_seconds":reference_seconds,"candidate_seconds":accelerated_seconds}),
        );
    }
    Ok(
        json!({"case":name,"input_f64_sha256":digest(values),"taps_f64_sha256":digest(taps),
        "input_samples":values.len(),"taps":taps.len(),"decimation":d,"complex":complex,"centered":centered,
        "output_scalar_count":expected.len(),"expected_output_f64_sha256":expected_sha256,
        "bit_exact":pass,"cold_operation_seconds":cold_seconds,"paired_repeats":timings}),
    )
}

pub fn run(source: Option<&Path>, repeats: usize, out: &Path) -> Result<Value, String> {
    if !(1..=20).contains(&repeats) {
        return Err("qualification repeats must be in 1..=20".into());
    }
    if out.exists() {
        return Err("qualification output must be a new path".into());
    }
    let mut rows = Vec::new();
    for n in [0, 1, 2, 17, 257, 8193, 250_003] {
        let values: Vec<f64> = (0..n)
            .map(|i| ((i * 127 % 1031) as f64 - 515.0) / 317.0)
            .collect();
        for taps in [vec![1.0], vec![-0.0, 0.25, -0.125], vec![1.0 / 115.0; 115]] {
            for (d, complex, centered) in [
                (1, false, false),
                (3, false, false),
                (2, true, false),
                (1, true, true),
            ] {
                rows.push(check(
                    "deterministic_boundary",
                    &values,
                    &taps,
                    d,
                    complex,
                    centered,
                    repeats,
                )?);
            }
        }
    }
    // Small signed-zero/subnormal/cancellation probes, without overflow.
    let adversarial = [
        0.0,
        -0.0,
        f64::MIN_POSITIVE,
        -f64::MIN_POSITIVE,
        f64::from_bits(1),
        -f64::from_bits(1),
        1e90,
        -1e90,
        1.0,
        -1.0,
    ];
    for (complex, centered) in [(false, false), (true, false), (true, true)] {
        rows.push(check(
            "numeric_edges",
            &adversarial,
            &[0.5, -0.5, 0.125],
            1,
            complex,
            centered,
            repeats,
        )?);
    }
    let source_identity = if let Some(path) = source {
        let scratch = tempfile::tempdir().map_err(|e| e.to_string())?;
        let audio = input::load_audio(path, scratch.path())?;
        // Full waveform, explicit bounded chunks; all source samples are covered.
        for (index, chunk) in audio.samples.chunks(262_144).enumerate() {
            let taps = crate::dsp::firwin(
                115,
                0.0,
                0.15 * audio.sample_rate as f64,
                audio.sample_rate as f64,
            );
            rows.push(check(
                &format!("real_audio_chunk_{index}"),
                chunk,
                &taps,
                2,
                false,
                false,
                repeats,
            )?);
        }
        Some(
            json!({"source":audio.source_identity,"decoded_wav":audio.wav_identity,
            "sample_rate":audio.sample_rate,"samples":audio.samples.len(),
            "conversion_command":audio.conversion_command,
            "scope":"FIR chunk qualification; independent chunk boundaries are NOT end-to-end demodulation"}),
        )
    } else {
        None
    };
    let pass = rows.iter().all(|r| r["bit_exact"] == true);
    let report = json!({"schema":"telemetry-compute-qualification-v1","status":if pass {"passed"} else {"failed"},
        "pass":pass,"bit_exact":pass,"cases":rows,"source":source_identity,
        "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "compute":current().report(),"repeats":repeats,
        "timing_scope":"operation plus allocation and CUDA transfer/synchronization; source I/O, initial context/JIT, checksum and report writes excluded; shared host not excluded",
        "publication_ready":false,"deployment_ready":false,
        "remaining_gates":["full receiver frame/task parity on the same recordings","independent held-out multi-satellite cohort","noise and malformed-input controls","isolated end-to-end cost and latency","deployment soak and fault injection"]});
    input::write_json_new(out, &report)?;
    if !pass {
        return Err(format!(
            "compute qualification failed; retained evidence {}",
            out.display()
        ));
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independently_hashes_float_bits_and_rejects_overwrite() {
        assert_ne!(digest(&[0.0]), digest(&[-0.0]));
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("report.json");
        std::fs::write(&path, b"sentinel").unwrap();
        assert!(run(None, 1, &path).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"sentinel");
        assert!(run(None, 0, &tmp.path().join("new.json")).is_err());
    }
}
