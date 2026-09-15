//! Exact-input, bounded replay of four diagnosed runtime failures, not a campaign.
#[allow(dead_code)]
#[path = "support/today20_baselines.rs"]
mod baselines;
#[allow(dead_code)]
#[path = "support/holdout_run_io.rs"]
mod io;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::{input, protocol};

const PRIOR: &str = "/home/ubuntu/telemetry-yield/work/polyitan-audio-all-20260910-v1/analysis";
const OUT: &str = "/home/ubuntu/telemetry-yield/work/decoder-runtime-repair-20260911-v1";
fn identity(p: &Path) -> Value {
    json!(input::identity(p).unwrap())
}
fn read(p: &Path) -> Value {
    input::read_json(p).unwrap()
}
fn verify(v: &Value) {
    let actual = identity(Path::new(v["path"].as_str().unwrap()));
    assert_eq!(actual, *v);
}
fn save(p: &Path, v: &Value) {
    input::write_json_new(p, v).unwrap();
}
fn replace_option(args: &mut [String], name: &str, value: &Path) {
    let i = args.iter().position(|v| v == name).unwrap();
    args[i + 1] = value.display().to_string();
}
fn fcs_frames(values: &Value, objects: bool) -> BTreeSet<String> {
    values
        .as_array()
        .unwrap()
        .iter()
        .map(|v| {
            let full = if objects {
                v["frame_with_fcs_hex"].as_str().unwrap()
            } else {
                v.as_str().unwrap()
            };
            let bytes = hex::decode(full).unwrap();
            assert!(bytes.len() >= 18);
            let mut crc = 0xffffu16;
            for x in &bytes[..bytes.len() - 2] {
                crc ^= *x as u16;
                for _ in 0..8 {
                    crc = if crc & 1 == 1 {
                        (crc >> 1) ^ 0x8408
                    } else {
                        crc >> 1
                    };
                }
            }
            assert_eq!(
                !crc,
                u16::from_le_bytes([bytes[bytes.len() - 2], bytes[bytes.len() - 1]])
            );
            assert!(protocol::valid_ax25_ui(&bytes[..bytes.len() - 2]));
            full.to_owned()
        })
        .collect()
}
fn run(id: u64, output_root: &Path) -> Value {
    let original = Path::new(PRIOR).join(format!("obs-{id}"));
    let root = output_root.join(format!("obs-{id}"));
    fs::create_dir(&root).unwrap();
    let prior_path = original.join("result.json");
    let prior_identity = identity(&prior_path);
    let prior = read(&prior_path);
    assert_eq!(prior["status"], "partially_complete");
    assert_eq!(prior["observation_id"], id);
    verify(&prior["source"]);
    verify(&prior["metadata"]);
    let wav = root.join("shared.wav");
    let mut convert: Vec<String> =
        serde_json::from_value(prior["conversion"]["args"].clone()).unwrap();
    *convert.last_mut().unwrap() = wav.display().to_string();
    let preparation = io::execute(
        Path::new("/usr/bin/ffmpeg"),
        &convert,
        &root,
        "prepare",
        180,
        256 * 1024 * 1024,
    )
    .unwrap();
    io::require_success(&preparation).unwrap();
    let shared = identity(&wav);
    assert_eq!(shared["sha256"], prior["input"]["sha256"]);
    assert_eq!(shared["bytes"], prior["input"]["bytes"]);
    let mut summary = json!({"schema":"runtime-repair-replay-v1","observation_id":id,"prior_result":prior_identity,
        "prior_plan_sha256":prior["plan_sha256"],"source":prior["source"],"shared_input":shared,
        "exact_prior_pcm_bytes_verified":true,"preparation":preparation,"arms":{},
        "normalization_policy":"unchanged q90 when >1e-12; exact zero residual empty; otherwise finite nonzero peak fallback",
        "publication_ready":false,"deployment_ready":false,"new_telemetry_claim":false});
    for name in ["native_audio", "innovation_v2", "gr_satellites"] {
        let old = &prior["arms"][name];
        if old["status"] != "failed_or_incomplete" {
            continue;
        }
        let arm = root.join(name);
        fs::create_dir(&arm).unwrap();
        let mut args: Vec<String> = serde_json::from_value(old["process"]["args"].clone()).unwrap();
        let program = match name {
            "native_audio" => Path::new(OUT).join("bin/telemetry-yield-rs"),
            "innovation_v2" => Path::new(OUT).join("bin/innovation_audio_probe"),
            _ => PathBuf::from(old["process"]["program"].as_str().unwrap()),
        };
        let mut profile_info = Value::Null;
        if name == "gr_satellites" {
            let previous = Path::new(&args[0]);
            let mut profile = read(previous);
            let txs = profile["transmitters"].as_object_mut().unwrap();
            let mut removed = vec![];
            for (tx_name, tx) in txs {
                if let Some(v) = tx.as_object_mut().unwrap().remove("additional_data") {
                    removed.push(json!({"transmitter":tx_name,"removed_application_sinks":v}));
                }
            }
            assert!(!removed.is_empty());
            let profile_path = arm.join("selected-profile-main-output-only.yml");
            save(&profile_path, &profile);
            profile_info = json!({"prior":identity(previous),"candidate":identity(&profile_path),"changes":removed,
                "scope":"preserve all main telemetry DSP/framing; omit unavailable auxiliary Codec2 voice sinks; no voice-recovery claim"});
            args[0] = profile_path.display().to_string();
            replace_option(&mut args, "--wavfile", &wav);
            replace_option(&mut args, "--kiss_out", &arm.join("frames.kiss"));
        } else {
            replace_option(&mut args, "--input", &wav);
            replace_option(&mut args, "--output", &arm.join("decode"));
        }
        let executable = identity(&program);
        save(
            &arm.join("plan.json"),
            &json!({"prior_arm":old,"program":executable,"args":args,"shared_input":shared,"profile":profile_info,"timeout_seconds":900}),
        );
        let process =
            io::execute(&program, &args, &arm, "run", 900, 2 * 1024 * 1024 * 1024).unwrap();
        let mut outcome = json!({"status":if process["success"]==true {"complete"} else {"failed_or_incomplete"},
            "process":process,"executable":executable,"profile":profile_info,"unique_count":null,"validated_full_frames":null});
        if process["success"] == true {
            if name == "gr_satellites" {
                let parsed = baselines::parse_gr_satellites_kiss(
                    &fs::read(arm.join("frames.kiss")).unwrap(),
                )
                .unwrap();
                outcome["unique_count"] = json!(parsed.all_payloads.len());
                outcome["candidate_pdus"] = json!(parsed.all_payloads);
                outcome["independent_received_fcs_verified"] = false.into();
                outcome["validation_scope"]="LilacSat-1 main output candidate stream, not AX25_UI metric or independent CCSDS/FEC proof".into();
            } else {
                let result_path = arm.join("decode/result.json");
                let result = read(&result_path);
                assert_eq!(result["status"], "complete");
                let frames = if name == "native_audio" {
                    assert_eq!(result["failed_window_count"], 0);
                    fcs_frames(&result["frames"], true)
                } else {
                    fcs_frames(&result["report"]["union_full_frames"], false)
                };
                let count = if name == "native_audio" {
                    &result["unique_pdu_count"]
                } else {
                    &result["union_count"]
                };
                assert_eq!(count.as_u64(), Some(frames.len() as u64));
                outcome["unique_count"] = json!(frames.len());
                outcome["validated_full_frames"] = json!(frames);
                outcome["result"] = identity(&result_path);
                outcome["window_count"] = result["window_count"].clone();
                outcome["failed_window_count"] = result["failed_window_count"].clone();
                outcome["independent_received_fcs_verified"] = true.into();
            }
        }
        verify(&executable);
        summary["arms"][name] = outcome;
        io::replace_json(&root.join("progress.json"), &summary).unwrap();
    }
    verify(&prior_identity);
    verify(&prior["source"]);
    assert_eq!(identity(&wav), shared);
    summary["status"] = if summary["arms"]
        .as_object()
        .unwrap()
        .values()
        .all(|v| v["status"] == "complete")
    {
        "complete"
    } else {
        "partially_complete"
    }
    .into();
    save(&root.join("result.json"), &summary);
    println!("observation {id}: {}", summary["status"]);
    summary
}
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let (ids, root) = if args == ["gr-profile-extension-retry-v2"] {
        let root = Path::new(OUT).join("gr-profile-extension-retry-v2");
        fs::create_dir(&root).unwrap();
        (vec![3208, 3412], root)
    } else {
        assert!(
            args.is_empty(),
            "only bounded gr-profile-extension-retry-v2 is supported"
        );
        (vec![3928, 3929, 3208, 3412], PathBuf::from(OUT))
    };
    let results: Vec<_> = ids.into_iter().map(|id| run(id, &root)).collect();
    save(
        &root.join("replay-summary.json"),
        &json!({"schema":"runtime-repair-replay-summary-v1","observations":results,"publication_ready":false,"deployment_ready":false}),
    );
}
