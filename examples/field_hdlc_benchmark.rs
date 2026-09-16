//! Full-recording development replay of two already exposed CANVAS IQ sources.
//! Every fixed overlapping window is attempted; windows are not independent units.
#[allow(dead_code, clippy::type_complexity, clippy::collapsible_if)]
#[path = "support/innovation_acquire_io.rs"]
mod io;
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::Instant,
};
use telemetry_yield_rs::{
    archive::baselines, generic, input, recovery_guard::Guard, recovery_hdlc,
};
#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    old: PathBuf,
}
fn hash(v: &impl serde::Serialize) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(v).map_err(|e| e.to_string())?,
    )))
}
fn cpu() -> f64 {
    let mut u: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut u) };
    u.ru_utime.tv_sec as f64
        + u.ru_stime.tv_sec as f64
        + (u.ru_utime.tv_usec + u.ru_stime.tv_usec) as f64 / 1e6
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
fn run(args: Args) -> Result<(), String> {
    fs::create_dir(&args.output).map_err(|e| e.to_string())?;
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"));
    let runtime = Guard::open(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let profile = repo.join("configs/protocols/field-canvas-gmsk9600.yml");
    let profile_guard = Guard::open(&profile)?;
    let old_guard = Guard::open(&args.old)?;
    let gr_guard = Guard::open(Path::new("/usr/bin/gr_satellites"))?;
    let verify_runtime = || -> Result<(), String> {
        runtime.verify()?;
        profile_guard.verify()?;
        old_guard.verify()?;
        gr_guard.verify()?;
        Ok(())
    };
    let mut config:recovery_hdlc::Config=serde_json::from_value(json!({
  "waveform":{"hypothesis_id":"canvas-gmsk9600","demodulator_id":"phase_fsk","dsp":{"baud":9600,"mode":"fsk","rate_errors_ppm":[0],"phase_bins":8,"top_timing":8,"bank":"full"},"decimation":1,"cutoff_hz":7200,"carrier_hz":null,"mark_hz":1200,"space_hz":2200,"psk":null},
  "line_coding":"nrzi","scrambler":"g3ruh","sequence":null,"workers":4,"work_budget":2000000000
 })).map_err(|e|e.to_string())?;
    let mut sources = Vec::new();
    for id in [14115025_u64, 14366383] {
        let path = repo.join(format!(
            "work/public-iq/canvas-{id}/satnogs_iq_{id}_437250000_57600.sigmf-data"
        ));
        let guard = Guard::open(&path)?;
        sources.push((id, path, guard));
    }
    let old_id = input::identity(&args.old)?;
    let gr_id = input::identity(Path::new("/usr/bin/gr_satellites"))?;
    input::write_json_new(
        &args.output.join("amendment.json"),
        &json!({"schema":"framelift-field-hdlc-amendment-v2","reason":"Use four workers for both native arms, held-descriptor guards for external executables and the profile, and shared window bounds. No DSP parameters or inputs changed.", "previous_complete_results_retained":true, "timings_still_concurrent_host":true}),
    )?;
    input::write_json_new(
        &args.output.join("freeze.json"),
        &json!({"schema":"framelift-field-hdlc-development-v1","frozen_utc":io::utc(),"runner":runtime.identity(),"old":old_id,"external":gr_id,"sources":sources.iter().map(|(id,_,g)|json!({"id":id,"input":g.identity()})).collect::<Vec<_>>(),"profile":input::identity(&profile)?,"receiver":config,"window_samples":921600,"hop_samples":864000,"sample_rate_hz":57600,"selection":"Both existing work/public-iq CANVAS recordings, entire files, no outcome-based cropping","exposure":"development","arms":["old_generic","new_baseline","new_sequence","gr_satellites"],"normalization_note":"Original CI16 bytes are identical. Native IQ converts integers to float64; gr-satellites rawint16 converts to float32 divided by 32767 internally. No shared resampling or preprocessing is applied.","runtime_closure_sealed":false}),
    )?;
    let mut observations = Vec::new();
    for (id, path, guard) in sources {
        let out = args.output.join(format!("obs-{id}"));
        fs::create_dir(&out).map_err(|e| e.to_string())?;
        let count = guard.identity().bytes as usize / 4;
        let mut arms = Vec::new();
        for name in ["new_baseline", "new_sequence"] {
            let lane = out.join(name);
            fs::create_dir(&lane).map_err(|e| e.to_string())?;
            config.sequence = if name == "new_sequence" {
                Some(recovery_hdlc::SequenceConfig {
                    maximum_training_frames: 8,
                })
            } else {
                None
            };
            config.workers = 4;
            verify_runtime()?;
            let start = Instant::now();
            let cpu_start = cpu();
            let mut frames = BTreeMap::new();
            let mut errors = Vec::new();
            let mut windows = 0;
            for (start_sample, end) in
                telemetry_yield_rs::receiver::window_bounds(count, 57600, 16., 15.)?
            {
                let iq = guard.read_iq_window(
                    &generic::InputFormat::Ci16Le,
                    start_sample,
                    end - start_sample,
                )?;
                match recovery_hdlc::decode(&iq, 57600, &config, &guard.identity().sha256) {
                    Ok(report) => {
                        for frame in &report.frames {
                            let raw = hex::decode(&frame.hex).map_err(|e| e.to_string())?;
                            let payload = hex::encode(&raw[..raw.len() - 2]);
                            frames.insert(payload.clone(),json!({"payload_hex":payload,"validation":{"type":"received_ax25_fcs","frame_hex":frame.hex}}));
                        }
                        guard.verify()?;
                        verify_runtime()?;
                        input::write_json_new(
                            &lane.join(format!("window-{windows:03}.json")),
                            &json!({"start_sample":start_sample,"report":report}),
                        )?;
                    }
                    Err(error) => {
                        errors.push(json!({"start_sample":start_sample,"error":error}));
                    }
                }
                windows += 1;
            }
            let status = if errors.is_empty() {
                "completed"
            } else {
                "failed"
            };
            input::write_json_new(
                &lane.join("windows-summary.json"),
                &json!({"windows":windows,"errors":errors,"partial_unique_frames":frames.len()}),
            )?;
            let arm = json!({"name":name,"status":status,"input_sha256":guard.identity().sha256,"runtime_sha256":runtime.identity().sha256,"profile_sha256":hash(&config)?,"wall_seconds":start.elapsed().as_secs_f64(),"cpu_seconds":cpu()-cpu_start,"error":if errors.is_empty(){Value::Null}else{json!(serde_json::to_string(&errors).map_err(|e|e.to_string())?)},"frames":if status=="completed"{frames.into_values().collect::<Vec<_>>()}else{vec![]}});
            input::write_json_new(&lane.join("arm.json"), &arm)?;
            arms.push(arm);
        }
        let plan = json!({"format":"ci16_le","sample_rate_hz":57600,"window_seconds":16,"hop_seconds":15,"protocols":{"ax25":{"type":"ax25","g3ruh_modes":[true]}},"hypotheses":[{"waveform":config.waveform,"protocol_id":"ax25"}]});
        let plan_path = out.join("generic-plan.json");
        input::write_json_new(&plan_path, &plan)?;
        for name in ["old_generic", "gr_satellites"] {
            let lane = out.join(name);
            fs::create_dir(&lane).map_err(|e| e.to_string())?;
            let (program, cmd) = if name == "old_generic" {
                (
                    args.old.as_path(),
                    vec![
                        "decode".into(),
                        "--input".into(),
                        path.display().to_string(),
                        "--plan".into(),
                        plan_path.display().to_string(),
                        "--output".into(),
                        lane.join("decode").display().to_string(),
                        "--threads".into(),
                        "4".into(),
                    ],
                )
            } else {
                (
                    Path::new("/usr/bin/gr_satellites"),
                    vec![
                        profile.display().to_string(),
                        "--rawint16".into(),
                        path.display().to_string(),
                        "--samp_rate".into(),
                        "57600".into(),
                        "--iq".into(),
                        "--deviation".into(),
                        "2400".into(),
                        "--kiss_out".into(),
                        lane.join("frames.kiss").display().to_string(),
                        "--hexdump".into(),
                    ],
                )
            };
            verify_runtime()?;
            let receipt = io::execute(program, &cmd, &lane, "receiver", 1800, 512 * 1024 * 1024)?;
            guard.verify()?;
            verify_runtime()?;
            let result = (|| -> Result<Vec<Value>, String> {
                io::require_success(&receipt)?;
                if name == "gr_satellites" {
                    let p = baselines::parse_gr_satellites_kiss(
                        &fs::read(lane.join("frames.kiss")).map_err(|e| e.to_string())?,
                    )?;
                    Ok(p.strict_ui_payloads.into_iter().map(|h|json!({"payload_hex":h,"validation":{"type":"external_decoder_attested","decoder":"gr_satellites"}})).collect())
                } else {
                    let r = input::read_json(&lane.join("decode/result.json"))?;
                    if r["status"] != "complete" || r["failed_window_count"] != 0 {
                        return Err("old generic run contains incomplete windows".into());
                    }
                    let frames = r["frames"].as_array().ok_or("generic frames absent")?;
                    let mut unique = BTreeMap::new();
                    for f in frames {
                        let h = f["frame_hex"].as_str().ok_or("frame")?;
                        let bytes = hex::decode(h).map_err(|e| e.to_string())?;
                        if bytes.len() < 2 {
                            return Err("frame is shorter than its FCS".into());
                        }
                        if !telemetry_yield_rs::protocol::valid_ax25_fcs(&bytes)
                            || telemetry_yield_rs::protocol::parse_ax25_ui(
                                &bytes[..bytes.len() - 2],
                            )
                            .is_none()
                        {
                            return Err("invalid old generic AX25 frame".into());
                        }
                        let p = hex::encode(&bytes[..bytes.len() - 2]);
                        unique.insert(p.clone(),json!({"payload_hex":p,"validation":{"type":"received_ax25_fcs","frame_hex":h}}));
                    }
                    Ok(unique.into_values().collect())
                }
            })();
            let (status, frames, error) = match result {
                Ok(f) => ("completed", f, Value::Null),
                Err(e) => (
                    if receipt["timed_out"] == true {
                        "timed_out"
                    } else {
                        "failed"
                    },
                    vec![],
                    json!(e),
                ),
            };
            let arm = json!({"name":name,"status":status,"input_sha256":guard.identity().sha256,"runtime_sha256":if name=="old_generic"{&old_id.sha256}else{&gr_id.sha256},"profile_sha256":hash(&cmd)?,"wall_seconds":receipt["wall_seconds"],"cpu_seconds":receipt["user_cpu_seconds"].as_f64().zip(receipt["system_cpu_seconds"].as_f64()).map(|(a,b)|a+b),"error":error,"frames":frames});
            input::write_json_new(&lane.join("arm.json"), &arm)?;
            arms.push(arm);
        }
        let observation = json!({"id":id.to_string(),"mission":"CANVAS-IQ","station":106,"pass_group":id.to_string(),"input_sha256":guard.identity().sha256,"exposure":"development","confirmed_signal":null,"signal_evidence":null,"negative_control":false,"duration_seconds":count as f64/57600.,"arms":arms});
        input::write_json_new(&out.join("observation.json"), &observation)?;
        observations.push(observation);
    }
    input::write_json_new(
        &args.output.join("study.json"),
        &json!({"schema":"framelift-recovery-study-v1","baseline_arm":"new_baseline","candidate_arms":["new_sequence","old_generic","gr_satellites"],"observations":observations}),
    )
}
