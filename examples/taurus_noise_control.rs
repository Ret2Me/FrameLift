//! One pinned, offline, same-profile noise control for LilacSat-1 main output.
#[allow(dead_code)]
#[path = "support/holdout_run_io.rs"]
mod io;
use serde_json::{Value, json};
use std::{collections::BTreeSet, fs, path::Path};
use telemetry_yield_rs::{formats, input};

fn main() -> Result<(), String> {
    let out = Path::new(
        "/home/ubuntu/telemetry-yield/work/taurus-validation-20260911-v1/white-noise-600s-v1",
    );
    let source = Path::new(
        "/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/negative-white-600s.wav",
    );
    let source_id = input::identity(source)?;
    if source_id.sha256 != "271761e41c34cf8c032961cdc90511f8f822a7d34a11af083c39881261a4107f" {
        return Err("noise source changed".into());
    }
    let wav = hound::WavReader::open(source).map_err(|e| e.to_string())?;
    if wav.spec().sample_rate != 48000 || wav.spec().channels != 1 || wav.len() != 48000 * 600 {
        return Err("not a complete mono48kHz600s control".into());
    }
    let original_profile = Path::new(
        "/home/ubuntu/telemetry-yield/work/decoder-runtime-repair-20260911-v1/gr-profile-extension-retry-v2/obs-3208/gr_satellites/selected-profile-main-output-only.yml",
    );
    let original_profile_id = input::identity(original_profile)?;
    if original_profile_id.sha256
        != "74ce4dda666812dd25ec21f220afc45de255baf3d611458f1c147f190eab6eaf"
    {
        return Err("Taurus control profile changed".into());
    }
    let executable = Path::new("/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites");
    let executable_id = input::identity(executable)?;
    if executable_id.sha256 != "86059e664af0e12ade1a5c3c52a7c14dc8fbe87f3febafdee1b1626e1f4827f7" {
        return Err("reference executable changed".into());
    }
    fs::create_dir(out).map_err(|e| e.to_string())?;
    let gr = out.join("gr_satellites");
    fs::create_dir(&gr).map_err(|e| e.to_string())?;
    let profile = gr.join("selected-profile-main-output-only.yml");
    fs::copy(original_profile, &profile).map_err(|e| e.to_string())?;
    let args = vec![
        profile.display().to_string(),
        "--wavfile".into(),
        source.display().to_string(),
        "--kiss_out".into(),
        gr.join("frames.kiss").display().to_string(),
        "--hexdump".into(),
    ];
    input::write_json_new(
        &out.join("plan.json"),
        &json!({"schema":"taurus-noise-control-plan-v1",
        "source":source_id,"reference_executable":executable_id,"args":args,
        "noise_recipe":"ffmpeg anoisesrc=r=48000:a=0.2:d=600:seed=2026091101:color=white",
        "profile":original_profile_id,"timeout_seconds":180,"submission_disabled":true}),
    )?;
    let process = io::execute(executable, &args, &gr, "run", 180, 64 * 1024 * 1024)?;
    io::require_success(&process)?;
    if json!(input::identity(source)?) != json!(source_id)
        || json!(input::identity(executable)?) != json!(executable_id)
        || json!(input::identity(original_profile)?) != json!(original_profile_id)
    {
        return Err("bound control input or executable changed during run".into());
    }
    let capture =
        formats::parse_kiss(&fs::read(gr.join("frames.kiss")).map_err(|e| e.to_string())?)?;
    if capture.malformed_records != 0
        || !capture.other_commands.is_empty()
        || capture
            .data_frames
            .iter()
            .any(|r| r.port != 0 || r.payload.len() != 81)
    {
        return Err("control produced malformed/unsupported outer stream".into());
    }
    let unique: BTreeSet<String> = capture
        .data_frames
        .iter()
        .map(|r| hex::encode(&r.payload))
        .collect();
    let result: Value = json!({"schema":"taurus-noise-control-result-v1","status":"complete",
        "observation_id":"synthetic-white-noise-600s","source":source_id,"shared_input":source_id,
        "arms":{"gr_satellites":{"status":"complete","process":process,"executable":executable_id,
            "profile":{"candidate":input::identity(&profile)?},"candidate_pdus":unique,
            "unique_count":unique.len(),"raw_main_chunk_count":capture.data_frames.len()}},
        "new_telemetry_claim":false,"independent_crc_checked":false,"publication_ready":false});
    input::write_json_new(&out.join("result.json"), &result)?;
    println!(
        "{}",
        json!({"status":"complete","raw_chunks":capture.data_frames.len(),"unique_chunks":unique.len()})
    );
    Ok(())
}
