//! Resumable, profile-routed analysis of a private-instance audio acquisition.
//! No network requests or reference payload bytes enter this dispatcher.
#[allow(dead_code)]
#[path = "support/today20_baselines.rs"]
mod baselines;
#[allow(dead_code)]
#[path = "support/holdout_run_io.rs"]
mod io;

use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    os::fd::AsRawFd,
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
    time::{Duration, Instant},
};
use telemetry_yield_rs::{formats, input, protocol};

const INSTANCE: &str = "https://polyitan.duckdns.org:8001";
const NATIVE: &str =
    "/home/ubuntu/telemetry-yield/work/progressive-20260910-v3/bin/telemetry-yield-rs";
const NATIVE_SHA: &str = "633f1f4ecaf7d4617f46fda83bf0866223bc123fa5013e3146cc3e32edefbb91";
const INNOVATION: &str =
    "/home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/bin/innovation_audio_probe";
const INNOVATION_SHA: &str = "1ba812f746a4fa65f53625c568e75188bd0c069f6c65f86878811f2c742895b6";
const GRSAT: &str = "/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites";
const SATYAML: &str =
    "/home/ubuntu/telemetry-yield/work/golden/env/lib/python3.12/site-packages/satellites/satyaml";
const ARMS: [&str; 4] = ["gr_satellites", "native_audio", "direwolf", "innovation_v2"];

#[derive(Parser)]
struct Args {
    #[arg(long)]
    acquisition: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 2)]
    workers: usize,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    #[arg(long, default_value_t = 900)]
    decoder_seconds: u64,
    /// Wait for further acquisition receipts, not for a predetermined subset.
    #[arg(long, default_value_t = 0)]
    watch_seconds: u64,
    #[arg(long)]
    resume: bool,
    /// Re-run only prior failed attempts; unsupported profiles remain explicit.
    #[arg(long)]
    retry_failed: bool,
    /// Operational one-shot milestone, not an outcome-based selection.
    #[arg(long)]
    max_new_observations: Option<usize>,
    /// Versioned, provenance-bearing additional SatYAML documents (JSON catalogue).
    #[arg(long)]
    supplemental_profiles: Option<PathBuf>,
    /// Decode only recordings rejected by the original installed-profile policy.
    #[arg(long)]
    only_newly_routable: bool,
    /// Audit all routing decisions without launching a demodulator.
    #[arg(long)]
    routing_only: bool,
    /// Explicit candidate child; requires its exact lowercase SHA256.
    #[arg(long, requires = "native_sha256")]
    native_binary: Option<PathBuf>,
    #[arg(long, requires = "native_binary")]
    native_sha256: Option<String>,
    /// Explicit innovation child, independently bound from the native child.
    #[arg(long, requires = "innovation_sha256")]
    innovation_binary: Option<PathBuf>,
    #[arg(long, requires = "innovation_binary")]
    innovation_sha256: Option<String>,
}

fn child_binary<'a>(args: &'a Args, name: &str) -> Result<(&'a Path, &'a str), String> {
    let (path, sha, default_path, default_sha) = match name {
        "native_audio" => (&args.native_binary, &args.native_sha256, NATIVE, NATIVE_SHA),
        "innovation_v2" => (
            &args.innovation_binary,
            &args.innovation_sha256,
            INNOVATION,
            INNOVATION_SHA,
        ),
        _ => return Err("unknown configurable child".into()),
    };
    let (path, sha) = match (path, sha) {
        (None, None) => (Path::new(default_path), default_sha),
        (Some(path), Some(sha)) => (path.as_path(), sha.as_str()),
        _ => return Err("child executable path and SHA256 must both be explicit".into()),
    };
    if !path.is_absolute()
        || sha.len() != 64
        || !sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("child requires absolute path and lowercase SHA256".into());
    }
    Ok((path, sha))
}
fn checked_child_identity(args: &Args, name: &str) -> Result<Value, String> {
    let (path, sha) = child_binary(args, name)?;
    let value = identity(path)?;
    if value["sha256"] != sha {
        return Err(format!("{name} child executable SHA256 mismatch"));
    }
    Ok(value)
}
fn child_bindings(args: &Args) -> Result<Value, String> {
    Ok(
        json!({"native_audio":checked_child_identity(args,"native_audio")?,
        "innovation_v2":checked_child_identity(args,"innovation_v2")?}),
    )
}
fn bound_child_identity(args: &Args, name: &str, declared: &Value) -> Result<Value, String> {
    let actual = checked_child_identity(args, name)?;
    if declared["child_binaries"][name] != actual {
        return Err(format!("{name} child differs from frozen plan"));
    }
    Ok(actual)
}

fn same(a: &Value, b: &Value) -> bool {
    a["sha256"].as_str().is_some() && a["sha256"] == b["sha256"] && a["bytes"] == b["bytes"]
}
fn identity(path: &Path) -> Result<Value, String> {
    Ok(json!(input::identity(path)?))
}
fn verify(value: &Value) -> Result<(), String> {
    let path = value["path"].as_str().ok_or("identity path absent")?;
    if !same(value, &identity(Path::new(path))?) {
        return Err(format!("identity changed: {path}"));
    }
    Ok(())
}
fn read(path: &Path) -> Result<Value, String> {
    input::read_json(path)
}
fn numeric(v: &Value) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .filter(|x| x.is_finite() && *x > 0.0)
}
fn mode(s: &str) -> String {
    match s.trim().to_ascii_uppercase().as_str() {
        "GMSK" | "GFSK" | "FSK" | "2FSK" | "FSK AX.25 G3RUH" | "FSK AX.100 MODE 5" => "FSK".into(),
        "DUV" => "FSK SUBAUDIO".into(),
        other => other.to_owned(),
    }
}
fn observation_id(metadata: &Value) -> Result<u64, String> {
    metadata["id"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or_else(|| "observation id absent".into())
}
fn route_single(
    metadata: &Value,
    registry: &formats::SatYamlRegistry,
    expanded_labels: bool,
) -> Value {
    let norad = metadata["norad_cat_id"].as_u64();
    let Some(profile) = norad.and_then(|id| registry.get_by_norad(id)) else {
        return json!({"status":"unsupported_profile","reason":"No unique installed SatYAML for metadata NORAD", "norad":norad});
    };
    let observed_mode = metadata["transmitter_mode"].as_str().map(|s| {
        if !expanded_labels && s.trim().eq_ignore_ascii_case("FSK AX.100 Mode 5") {
            s.trim().to_ascii_uppercase()
        } else {
            mode(s)
        }
    });
    let baud = numeric(&metadata["transmitter_baud"]);
    let frequency = ["transmitter_downlink_low", "frequency", "center_frequency"]
        .iter()
        .find_map(|key| numeric(&metadata[*key]));
    let candidates: Vec<_> = profile
        .transmitters
        .iter()
        .filter(|tx| {
            let tx_mode = mode(&tx.modulation);
            let compatible_mode = observed_mode.as_ref().is_none_or(|m| {
                *m == tx_mode
                    || (expanded_labels
                        && tx
                            .metadata
                            .get("metadata_mode_aliases")
                            .and_then(Value::as_array)
                            .is_some_and(|aliases| {
                                aliases
                                    .iter()
                                    .any(|a| a.as_str().is_some_and(|s| mode(s) == *m))
                            }))
            });
            // Labels which explicitly specify framing constrain it too.
            let compatible_framing = !expanded_labels
                || match metadata["transmitter_mode"]
                    .as_str()
                    .map(|s| s.trim().to_ascii_uppercase())
                    .as_deref()
                {
                    Some("FSK AX.100 MODE 5") => tx.framing == "AX100 ASM+Golay",
                    Some("FSK AX.25 G3RUH") => tx.framing == "AX.25 G3RUH",
                    _ => true,
                };
            let compatible_baud = baud.is_none_or(|b| {
                tx.metadata
                    .get("baudrate")
                    .and_then(numeric)
                    .is_some_and(|t| (t - b).abs() < 0.01)
            });
            let compatible_frequency = frequency.is_none_or(|f| {
                tx.metadata
                    .get("frequency")
                    .and_then(numeric)
                    .is_some_and(|t| (t - f).abs() <= 25000.0)
            });
            compatible_mode && compatible_framing && compatible_baud && compatible_frequency
        })
        .collect();
    if candidates.len() != 1 {
        return json!({"status":"unsupported_or_ambiguous_profile","norad":norad,"profile":profile,
            "matching_transmitters":candidates,"reason":"Requires one mode/baud/frequency-compatible installed transmitter", "frequency_tolerance_hz":25000});
    }
    let tx = candidates[0];
    let mut native = None;
    if matches!(tx.framing.as_str(), "AX.25" | "AX.25 G3RUH") && tx.fec.is_empty() {
        let b = tx.metadata.get("baudrate").and_then(numeric);
        if mode(&tx.modulation) == "FSK" && b.is_some() {
            native = Some("fsk");
        }
        if mode(&tx.modulation) == "AFSK"
            && b == Some(1200.0)
            && tx
                .metadata
                .get("af_carrier")
                .and_then(numeric)
                .unwrap_or(1700.0)
                == 1700.0
            && tx
                .metadata
                .get("deviation")
                .and_then(numeric)
                .unwrap_or(500.0)
                == 500.0
        {
            native = Some("afsk");
        }
    }
    json!({"status":"routed","norad":norad,"profile":profile,"transmitter":tx,
        "native_mode":native,"baud":tx.metadata.get("baudrate"),"frequency_tolerance_hz":25000,
        "mode_family_assumptions":"GMSK/GFSK/2FSK map to FSK; DUV maps to FSK subaudio; no result-based routing",
        "unknown_metadata_fields_require_unique_remaining_transmitter":true,
        "representation":"Real demodulated audio, not reconstructed RF IQ"})
}

fn route_v1(metadata: &Value, registry: &formats::SatYamlRegistry) -> Value {
    route_single(metadata, registry, false)
}

fn route(metadata: &Value, registry: &formats::SatYamlRegistry) -> Value {
    let mut result = route_single(metadata, registry, true);
    let Some(candidates) = result["matching_transmitters"].as_array() else {
        return result;
    };
    // Multiple *documented* framing variants on one fully specified physical
    // channel can all be tested by gr-satellites. Missing rate/mode/frequency or
    // duplicate indistinguishable entries remain ambiguous, not a blind guess.
    let explicit = metadata["transmitter_mode"].is_string()
        && numeric(&metadata["transmitter_baud"]).is_some()
        && numeric(&metadata["transmitter_downlink_low"]).is_some();
    if explicit && (2..=4).contains(&candidates.len()) {
        let first = &candidates[0];
        let framings: BTreeSet<_> = candidates.iter().map(|t| t["framing"].as_str()).collect();
        if framings.len() == candidates.len()
            && candidates.iter().all(|t| {
                t["modulation"] == first["modulation"]
                    && t["metadata"]["baudrate"] == first["metadata"]["baudrate"]
                    && t["metadata"]["frequency"] == first["metadata"]["frequency"]
            })
        {
            let all = candidates.clone();
            let baud = first["metadata"]["baudrate"].clone();
            result["status"] = "routed".into();
            result["transmitters"] = json!(all);
            result["transmitter"] = Value::Null;
            result["native_mode"] = Value::Null;
            result["baud"] = baud;
            result["reason"] =
                "All documented same-channel framing variants are tested; none silently selected"
                    .into();
            result["multiple_framing_hypotheses"] = true.into();
            result["representation"] = "Real demodulated audio, not reconstructed RF IQ".into();
        }
    }
    result
}

/// Route each receiver on its own required contract. An unavailable SatYAML
/// blocks only gr-satellites, not an independently specified AX.25 receiver.
/// Generic FSK/GMSK labels do NOT specify a link-layer protocol.
fn backend_routes(metadata: &Value, profile_route: &Value, rate: Option<u32>, ogg: bool) -> Value {
    let mut native_route = profile_route.clone();
    if profile_route["status"] != "routed" {
        let explicit_g3ruh = metadata["transmitter_mode"]
            .as_str()
            .is_some_and(|s| s.trim().eq_ignore_ascii_case("FSK AX.25 G3RUH"));
        if explicit_g3ruh && numeric(&metadata["transmitter_baud"]).is_some() {
            native_route = json!({"status":"routed","native_mode":"fsk","baud":metadata["transmitter_baud"],
                "transmitter":{"framing":"AX.25 G3RUH"},"routing_evidence":{
                    "kind":"explicit_acquired_metadata_not_inferred_from_signal",
                    "transmitter_mode":metadata["transmitter_mode"],"transmitter_baud":metadata["transmitter_baud"]},
                "representation":"Real demodulated audio, not reconstructed RF IQ"});
        }
    }
    let native = native_route["native_mode"].as_str();
    let baud = numeric(&native_route["baud"]);
    let g3ruh = native_route["transmitter"]["framing"] == "AX.25 G3RUH";
    let mut output = json!({});
    for name in ARMS {
        let eligible = match name {
            "gr_satellites" => profile_route["status"] == "routed",
            "native_audio" => native.is_some(),
            // Dire Wolf -B >=4800 selects the G3RUH modem. Do not present that
            // as a matched baseline for documented unscrambled AX.25 FSK.
            "direwolf" => {
                (native == Some("afsk") && baud == Some(1200.0))
                    || (native == Some("fsk")
                        && g3ruh
                        && matches!(baud, Some(4800.0 | 9600.0 | 19200.0 | 38400.0)))
            }
            "innovation_v2" => {
                native == Some("fsk")
                    && g3ruh
                    && baud == Some(9600.0)
                    && rate.is_none_or(|r| r == 48000)
                    && ogg
            }
            _ => false,
        };
        output[name] = if eligible {
            json!({"status":"routed","parameters":if name == "gr_satellites" {profile_route} else {&native_route}})
        } else {
            json!({"status":"unsupported_backend_contract","reason":if name == "gr_satellites" {
                "No unambiguous documented satellite/transmitter profile for this backend"
            } else {"No explicit compatible modulation/framing/rate/audio contract for this backend"},
                "candidate_unique_count":null,"strict_ui_unique_count":null})
        };
    }
    output
}

fn any_backend_routed(routes: &Value) -> bool {
    ARMS.iter().any(|name| routes[*name]["status"] == "routed")
}

fn load_supplemental(
    path: &Path,
    output: &Path,
    registry: &mut formats::SatYamlRegistry,
) -> Result<Value, String> {
    let catalog = read(path)?;
    if catalog["schema"] != "polyitan-supplemental-profiles-v1" {
        return Err("unknown supplemental profile catalogue schema".into());
    }
    let entries = catalog["profiles"]
        .as_array()
        .ok_or("profile catalogue entries absent")?;
    let dir = output.join("supplemental-profiles");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    for entry in entries {
        let mut doc = entry["document"].clone();
        let evidence = &entry["evidence"];
        if !evidence["urls"].as_array().is_some_and(|a| {
            !a.is_empty()
                && a.iter()
                    .all(|v| v.as_str().is_some_and(|s| s.starts_with("https://")))
        }) || !evidence["limitations"].is_string()
        {
            return Err(
                "supplemental profile requires HTTPS evidence and explicit limitations".into(),
            );
        }
        let norad = doc["norad"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or("supplemental NORAD absent")?;
        if registry.get_by_norad(norad).is_some() || registry.conflicted_norads.contains(&norad) {
            return Err(format!(
                "supplemental profile would overwrite/conflict with existing NORAD {norad}"
            ));
        }
        doc["x-evidence"] = evidence.clone();
        let dest = dir.join(format!("{norad}.yml"));
        let parsed = formats::parse_satyaml_document(&doc, &dest.display().to_string())?;
        if dest.exists() {
            if read(&dest)? != doc {
                return Err("frozen supplemental profile changed".into());
            }
        } else {
            input::write_json_new(&dest, &doc)?;
        }
        registry.norad_index.insert(norad, parsed.selector.clone());
        registry.profiles.push(parsed);
    }
    identity(path)
}

fn selected_profile(route: &Value) -> Result<Value, String> {
    if let Some(variants) = route["transmitters"].as_array() {
        let mut transmitters = serde_json::Map::new();
        for (i, tx) in variants.iter().enumerate() {
            let mut single = route.clone();
            single
                .as_object_mut()
                .ok_or("route not object")?
                .remove("transmitters");
            single["transmitter"] = tx.clone();
            transmitters.insert(
                format!("variant-{i}"),
                selected_profile(&single)?["transmitters"]["selected"].clone(),
            );
        }
        return Ok(
            json!({"name":route["profile"]["name"],"norad":route["norad"],"data":{"Raw":"unknown"},"transmitters":transmitters}),
        );
    }
    let tx = &route["transmitter"];
    let mut selected = tx["metadata"]
        .as_object()
        .ok_or("transmitter metadata absent")?
        .clone();
    selected.insert("modulation".into(), tx["modulation"].clone());
    selected.insert("framing".into(), tx["framing"].clone());
    if tx["fec"].as_array().is_some_and(|a| !a.is_empty()) {
        selected.insert("fec".into(), tx["fec"].clone());
    }
    // Preserve every DSP/deframer parameter, but do not instantiate telemetry,
    // image, forwarding, transport or uploading sinks for this offline task.
    selected.remove("transports");
    // Additional ports (e.g. LilacSat/Taurus codec2 voice) refer to named
    // application sinks removed above. Keeping these references crashes GR
    // before it can run the normal telemetry output. This offline comparison
    // captures only the deframer's main out port; voice/image side streams are
    // neither decoded nor counted as telemetry candidates.
    selected.remove("additional_data");
    selected.remove("metadata_mode_aliases");
    selected.insert("data".into(), json!(["Raw"]));
    Ok(
        json!({"name":route["profile"]["name"],"norad":route["norad"],
        "data":{"Raw":"unknown"},"transmitters":{"selected":selected}}),
    )
}

fn native_pdus(frames: &Value) -> Result<BTreeSet<String>, String> {
    let mut pdus = BTreeSet::new();
    for item in frames.as_array().ok_or("native frame array absent")? {
        let value = item
            .as_str()
            .or_else(|| item["frame_with_fcs_hex"].as_str())
            .ok_or("native full frame absent")?;
        let frame = hex::decode(value).map_err(|e| e.to_string())?;
        if frame.len() < 3 {
            return Err("short native frame".into());
        }
        let body = &frame[..frame.len() - 2];
        let mut crc = 0xffffu16;
        for byte in body {
            crc ^= u16::from(*byte);
            for _ in 0..8 {
                crc = if crc & 1 == 1 {
                    (crc >> 1) ^ 0x8408
                } else {
                    crc >> 1
                };
            }
        }
        if (crc ^ 0xffff).to_le_bytes() != frame[frame.len() - 2..] {
            return Err("received native CRC recheck failed".into());
        }
        pdus.insert(hex::encode(body));
    }
    Ok(pdus)
}
fn set(value: &Value) -> Result<BTreeSet<String>, String> {
    value
        .as_array()
        .ok_or("PDU set absent")?
        .iter()
        .map(|v| v.as_str().map(str::to_owned).ok_or("PDU not string".into()))
        .collect()
}

/// CRC checks alone do not establish a valid AX.25 UI packet, mission telemetry,
/// or an archive-absent gain. Keep evidence tiers separate, including null when
/// the selected protocol has no implemented independent validation contract.
fn validation_accounting(
    pdus: &BTreeSet<String>,
    crc: bool,
    independent: bool,
) -> Result<Value, String> {
    let strict: BTreeSet<String> = if crc {
        pdus.iter()
            .filter_map(|p| {
                let bytes = hex::decode(p).ok()?;
                protocol::parse_ax25_ui(&bytes).is_some().then(|| p.clone())
            })
            .collect()
    } else {
        BTreeSet::new()
    };
    // Fail closed on malformed transport-normalized hex even for unresolved
    // protocols; parser errors must not silently become empty success sets.
    for p in pdus {
        hex::decode(p).map_err(|e| e.to_string())?;
    }
    let unresolved: BTreeSet<_> = pdus.difference(&strict).cloned().collect();
    Ok(json!({
        "strict_ui_pdus":if crc {json!(strict)} else {Value::Null},
        "strict_ui_unique_count":if crc {json!(strict.len())} else {Value::Null},
        "crc_passed_pdus":if crc {json!(pdus)} else {Value::Null},
        "crc_passed_unique_count":if crc {json!(pdus.len())} else {Value::Null},
        "crc_passed_non_ui_pdus":if crc {json!(unresolved)} else {Value::Null},
        "crc_passed_non_ui_unique_count":if crc {json!(unresolved.len())} else {Value::Null},
        "protocol_unresolved_pdus":unresolved,"protocol_unresolved_unique_count":unresolved.len(),
        "independent_fcs_verified":crc && independent,
        "received_fcs_present":crc && independent,
        "non_ui_crc_pass_is_not_verified_telemetry":true,
        "outcome":if pdus.is_empty() {"no_frames"} else if !strict.is_empty() {"strict_ui_frames"} else {"unresolved_candidates"}
    }))
}

fn parse_gr_transport(bytes: &[u8]) -> Result<formats::KissResult, String> {
    if !bytes.is_empty() && bytes[0] != 0xc0 {
        return Err("KISS prefix malformed".into());
    }
    let parsed = formats::parse_kiss(bytes)?;
    if parsed.malformed_records != 0 || !parsed.other_commands.is_empty() {
        return Err("malformed or unsupported-command KISS output".into());
    }
    Ok(parsed)
}

fn score(
    name: &str,
    dir: &Path,
    source: &Value,
    pcm: &Value,
    routing: &Value,
) -> Result<Value, String> {
    let mut artifacts = vec![];
    let mut transport = Value::Null;
    let (pdus, crc_contract, evidence) = match name {
        "native_audio" | "innovation_v2" => {
            let path = dir.join("decode/result.json");
            let r = read(&path)?;
            let native_source = if name == "native_audio" {
                &r["source_input"]
            } else {
                &r["source"]
            };
            if r["status"] != "complete" || !same(native_source, pcm) {
                return Err("native input / completion mismatch".into());
            }
            if name == "innovation_v2" {
                let binding = &r["codec_input_binding"];
                if !same(&binding["codec_source"], source)
                    || !same(&binding["analysis_input"], pcm)
                    || !same(&binding["derived_pcm16"], pcm)
                    || binding["same_numeric_input_verified"] != true
                {
                    return Err("codec source / shared PCM binding mismatch".into());
                }
            }
            let frames = if name == "native_audio" {
                &r["frames"]
            } else {
                &r["report"]["union_full_frames"]
            };
            let pdus = native_pdus(frames)?;
            let count = if name == "native_audio" {
                &r["unique_pdu_count"]
            } else {
                &r["union_count"]
            };
            if count.as_u64() != Some(pdus.len() as u64) {
                return Err("native count mismatch".into());
            }
            artifacts.push(identity(&path)?);
            (
                pdus.clone(),
                true,
                "independently rechecked received FCS; strict AX.25 UI counted separately",
            )
        }
        "direwolf" => {
            let text = input::read_bytes_bounded(&dir.join("run.stdout.log"), 64 * 1024 * 1024)?;
            let p = baselines::parse_direwolf_atest(&String::from_utf8_lossy(&text))?;
            (
                p.all_payloads,
                true,
                "Dire Wolf internal received-FCS check, Fix Bits 0; received FCS stripped",
            )
        }
        "gr_satellites" => {
            let path = dir.join("frames.kiss");
            let bytes = input::read_bytes_bounded(&path, 64 * 1024 * 1024)?;
            let p = parse_gr_transport(&bytes)?;
            transport = json!({"data_records":p.data_frames.len(),"timestamp_records":p.timestamp_commands.len(),
                "timestamps_excluded_from_pdu_counts":true,"malformed_records":p.malformed_records,
                "data_record_metadata":p.data_frames.iter().map(|f|json!({"record_index":f.record_index,"port":f.port,"timestamp_ms":f.timestamp_ms,"payload_bytes":f.payload.len()})).collect::<Vec<_>>()});
            let all: BTreeSet<String> = p
                .data_frames
                .iter()
                .map(|f| hex::encode(&f.payload))
                .collect();
            let ax25 = matches!(
                routing["transmitter"]["framing"].as_str(),
                Some("AX.25" | "AX.25 G3RUH")
            );
            artifacts.push(identity(&path)?);
            (
                all,
                ax25,
                if ax25 {
                    "gr-satellites AX.25 internal FCS check; received FCS stripped"
                } else {
                    "profile-specific deframer output; independent protocol validation pending, not CRC-verified telemetry"
                },
            )
        }
        _ => return Err("unknown arm".into()),
    };
    for path in [
        "run.stdout.log",
        "run.stderr.log",
        "run.process.json",
        "run.rusage.txt",
    ] {
        artifacts.push(identity(&dir.join(path))?);
    }
    let mut scored = validation_accounting(
        &pdus,
        crc_contract,
        name == "native_audio" || name == "innovation_v2",
    )?;
    scored["pdus"] = json!(pdus);
    scored["candidate_unique_count"] = json!(pdus.len());
    scored["pdu_bytes"] = json!(pdus.iter().map(|p| p.len() / 2).sum::<usize>());
    scored["validation_evidence"] = evidence.into();
    scored["kiss_transport"] = transport;
    scored["artifacts"] = json!(artifacts);
    Ok(scored)
}

fn committed(root: &Path, source: &Value, plan: &Value) -> Result<Option<Value>, String> {
    let path = root.join("committed.json");
    if !path.exists() {
        return Ok(None);
    }
    let pointer = read(&path)?;
    verify(&pointer["record"])?;
    let record_path = PathBuf::from(
        pointer["record"]["path"]
            .as_str()
            .ok_or("commit path absent")?,
    );
    if !record_path.starts_with(root) {
        return Err("commit escaped arm directory".into());
    }
    let r = read(&record_path)?;
    if r["instance"] != INSTANCE || r["arm"].as_str() != root.file_name().and_then(|s| s.to_str()) {
        return Err("commit namespace / arm mismatch".into());
    }
    if !same(&r["source"], source) || r["plan_sha256"] != plan["sha256"] {
        return Err("commit source / plan changed".into());
    }
    for artifact in r["artifacts"].as_array().ok_or("commit artifacts absent")? {
        verify(artifact)?;
    }
    Ok(Some(r))
}

fn arm(
    args: &Args,
    name: &str,
    root: &Path,
    source: &Value,
    pcm: &Value,
    route: &Value,
    plan: &Value,
    profile: &Path,
) -> Result<Value, String> {
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    if let Some(r) = committed(root, source, plan)? {
        if r["status"] == "complete" || !args.retry_failed {
            if !same(&r["input"], pcm) {
                return Err("resumed arm shared PCM identity mismatch".into());
            }
            return Ok(r);
        }
    }
    let dir = tempfile::Builder::new()
        .prefix("attempt-")
        .tempdir_in(root)
        .map_err(|e| e.to_string())?
        .keep();
    let mut r = json!({"schema":"polyitan-audio-arm-v2","instance":INSTANCE,"arm":name,"source":source,"input":pcm,
        "receiver_identity":if name == "native_audio" {"our_rust_generic_audio_not_native_satnogs"} else {name},"routing":route,
        "plan_sha256":plan["sha256"],"status":"running","started_utc":io::utc(),"artifacts":[],
        "candidate_unique_count":null,"strict_ui_unique_count":null,"crc_passed_unique_count":null,
        "protocol_unresolved_unique_count":null,"outcome":null});
    let execution = (|| -> Result<Value, String> {
        verify(source)?;
        verify(pcm)?;
        let wav = pcm["path"].as_str().ok_or("WAV path absent")?.to_owned();
        let mut argv = vec![
            "--input".into(),
            wav.clone(),
            "--output".into(),
            dir.join("decode").display().to_string(),
            "--threads".into(),
            args.threads.to_string(),
        ];
        let program = match name {
            "native_audio" => {
                argv.insert(0, "decode-audio".into());
                argv.extend([
                    "--mode".into(),
                    route["native_mode"]
                        .as_str()
                        .ok_or("native mode absent")?
                        .into(),
                    "--baud".into(),
                    numeric(&route["baud"]).ok_or("baud absent")?.to_string(),
                ]);
                child_binary(args, name)?.0
            }
            "innovation_v2" => {
                argv.extend([
                    "--codec-sideinfo".into(),
                    "--pooled-codec".into(),
                    "--codec-source".into(),
                    source["path"].as_str().ok_or("source path absent")?.into(),
                ]);
                child_binary(args, name)?.0
            }
            "direwolf" => {
                argv = vec![
                    "-B".into(),
                    (numeric(&route["baud"]).ok_or("baud absent")? as u64).to_string(),
                    "-F".into(),
                    "0".into(),
                    "-h".into(),
                    wav,
                ];
                Path::new("/usr/bin/atest")
            }
            "gr_satellites" => {
                argv = vec![
                    profile.display().to_string(),
                    "--wavfile".into(),
                    wav,
                    "--kiss_out".into(),
                    dir.join("frames.kiss").display().to_string(),
                    "--hexdump".into(),
                ];
                Path::new(GRSAT)
            }
            _ => return Err("unknown arm".into()),
        };
        let child = if matches!(name, "native_audio" | "innovation_v2") {
            verify(plan)?;
            let declared = read(Path::new(plan["path"].as_str().ok_or("plan path absent")?))?;
            let value = bound_child_identity(args, name, &declared)?;
            r["executable"] = value.clone();
            Some(value)
        } else {
            None
        };
        let process = io::execute(
            program,
            &argv,
            &dir,
            "run",
            args.decoder_seconds,
            512 * 1024 * 1024,
        )?;
        r["process"] = process.clone();
        if let Some(child) = child {
            verify(&child)?;
        }
        io::require_success(&process)?;
        verify(source)?;
        verify(pcm)?;
        score(name, &dir, source, pcm, route)
    })();
    match execution {
        Ok(scored) => {
            for (k, v) in scored.as_object().unwrap() {
                r[k] = v.clone();
            }
            r["status"] = "complete".into();
        }
        Err(error) => {
            r["status"] = if r["process"]["timed_out"] == true {
                "timeout"
            } else {
                "failed_or_incomplete"
            }
            .into();
            r["outcome"] = "execution_incomplete_not_zero_recovery".into();
            r["error"] = error.into();
            for name in [
                "run.stdout.log",
                "run.stderr.log",
                "run.process.json",
                "run.rusage.txt",
            ] {
                let path = dir.join(name);
                if path.exists() {
                    r["artifacts"]
                        .as_array_mut()
                        .unwrap()
                        .push(identity(&path)?);
                }
            }
        }
    }
    r["finished_utc"] = io::utc().into();
    let path = dir.join("result.json");
    input::write_json_new(&path, &r)?;
    io::replace_json(
        &root.join("committed.json"),
        &json!({"record":identity(&path)?}),
    )?;
    Ok(r)
}

fn run_observation(
    args: &Args,
    id: u64,
    registry: &formats::SatYamlRegistry,
    plan: &Value,
) -> Result<(), String> {
    let acquired = args.acquisition.join(format!("obs-{id}"));
    let root = args.output.join(format!("obs-{id}"));
    fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let source_record = read(&acquired.join("downloaded.json"))?;
    if source_record["instance"] != INSTANCE || source_record["observation_id"].as_u64() != Some(id)
    {
        return Err("download receipt private namespace / observation mismatch".into());
    }
    let source = &source_record["source"];
    verify(source)?;
    let source_path = PathBuf::from(source["path"].as_str().ok_or("source path missing")?)
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if !source_path.starts_with(acquired.canonicalize().map_err(|e| e.to_string())?) {
        return Err("source escaped observation acquisition directory".into());
    }
    let metadata = read(&acquired.join("metadata.json"))?;
    if observation_id(&metadata)? != id {
        return Err("observation id / directory mismatch".into());
    }
    let metadata_identity = identity(&acquired.join("metadata.json"))?;
    verify(&source_record["metadata"])?;
    if !same(&source_record["metadata"], &metadata_identity) {
        return Err("download receipt metadata differs from observation metadata".into());
    }
    let mut routing = route(&metadata, registry);
    if let Some(selector) = routing["profile"]["selector"].as_str() {
        let selector = Path::new(selector);
        if selector.starts_with(args.output.join("supplemental-profiles")) {
            routing["profile_evidence"] = read(selector)?["x-evidence"].clone();
        }
    }
    let ogg = source_path
        .extension()
        .is_some_and(|s| s.eq_ignore_ascii_case("ogg"));
    let preliminary_routes = backend_routes(&metadata, &routing, None, ogg);
    let mut state = json!({"schema":"polyitan-audio-observation-v2","instance":INSTANCE,"observation_id":id,"source":source,
        "metadata":metadata_identity,"routing":routing,"plan_sha256":plan["sha256"],"status":"running","started_utc":io::utc(),"arms":{},
        "backend_routing":preliminary_routes,
        "reference_bytes_used_for_search":false,"private_observation_namespace":true});
    io::replace_json(&root.join("progress.json"), &state)?;
    let result = (|| -> Result<(), String> {
        if !any_backend_routed(&preliminary_routes) {
            state["arms"] = preliminary_routes.clone();
            state["status"] = "unsupported_backend_contract".into();
            return Ok(());
        }
        let profile_path = root.join("selected-profile.yml");
        if preliminary_routes["gr_satellites"]["status"] == "routed" {
            let profile = selected_profile(&routing)?;
            if profile_path.exists() {
                if read(&profile_path)? != profile {
                    return Err("selected profile changed on resume".into());
                }
            } else {
                input::write_json_new(&profile_path, &profile)?;
            }
            state["selected_profile"] = identity(&profile_path)?;
        }
        let scratch = tempfile::Builder::new()
            .prefix("pcm-")
            .tempdir_in(&root)
            .map_err(|e| e.to_string())?;
        let wav = scratch.path().join("shared.wav");
        let preparation = tempfile::Builder::new()
            .prefix("preparation-")
            .tempdir_in(&root)
            .map_err(|e| e.to_string())?
            .keep();
        let convert = io::execute(
            Path::new("/usr/bin/ffmpeg"),
            &[
                "-nostdin".into(),
                "-v".into(),
                "error".into(),
                "-n".into(),
                "-i".into(),
                source_path.display().to_string(),
                "-map".into(),
                "0:a:0".into(),
                "-c:a".into(),
                "pcm_s16le".into(),
                "-flags:a".into(),
                "+bitexact".into(),
                "-fflags".into(),
                "+bitexact".into(),
                wav.display().to_string(),
            ],
            &preparation,
            "convert",
            180,
            512 * 1024 * 1024,
        )?;
        state["conversion"] = convert.clone();
        state["preparation_artifacts"] = json!([
            identity(&preparation.join("convert.stdout.log"))?,
            identity(&preparation.join("convert.stderr.log"))?,
            identity(&preparation.join("convert.process.json"))?,
            identity(&preparation.join("convert.rusage.txt"))?
        ]);
        io::require_success(&convert)?;
        let reader = hound::WavReader::open(&wav).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        state["audio"] = json!({"channels":spec.channels,"sample_rate":spec.sample_rate,"samples":reader.duration(),"duration_seconds":reader.duration() as f64/spec.sample_rate as f64});
        if spec.channels != 1
            || spec.sample_format != hound::SampleFormat::Int
            || spec.bits_per_sample != 16
        {
            for name in ARMS {
                state["arms"][name] = json!({"status":"unsupported_audio_representation","candidate_unique_count":null,"strict_ui_unique_count":null});
            }
            state["status"] = "unsupported_audio_representation".into();
            return Ok(());
        }
        let pcm = identity(&wav)?;
        state["input"] = pcm.clone();
        state["conversion"] = convert;
        let routes = backend_routes(&metadata, &routing, Some(spec.sample_rate), ogg);
        state["backend_routing"] = routes.clone();
        let mut arms = vec![];
        for name in ARMS {
            if routes[name]["status"] == "routed" {
                arms.push(name);
            } else {
                state["arms"][name] = routes[name].clone();
            }
        }
        if arms.is_empty() {
            state["status"] = "unsupported_backend_contract".into();
            return Ok(());
        }
        let rotation = id as usize % arms.len();
        arms.rotate_left(rotation);
        for name in arms {
            let r = arm(
                args,
                name,
                &root.join("arms").join(name),
                source,
                &pcm,
                &routes[name]["parameters"],
                plan,
                &profile_path,
            )?;
            state["arms"][name] = r;
            io::replace_json(&root.join("progress.json"), &state)?;
        }
        verify(source)?;
        verify(&metadata_identity)?;
        state["status"] = if state["arms"].as_object().unwrap().values().any(|r| {
            matches!(
                r["status"].as_str(),
                Some("timeout" | "failed_or_incomplete")
            )
        }) {
            "partially_complete"
        } else {
            "complete"
        }
        .into();
        let completed_native: Vec<_> = state["arms"]
            .as_object()
            .unwrap()
            .iter()
            .filter(|(k, v)| {
                (k.starts_with("native") || *k == "innovation_v2") && v["status"] == "complete"
            })
            .map(|(_, v)| set(&v["strict_ui_pdus"]))
            .collect::<Result<Vec<_>, _>>()?;
        let native_complete_count = completed_native.len();
        let native_union: BTreeSet<String> = completed_native.into_iter().flatten().collect();
        state["native_complete_arm_count"] = json!(native_complete_count);
        state["native_union_pdus"] = if native_complete_count > 0 {
            json!(native_union)
        } else {
            Value::Null
        };
        state["native_union_count"] = if native_complete_count > 0 {
            json!(native_union.len())
        } else {
            Value::Null
        };
        Ok(())
    })();
    if let Err(error) = result {
        state["status"] = "failed_or_incomplete".into();
        state["error"] = error.into();
    }
    state["finished_utc"] = io::utc().into();
    // Attempts are immutable; this replaceable observation summary indexes them.
    io::replace_json(&root.join("result.json"), &state)?;
    println!(
        "private observation {id}: {}",
        state["status"].as_str().unwrap_or("unknown")
    );
    Ok(())
}

fn ready_ids(acquisition: &Path) -> Result<Vec<u64>, String> {
    let mut ids = vec![];
    for entry in fs::read_dir(acquisition).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let name = entry.file_name();
        let Some(id) = name
            .to_str()
            .and_then(|s| s.strip_prefix("obs-"))
            .and_then(|s| s.parse::<u64>().ok())
        else {
            continue;
        };
        if entry.path().join("downloaded.json").is_file() {
            ids.push(id);
        }
    }
    ids.sort_unstable();
    ids.reverse();
    Ok(ids)
}

fn acquisition_finished(path: &Path) -> Result<bool, String> {
    let path = path.join("acquisition-complete.json");
    if !path.is_file() {
        return Ok(false);
    }
    let receipt = read(&path)?;
    // A failed metadata prefix is not EOF and must not stop a live recovery.
    Ok(receipt["metadata_complete"] == true)
}

fn record_dispatcher_errors(output: &Path, errors: Vec<(u64, String)>) -> Result<(), String> {
    let path = output.join("dispatcher-errors.json");
    let mut history = if path.exists() {
        read(&path)?["errors"]
            .as_array()
            .ok_or("dispatcher error history malformed")?
            .clone()
    } else {
        vec![]
    };
    history.extend(
        errors
            .into_iter()
            .map(|(id, error)| json!({"observation_id":id,"error":error,"recorded_utc":io::utc()})),
    );
    io::replace_json(&path, &json!({"errors":history,"updated_utc":io::utc()}))
}

fn unresolved_dispatcher_errors(output: &Path) -> Result<BTreeSet<u64>, String> {
    let path = output.join("dispatcher-errors.json");
    if !path.exists() {
        return Ok(BTreeSet::new());
    }
    let r = read(&path)?;
    let errors = r["errors"]
        .as_array()
        .ok_or("dispatcher error history malformed")?;
    let mut missing = BTreeSet::new();
    for error in errors {
        let id = error["observation_id"]
            .as_u64()
            .ok_or("dispatcher error observation ID absent")?;
        if !output.join(format!("obs-{id}/result.json")).is_file() {
            missing.insert(id);
        }
    }
    Ok(missing)
}

fn summarize(args: &Args, plan: &Value, elapsed: f64, terminal: bool) -> Result<Value, String> {
    let mut counts = BTreeMap::<String, usize>::new();
    let mut arms = BTreeMap::<String, Value>::new();
    let mut global = BTreeSet::<String>::new();
    let mut observations = vec![];
    for entry in fs::read_dir(&args.output).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path().join("result.json");
        if !path.is_file() {
            continue;
        }
        let r = read(&path)?;
        if r["instance"] != INSTANCE || r["plan_sha256"] != plan["sha256"] {
            return Err("summary namespace / plan mismatch".into());
        }
        *counts
            .entry(r["status"].as_str().unwrap_or("invalid").to_owned())
            .or_default() += 1;
        observations.push(json!({"observation_id":r["observation_id"],"status":r["status"],"native_union_count":r["native_union_count"]}));
        if r["native_union_pdus"].is_array() {
            global.extend(set(&r["native_union_pdus"])?);
        }
        for (name, arm) in r["arms"].as_object().ok_or("arms absent")? {
            let total=arms.entry(name.clone()).or_insert_with(||json!({"complete":0,"unsupported":0,"incomplete":0,
                "complete_no_frames":0,"complete_strict_ui_frames":0,"complete_unresolved_candidates":0,
                "strict_ui_validation_supported_complete":0,"crc_validation_supported_complete":0,
                "verified_strict_ui_observation_pdu_pairs":0,"candidate_observation_pdu_pairs":0,
                "crc_passed_observation_pdu_pairs":0,"crc_passed_non_ui_observation_pdu_pairs":0,"protocol_unresolved_observation_pdu_pairs":0,
                "wall_seconds":0.0,"user_cpu_seconds":0.0,"system_cpu_seconds":0.0,"incomplete_arm_wall_seconds":0.0}));
            let key = match arm["status"].as_str() {
                Some("complete") => "complete",
                Some(
                    "unsupported_profile"
                    | "unsupported_backend_contract"
                    | "unsupported_audio_representation",
                ) => "unsupported",
                _ => "incomplete",
            };
            total[key] = json!(total[key].as_u64().unwrap() + 1);
            if key == "incomplete" {
                total["incomplete_arm_wall_seconds"] = json!(
                    total["incomplete_arm_wall_seconds"].as_f64().unwrap()
                        + arm["process"]["wall_seconds"].as_f64().unwrap_or(0.0)
                );
            }
            if key == "complete" {
                let outcome = match arm["outcome"].as_str() {
                    Some("no_frames") => "complete_no_frames",
                    Some("strict_ui_frames") => "complete_strict_ui_frames",
                    Some("unresolved_candidates") => "complete_unresolved_candidates",
                    _ => return Err("complete arm lacks explicit decode outcome".into()),
                };
                total[outcome] = json!(total[outcome].as_u64().unwrap() + 1);
                if arm["crc_passed_unique_count"].is_number() {
                    total["crc_validation_supported_complete"] =
                        json!(total["crc_validation_supported_complete"].as_u64().unwrap() + 1);
                }
                if arm["strict_ui_unique_count"].is_number() {
                    total["strict_ui_validation_supported_complete"] = json!(
                        total["strict_ui_validation_supported_complete"]
                            .as_u64()
                            .unwrap()
                            + 1
                    );
                }
                for (field, target) in [
                    (
                        "strict_ui_unique_count",
                        "verified_strict_ui_observation_pdu_pairs",
                    ),
                    ("candidate_unique_count", "candidate_observation_pdu_pairs"),
                    (
                        "crc_passed_unique_count",
                        "crc_passed_observation_pdu_pairs",
                    ),
                    (
                        "crc_passed_non_ui_unique_count",
                        "crc_passed_non_ui_observation_pdu_pairs",
                    ),
                    (
                        "protocol_unresolved_unique_count",
                        "protocol_unresolved_observation_pdu_pairs",
                    ),
                ] {
                    total[target] =
                        json!(total[target].as_u64().unwrap() + arm[field].as_u64().unwrap_or(0));
                }
                for field in ["wall_seconds", "user_cpu_seconds", "system_cpu_seconds"] {
                    total[field] = json!(
                        total[field].as_f64().unwrap()
                            + arm["process"][field].as_f64().unwrap_or(0.0)
                    );
                }
            }
        }
    }
    observations.sort_by_key(|v| v["observation_id"].as_u64());
    let inventory = args.acquisition.join("inventory.json");
    let inventory = if inventory.is_file() {
        read(&inventory)?
    } else {
        Value::Null
    };
    let ready = ready_ids(&args.acquisition)?.len();
    let dispatcher_failures = unresolved_dispatcher_errors(&args.output)?;
    let plan_document = read(Path::new(plan["path"].as_str().ok_or("plan path absent")?))?;
    let selected_count = plan_document["selected_ids"].as_array().map(Vec::len);
    Ok(
        json!({"schema":"polyitan-audio-summary-v2","instance":INSTANCE,"updated_utc":io::utc(),"terminal_for_current_invocation":terminal,
        "unresolved_dispatcher_failures":dispatcher_failures,"dispatcher_failure_count":dispatcher_failures.len(),
        "plan_sha256":plan["sha256"],"inventory_observations":inventory["observations"].as_array().map(Vec::len),
        "metadata_complete":inventory["metadata_complete"],"acquisition_terminal_receipt_present":args.acquisition.join("acquisition-complete.json").is_file(),
        "downloaded_recordings":ready,"analyzed_or_explicitly_unsupported":observations.len(),"observation_statuses":counts,"arms":arms,
        "native_global_distinct_verified_pdus":global.len(),"native_global_distinct_pdu_bytes":global.iter().map(|s|s.len()/2).sum::<usize>(),
        "observations":observations,"current_invocation_wall_seconds":elapsed,
        "selected_recordings":selected_count,
        "all_selected_have_result":observations.len() == selected_count.unwrap_or(ready) && dispatcher_failures.is_empty(),
        "missing_unsupported_and_timeouts_are_not_zero_recovery":true,"candidate_counts_are_not_all_verified_telemetry":true,
        "native_audio_is_our_rust_receiver_not_production_satnogs":true,"strict_ui_is_not_a_universal_telemetry_metric":true,
        "paired_benchmark_or_archive_gain_claim":false,"publication_ready":false,"deployment_ready":false}),
    )
}

fn run() -> Result<(), String> {
    let mut args = Args::parse();
    if !(1..=4).contains(&args.workers)
        || !(1..=4).contains(&args.threads)
        || args.workers * args.threads > 8
        || !(30..=3600).contains(&args.decoder_seconds)
        || args.watch_seconds > 7 * 86400
    {
        return Err("invalid bounded worker/thread/time contract".into());
    }
    args.acquisition = args.acquisition.canonicalize().map_err(|e| e.to_string())?;
    fs::create_dir_all(&args.output).map_err(|e| e.to_string())?;
    args.output = args.output.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(args.output.join("analysis.lock"))
        .map_err(|e| e.to_string())?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("analysis output is owned by another dispatcher".into());
    }
    let mut registry = formats::build_satyaml_registry(&[PathBuf::from(SATYAML)], 4 * 1024 * 1024)?;
    // CANVAS is absent from the installed historical catalogue; this exact
    // G3RUH 9600 profile is the previously exercised frozen OLD20 contract.
    if registry.get_by_norad(68635).is_none() && !registry.conflicted_norads.contains(&68635) {
        let canvas = json!({"name":"CANVAS frozen prior OLD20 contract","norad":68635,"data":{"Raw":"unknown"},
            "transmitters":{"9600 G3RUH":{"frequency":437250000,"modulation":"FSK","baudrate":9600,"framing":"AX.25 G3RUH","data":["Raw"]}}});
        let path = args.output.join("canvas-prior-profile.yml");
        if path.exists() {
            if read(&path)? != canvas {
                return Err("CANVAS prior profile changed".into());
            }
        } else {
            input::write_json_new(&path, &canvas)?;
        }
        let p = formats::parse_satyaml_document(&canvas, &path.display().to_string())?;
        registry.norad_index.insert(68635, p.selector.clone());
        registry.profiles.push(p);
    }
    if registry.get_by_norad(25544).is_none() && !registry.conflicted_norads.contains(&25544) {
        // Explicit APRS/AX.25 mapping, not a generic framing assumption for AFSK.
        // The private API identifies the ISS transmitter as Mode V APRS.
        let iss = json!({"name":"ISS explicit Mode V APRS","norad":25544,"data":{"Raw":"unknown"},
            "transmitters":{"145825 APRS":{"frequency":145825000,"modulation":"AFSK","baudrate":1200,"af_carrier":1700,"deviation":500,"framing":"AX.25","data":["Raw"]}}});
        let path = args.output.join("iss-aprs-profile.yml");
        if path.exists() {
            if read(&path)? != iss {
                return Err("ISS explicit profile changed".into());
            }
        } else {
            input::write_json_new(&path, &iss)?;
        }
        let p = formats::parse_satyaml_document(&iss, &path.display().to_string())?;
        registry.norad_index.insert(25544, p.selector.clone());
        registry.profiles.push(p);
    }
    let original_registry = registry.clone();
    let supplemental_identity = args
        .supplemental_profiles
        .as_ref()
        .map(|p| load_supplemental(p, &args.output, &mut registry))
        .transpose()?;
    let inventory = read(&args.acquisition.join("inventory.json"))?;
    if args.only_newly_routable && inventory["metadata_complete"] != true {
        return Err("newly-routable follow-up requires a complete metadata snapshot".into());
    }
    let committed_ids: BTreeSet<_> = ready_ids(&args.acquisition)?.into_iter().collect();
    let mut routing_rows = vec![];
    let mut selected_ids = BTreeSet::new();
    for metadata in inventory["observations"]
        .as_array()
        .ok_or("inventory observations absent")?
    {
        let id = observation_id(metadata)?;
        if !committed_ids.contains(&id) {
            continue;
        }
        let old = route_v1(metadata, &original_registry);
        let new = route(metadata, &registry);
        // The acquisition is an original-OGG cohort. Exact rate is gated again
        // after opening the shared WAV; this is metadata-only preliminary routing.
        let backend_routing = backend_routes(metadata, &new, None, true);
        let any_routed = any_backend_routed(&backend_routing);
        let newly = old["status"] != "routed" && any_routed;
        if !args.only_newly_routable || newly {
            selected_ids.insert(id);
        }
        routing_rows.push(json!({"observation_id":id,"satellite":metadata["satellite_name"],"norad":metadata["norad_cat_id"],
            "mode":metadata["transmitter_mode"],"old_status":old["status"],"new_status":new["status"],"newly_routable":newly,
            "new_reason":new["reason"],"selected":selected_ids.contains(&id),"routing":new,
            "any_backend_routed":any_routed,"backend_routing":backend_routing}));
    }
    let routing_audit = json!({"schema":"polyitan-routing-coverage-v3","instance":INSTANCE,"inventory":identity(&args.acquisition.join("inventory.json"))?,
        "downloaded":committed_ids.len(),"selected":selected_ids.len(),"newly_routable":routing_rows.iter().filter(|r| r["newly_routable"] == true).count(),
        "routable":routing_rows.iter().filter(|r| r["any_backend_routed"] == true).count(),
        "gr_satellites_routable":routing_rows.iter().filter(|r| r["new_status"] == "routed").count(),"observations":routing_rows});
    let children = child_bindings(&args)?;
    let artifacts = [
        child_binary(&args, "native_audio")?.0,
        child_binary(&args, "innovation_v2")?.0,
        Path::new(GRSAT),
        Path::new("/usr/bin/atest"),
        Path::new("/usr/bin/ffmpeg"),
    ]
    .iter()
    .map(|s| identity(s))
    .collect::<Result<Vec<_>, _>>()?;
    let profiles = registry
        .profiles
        .iter()
        .map(|p| identity(Path::new(&p.selector)))
        .collect::<Result<Vec<_>, _>>()?;
    let plan_path = args.output.join("plan.json");
    let proposed = json!({"schema":"polyitan-audio-plan-v3","instance":INSTANCE,"acquisition":args.acquisition,
        "workers":args.workers,"threads":args.threads,"decoder_seconds":args.decoder_seconds,"artifacts":artifacts,"profiles":profiles,
        "child_binaries":children,"child_identity_contract":"explicit path/SHA256 pairs or documented frozen defaults; independently named in plan and verified before/after every native child execution",
        "runner":identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"source_inventory_frozen":false,
        "selection":"committed recordings, descending private ID; optional metadata-only newly-routable filter, never frame outcomes",
        "supplemental_catalog":supplemental_identity,"only_newly_routable":args.only_newly_routable,"selected_ids":if args.only_newly_routable {json!(selected_ids)} else {Value::Null},
        "pcm_contract":"original first audio stream, mono PCM16; bitexact WAV container; no gain, resampling, downmix or crop",
        "pcm16_conversion_may_clip_vorbis_overshoot":true,"pcm_contract_is_not_lossless_float_audio":true,
        "native_contract":"frozen generic FSK/AFSK AX.25 and innovation G3RUH 9600 subset; not the full progressive bank",
        "native_audio_is_not_production_satnogs":true,
        "external_contract":"offline selected installed gr-satellites profile; output validity depends on framing; Dire Wolf -F0 where compatible",
        "external_auxiliary_application_sinks":"additional_data and transports removed; main deframer output retained; Codec2 voice is not counted as telemetry",
        "routing_contract":"independent per-backend requirements; gr-satellites needs metadata-compatible documented profile; explicit metadata FSK AX.25 G3RUH plus rate can route other backends without satellite definition; generic FSK never implies AX.25",
        "validation_contract":"CRC-passed output, strict AX25 UI and protocol-unresolved output remain separate; KISS timestamps excluded; CRC16 alone does not prove telemetry",
        "reference_bytes_used_for_search":false,"network_submission":false,"python_external_gr_satellites_only":true});
    if plan_path.exists() {
        if !args.resume {
            return Err("existing output requires --resume".into());
        }
        if read(&plan_path)? != proposed {
            return Err("frozen analysis plan differs on resume".into());
        }
    } else {
        input::write_json_new(&plan_path, &proposed)?;
        input::write_json_new(&args.output.join("satyaml-registry.json"), &json!(registry))?;
    }
    io::replace_json(&args.output.join("routing-coverage.json"), &routing_audit)?;
    if args.routing_only {
        println!(
            "{}",
            json!({"downloaded":routing_audit["downloaded"],"selected":routing_audit["selected"],"newly_routable":routing_audit["newly_routable"],"routable":routing_audit["routable"],"dsp_launched":false})
        );
        return Ok(());
    }
    let plan = identity(&plan_path)?;
    let started = Instant::now();
    io::replace_json(
        &args.output.join("summary.json"),
        &summarize(&args, &plan, 0.0, false)?,
    )?;
    let mut attempted = BTreeSet::new();
    let mut launched = 0usize;
    loop {
        let mut ready = vec![];
        for id in ready_ids(&args.acquisition)? {
            if args.only_newly_routable && !selected_ids.contains(&id) {
                continue;
            }
            if attempted.contains(&id) {
                continue;
            }
            let path = args.output.join(format!("obs-{id}/result.json"));
            if path.is_file() {
                let r = read(&path)?;
                if r["plan_sha256"] != plan["sha256"]
                    || r["instance"] != INSTANCE
                    || r["observation_id"].as_u64() != Some(id)
                {
                    return Err("existing observation belongs to another plan/instance".into());
                }
                verify(&r["source"])?;
                verify(&r["metadata"])?;
                if let Some(artifacts) = r["preparation_artifacts"].as_array() {
                    for artifact in artifacts {
                        verify(artifact)?;
                    }
                }
                for (name, arm) in r["arms"].as_object().ok_or("previous arms absent")? {
                    if matches!(
                        arm["status"].as_str(),
                        Some(
                            "unsupported_profile"
                                | "unsupported_backend_contract"
                                | "unsupported_audio_representation"
                        )
                    ) {
                        continue;
                    }
                    let previous = committed(
                        &args.output.join(format!("obs-{id}/arms/{name}")),
                        &r["source"],
                        &plan,
                    )?
                    .ok_or("previous arm commit absent")?;
                    if previous != *arm {
                        return Err("observation summary differs from durable arm commit".into());
                    }
                }
                if !args.retry_failed
                    || !matches!(
                        r["status"].as_str(),
                        Some("partially_complete" | "failed_or_incomplete")
                    )
                {
                    continue;
                }
            }
            ready.push(id);
        }
        if let Some(max) = args.max_new_observations {
            ready.truncate(max.saturating_sub(launched));
        }
        // Frequent durable summaries and discovery while the downloader runs.
        ready.truncate(args.workers * 2);
        if !ready.is_empty() {
            let cursor = AtomicUsize::new(0);
            let errors = std::sync::Mutex::new(Vec::<(u64, String)>::new());
            std::thread::scope(|scope| {
                for _ in 0..args.workers {
                    let args = &args;
                    let ready = &ready;
                    let cursor = &cursor;
                    let registry = &registry;
                    let plan = &plan;
                    let errors = &errors;
                    scope.spawn(move || {
                        loop {
                            let index = cursor.fetch_add(1, Ordering::Relaxed);
                            let Some(id) = ready.get(index) else {
                                break;
                            };
                            if let Err(error) = run_observation(args, *id, registry, plan) {
                                errors.lock().unwrap().push((*id, error));
                            }
                        }
                    });
                }
            });
            launched += ready.len();
            attempted.extend(ready);
            let errors = errors.into_inner().unwrap();
            if !errors.is_empty() {
                record_dispatcher_errors(&args.output, errors)?;
            }
        }
        let mut pending = false;
        for id in ready_ids(&args.acquisition)? {
            if args.only_newly_routable && !selected_ids.contains(&id) {
                continue;
            }
            if attempted.contains(&id) {
                continue;
            }
            let path = args.output.join(format!("obs-{id}/result.json"));
            if !path.is_file()
                || (args.retry_failed
                    && matches!(
                        read(&path)?["status"].as_str(),
                        Some("partially_complete" | "failed_or_incomplete")
                    ))
            {
                pending = true;
                break;
            }
        }
        let terminal = args.max_new_observations.is_some_and(|n| launched >= n)
            || (!pending
                && (started.elapsed() >= Duration::from_secs(args.watch_seconds)
                    || acquisition_finished(&args.acquisition)?));
        let summary = summarize(&args, &plan, started.elapsed().as_secs_f64(), terminal)?;
        io::replace_json(&args.output.join("summary.json"), &summary)?;
        if terminal {
            if summary["dispatcher_failure_count"].as_u64().unwrap_or(1) != 0 {
                return Err("analysis incomplete: dispatcher failures are recorded in summary and error history".into());
            }
            println!(
                "{}",
                serde_json::to_string(&summary["observation_statuses"]).unwrap()
            );
            break;
        }
        std::thread::sleep(Duration::from_secs(2));
    }
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("polyitan_audio_analyze: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn registry() -> formats::SatYamlRegistry {
        let p=formats::parse_satyaml_document(&json!({"name":"Test","norad":123,"transmitters":{"fsk":{"modulation":"FSK","framing":"AX.25 G3RUH","baudrate":9600,"frequency":437000000}}}),"test.yml").unwrap();
        formats::SatYamlRegistry {
            profiles: vec![p],
            norad_index: BTreeMap::from([(123, "test.yml".into())]),
            ..Default::default()
        }
    }
    #[test]
    fn metadata_routing_accepts_explicit_fsk_family_and_rejects_conflicts() {
        let mut m = json!({"id":1,"norad_cat_id":123,"transmitter_mode":"GMSK","transmitter_baud":9600,"transmitter_downlink_low":437000000});
        assert_eq!(route(&m, &registry())["status"], "routed");
        m["transmitter_baud"] = 4800.into();
        assert_ne!(route(&m, &registry())["status"], "routed");
        m["transmitter_baud"] = 9600.into();
        m["transmitter_mode"] = "BPSK".into();
        assert_ne!(route(&m, &registry())["status"], "routed");
    }
    #[test]
    fn profile_norad_is_not_private_observation_id() {
        assert_eq!(
            route(&json!({"id":123,"norad_cat_id":456}), &registry())["status"],
            "unsupported_profile"
        );
    }
    #[test]
    fn selection_retains_dsp_parameters_but_only_raw_sink() {
        let r = route(&json!({"id":1,"norad_cat_id":123}), &registry());
        let p = selected_profile(&r).unwrap();
        assert_eq!(p["transmitters"]["selected"]["baudrate"], 9600);
        assert_eq!(p["transmitters"]["selected"]["data"], json!(["Raw"]));
    }
    #[test]
    fn selection_drops_dangling_codec2_sink_without_changing_demodulator() {
        let mut r = route(&json!({"id":1,"norad_cat_id":123}), &registry());
        r["transmitter"]["metadata"]["additional_data"] = json!({"codec2":"Codec2"});
        r["transmitter"]["metadata"]["transports"] = json!(["Uploader"]);
        r["transmitter"]["metadata"]["deviation"] = json!(2400);
        let p = selected_profile(&r).unwrap();
        let tx = &p["transmitters"]["selected"];
        assert!(tx.get("additional_data").is_none());
        assert!(tx.get("transports").is_none());
        assert_eq!(tx["deviation"], 2400);
        assert_eq!(tx["framing"], "AX.25 G3RUH");
        assert_eq!(tx["modulation"], "FSK");
        assert_eq!(tx["data"], json!(["Raw"]));
    }
    #[test]
    fn child_overrides_require_both_path_and_hash() {
        assert!(
            Args::try_parse_from([
                "test",
                "--acquisition",
                "/tmp/a",
                "--output",
                "/tmp/b",
                "--native-binary",
                "/tmp/native"
            ])
            .is_err()
        );
        assert!(
            Args::try_parse_from([
                "test",
                "--acquisition",
                "/tmp/a",
                "--output",
                "/tmp/b",
                "--innovation-sha256",
                NATIVE_SHA
            ])
            .is_err()
        );
        let mut args =
            Args::try_parse_from(["test", "--acquisition", "/tmp/a", "--output", "/tmp/b"])
                .unwrap();
        assert_eq!(
            child_binary(&args, "native_audio").unwrap(),
            (Path::new(NATIVE), NATIVE_SHA)
        );
        assert_eq!(
            child_binary(&args, "innovation_v2").unwrap(),
            (Path::new(INNOVATION), INNOVATION_SHA)
        );
        args.native_binary = Some(PathBuf::from("relative"));
        args.native_sha256 = Some(NATIVE_SHA.into());
        assert!(child_binary(&args, "native_audio").is_err());
        args.native_binary = Some(PathBuf::from("/tmp/native"));
        args.native_sha256 = Some("A".repeat(64));
        assert!(child_binary(&args, "native_audio").is_err());
        args.native_sha256 = Some(NATIVE_SHA.into());
        assert_eq!(
            child_binary(&args, "innovation_v2").unwrap(),
            (Path::new(INNOVATION), INNOVATION_SHA)
        );
    }
    #[test]
    fn independent_child_bindings_reject_wrong_hash_or_swapped_plan_arms() {
        let d = tempfile::tempdir().unwrap();
        let native = d.path().join("native");
        let innovation = d.path().join("innovation");
        input::write_json_new(&native, &json!({"fixture":"native"})).unwrap();
        input::write_json_new(&innovation, &json!({"fixture":"innovation"})).unwrap();
        let n = identity(&native).unwrap();
        let i = identity(&innovation).unwrap();
        let mut args = Args::try_parse_from([
            "test",
            "--acquisition",
            "/tmp/a",
            "--output",
            "/tmp/b",
            "--native-binary",
            native.to_str().unwrap(),
            "--native-sha256",
            n["sha256"].as_str().unwrap(),
            "--innovation-binary",
            innovation.to_str().unwrap(),
            "--innovation-sha256",
            i["sha256"].as_str().unwrap(),
        ])
        .unwrap();
        let bindings = child_bindings(&args).unwrap();
        let plan = json!({"child_binaries":bindings});
        assert_eq!(
            bound_child_identity(&args, "native_audio", &plan).unwrap(),
            n
        );
        assert_eq!(
            bound_child_identity(&args, "innovation_v2", &plan).unwrap(),
            i
        );
        let swapped = json!({"child_binaries":{"native_audio":i,"innovation_v2":n}});
        assert!(bound_child_identity(&args, "native_audio", &swapped).is_err());
        assert!(bound_child_identity(&args, "innovation_v2", &swapped).is_err());
        args.native_sha256 = Some("0".repeat(64));
        assert!(checked_child_identity(&args, "native_audio").is_err());
        assert_eq!(checked_child_identity(&args, "innovation_v2").unwrap(), i);
        assert!(bound_child_identity(&args, "innovation_v2", &json!({})).is_err());
    }
    #[test]
    fn wrong_fcs_is_not_a_native_frame() {
        assert!(native_pdus(&json!(["94a662b2a0826094a662b29eb2e103f061620000"])).is_err());
    }
    #[test]
    fn absent_native_frames_are_not_zero() {
        assert!(native_pdus(&Value::Null).is_err());
        assert!(set(&Value::Null).is_err());
    }
    #[test]
    fn missing_satellite_profile_does_not_gate_explicit_other_backends() {
        let m = json!({"id":1,"norad_cat_id":999999,"transmitter_mode":"FSK AX.25 G3RUH","transmitter_baud":9600});
        let routes = backend_routes(&m, &route(&m, &registry()), Some(48000), true);
        assert_eq!(
            routes["gr_satellites"]["status"],
            "unsupported_backend_contract"
        );
        for name in ["native_audio", "direwolf", "innovation_v2"] {
            assert_eq!(routes[name]["status"], "routed");
            assert_eq!(
                routes[name]["parameters"]["routing_evidence"]["kind"],
                "explicit_acquired_metadata_not_inferred_from_signal"
            );
        }
    }
    #[test]
    fn generic_fsk_and_missing_baud_never_invent_framing() {
        for label in ["FSK", "GMSK", "GFSK", "BPSK", "AFSK", "FSK AX.100 Mode 5"] {
            let m = json!({"id":1,"norad_cat_id":999999,"transmitter_mode":label,"transmitter_baud":9600});
            assert!(
                !any_backend_routed(&backend_routes(
                    &m,
                    &route(&m, &registry()),
                    Some(48000),
                    true
                )),
                "{label}"
            );
        }
        let m = json!({"id":1,"norad_cat_id":999999,"transmitter_mode":"FSK AX.25 G3RUH"});
        assert!(!any_backend_routed(&backend_routes(
            &m,
            &route(&m, &registry()),
            Some(48000),
            true
        )));
    }
    #[test]
    fn innovation_representation_limit_does_not_gate_other_arms() {
        let m = json!({"id":1,"norad_cat_id":123});
        for (rate, ogg) in [(44100, true), (48000, false)] {
            let routes = backend_routes(&m, &route(&m, &registry()), Some(rate), ogg);
            assert_eq!(
                routes["innovation_v2"]["status"],
                "unsupported_backend_contract"
            );
            assert_eq!(routes["native_audio"]["status"], "routed");
            assert_eq!(routes["gr_satellites"]["status"], "routed");
        }
    }
    #[test]
    fn direwolf_g3ruh_modem_not_claimed_for_unscrambled_fsk() {
        let mut r = registry();
        r.profiles[0].transmitters[0].framing = "AX.25".into();
        let m = json!({"id":1,"norad_cat_id":123});
        let routes = backend_routes(&m, &route(&m, &r), Some(48000), true);
        assert_eq!(routes["native_audio"]["status"], "routed");
        assert_eq!(routes["direwolf"]["status"], "unsupported_backend_contract");
    }
    #[test]
    fn received_crc_non_ui_is_retained_but_not_promoted_to_telemetry() {
        let ui = hex::decode("94a662b2a0826094a662b29eb2e103f06162").unwrap();
        assert!(protocol::parse_ax25_ui(&ui).is_some());
        let mut non_ui = ui.clone();
        non_ui[14] = 0;
        let frames: Vec<_> = [&ui, &non_ui]
            .iter()
            .map(|body| {
                let mut frame = body.to_vec();
                frame.extend(protocol::crc16_x25(body).to_le_bytes());
                hex::encode(frame)
            })
            .collect();
        let pdus = native_pdus(&json!(frames)).unwrap();
        assert_eq!(pdus.len(), 2);
        let v = validation_accounting(&pdus, true, true).unwrap();
        assert_eq!(v["strict_ui_unique_count"], 1);
        assert_eq!(v["crc_passed_unique_count"], 2);
        assert_eq!(v["crc_passed_non_ui_unique_count"], 1);
        assert_eq!(v["protocol_unresolved_unique_count"], 1);
        assert_eq!(v["independent_fcs_verified"], true);
    }
    #[test]
    fn protocol_without_validator_remains_null_even_if_bytes_look_like_ui() {
        let pdus = BTreeSet::from(["94a662b2a0826094a662b29eb2e103f06162".into()]);
        let v = validation_accounting(&pdus, false, false).unwrap();
        assert!(v["strict_ui_unique_count"].is_null());
        assert!(v["crc_passed_unique_count"].is_null());
        assert_eq!(v["protocol_unresolved_unique_count"], 1);
        assert_eq!(v["outcome"], "unresolved_candidates");
        assert_eq!(v["independent_fcs_verified"], false);
        assert!(validation_accounting(&BTreeSet::from(["zz".into()]), true, false).is_err());
    }
    #[test]
    fn gr_timestamps_never_become_candidate_frames() {
        let timestamp = formats::encode_kiss_record(0, 9, &12345u64.to_be_bytes()).unwrap();
        let mut bytes = timestamp.clone();
        bytes.extend(formats::encode_kiss_record(0, 0, &[0xc0, 0xdb, 1]).unwrap());
        let parsed = parse_gr_transport(&bytes).unwrap();
        assert_eq!(parsed.timestamp_commands.len(), 1);
        assert_eq!(parsed.data_frames.len(), 1);
        assert_eq!(parsed.data_frames[0].payload, [0xc0, 0xdb, 1]);
        assert_eq!(parsed.data_frames[0].timestamp_ms, Some(12345));
        assert!(
            parse_gr_transport(&timestamp)
                .unwrap()
                .data_frames
                .is_empty()
        );
        assert!(parse_gr_transport(&[0xc0, 0, 1]).is_err());
        assert!(parse_gr_transport(&[0xc0, 9, 1, 0xc0]).is_err());
        assert!(parse_gr_transport(&formats::encode_kiss_record(0, 1, &[1]).unwrap()).is_err());
    }
    #[test]
    #[ignore = "integration gate executes frozen native and Dire Wolf on synthetic silence"]
    fn missing_gr_profile_still_executes_other_receivers_end_to_end() {
        let d = tempfile::tempdir().unwrap();
        let acquisition = d.path().join("acquisition");
        let acquired = acquisition.join("obs-1");
        let output = d.path().join("analysis");
        fs::create_dir_all(&acquired).unwrap();
        fs::create_dir(&output).unwrap();
        let wav = acquired.join("capture.wav");
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
        for _ in 0..48000 {
            writer.write_sample(0i16).unwrap();
        }
        writer.finalize().unwrap();
        let m = json!({"id":1,"norad_cat_id":999999,"transmitter_mode":"FSK AX.25 G3RUH","transmitter_baud":9600});
        input::write_json_new(&acquired.join("metadata.json"), &m).unwrap();
        input::write_json_new(&acquired.join("downloaded.json"), &json!({"instance":INSTANCE,"observation_id":1,
            "source":identity(&wav).unwrap(),"metadata":identity(&acquired.join("metadata.json")).unwrap()})).unwrap();
        let args = Args::try_parse_from([
            "test",
            "--acquisition",
            acquisition.to_str().unwrap(),
            "--output",
            output.to_str().unwrap(),
            "--decoder-seconds",
            "30",
            "--threads",
            "1",
        ])
        .unwrap();
        input::write_json_new(
            &output.join("plan.json"),
            &json!({"selected_ids":[1],"child_binaries":child_bindings(&args).unwrap()}),
        )
        .unwrap();
        let plan = identity(&output.join("plan.json")).unwrap();
        run_observation(&args, 1, &formats::SatYamlRegistry::default(), &plan).unwrap();
        let r = read(&output.join("obs-1/result.json")).unwrap();
        assert_eq!(r["status"], "complete", "{r}");
        assert_eq!(
            r["arms"]["gr_satellites"]["status"],
            "unsupported_backend_contract"
        );
        assert!(!output.join("obs-1/selected-profile.yml").exists());
        for name in ["native_audio", "direwolf"] {
            assert_eq!(r["arms"][name]["status"], "complete", "{}", r["arms"][name]);
            assert_eq!(r["arms"][name]["outcome"], "no_frames");
            assert_eq!(r["arms"][name]["strict_ui_unique_count"], 0);
        }
        let summary = summarize(&args, &plan, 1.0, true).unwrap();
        assert_eq!(summary["arms"]["native_audio"]["complete_no_frames"], 1);
        assert_eq!(summary["arms"]["gr_satellites"]["unsupported"], 1);
        assert_eq!(summary["arms"]["gr_satellites"]["complete"], 0);
    }
    #[test]
    fn duv_alias_matches_subaudio_and_not_ax25_fsk() {
        assert_eq!(mode("DUV"), mode("FSK subaudio"));
        assert_ne!(mode("DUV"), mode("FSK"));
    }
    #[test]
    fn incomplete_metadata_receipt_is_not_eof() {
        let d = tempfile::tempdir().unwrap();
        input::write_json_new(
            &d.path().join("acquisition-complete.json"),
            &json!({"status":"incomplete","metadata_complete":false}),
        )
        .unwrap();
        assert!(!acquisition_finished(d.path()).unwrap());
    }
    #[test]
    fn duplicate_transmitters_are_explicitly_ambiguous() {
        let mut r = registry();
        let mut other = r.profiles[0].transmitters[0].clone();
        other.transmitter_id = "second".into();
        r.profiles[0].transmitters.push(other);
        assert_eq!(
            route(&json!({"id":1,"norad_cat_id":123}), &r)["status"],
            "unsupported_or_ambiguous_profile"
        );
    }
    #[test]
    fn documented_framing_variants_are_all_selected_only_with_explicit_channel() {
        let mut r = registry();
        let mut other = r.profiles[0].transmitters[0].clone();
        other.transmitter_id = "NRZ variant".into();
        other.framing = "Astrocast FX.25 NRZ".into();
        r.profiles[0].transmitters[0].framing = "Astrocast FX.25 NRZ-I".into();
        r.profiles[0].transmitters.push(other);
        let m = json!({"id":1,"norad_cat_id":123,"transmitter_mode":"FSK","transmitter_baud":9600,"transmitter_downlink_low":437000000});
        assert_ne!(route_v1(&m, &r)["status"], "routed");
        let routed = route(&m, &r);
        assert_eq!(routed["status"], "routed");
        assert!(routed["transmitter"].is_null());
        assert!(routed["native_mode"].is_null());
        assert_eq!(
            selected_profile(&routed).unwrap()["transmitters"]
                .as_object()
                .unwrap()
                .len(),
            2
        );
        assert_ne!(
            route(&json!({"id":1,"norad_cat_id":123}), &r)["status"],
            "routed"
        );
    }
    #[test]
    fn explicit_mode5_does_not_route_as_ax25() {
        let mut r = registry();
        let m = json!({"id":1,"norad_cat_id":123,"transmitter_mode":"FSK AX.100 Mode 5","transmitter_baud":9600,"transmitter_downlink_low":437000000});
        assert_ne!(route(&m, &r)["status"], "routed");
        r.profiles[0].transmitters[0].framing = "AX100 ASM+Golay".into();
        assert_eq!(route(&m, &r)["status"], "routed");
        assert!(route(&m, &r)["native_mode"].is_null());
        assert_ne!(route_v1(&m, &r)["status"], "routed");
    }
    #[test]
    fn frozen_supplements_have_evidence_and_cannot_overwrite_existing_satellite() {
        let d = tempfile::tempdir().unwrap();
        let path = Path::new("config/polyitan-supplemental-profiles-20260911.json");
        let mut registry = formats::SatYamlRegistry::default();
        load_supplemental(path, d.path(), &mut registry).unwrap();
        assert_eq!(registry.profiles.len(), 12);
        assert_eq!(
            registry.get_by_norad(98395).unwrap().transmitters[0].metadata["deviation"],
            2400
        );
        assert!(load_supplemental(path, d.path(), &mut registry).is_err());
        let mut fresh = formats::SatYamlRegistry::default();
        load_supplemental(path, d.path(), &mut fresh).unwrap();
        let changed = d.path().join("supplemental-profiles/26931.yml");
        io::replace_json(&changed, &json!({"changed":true})).unwrap();
        assert!(
            load_supplemental(path, d.path(), &mut formats::SatYamlRegistry::default()).is_err()
        );
    }
    #[test]
    fn supplements_do_not_claim_duv_cw_or_unknown_framing() {
        let d = tempfile::tempdir().unwrap();
        let mut registry = formats::SatYamlRegistry::default();
        load_supplemental(
            Path::new("config/polyitan-supplemental-profiles-20260911.json"),
            d.path(),
            &mut registry,
        )
        .unwrap();
        for norad in [43017, 43770, 98330, 98600] {
            assert!(registry.get_by_norad(norad).is_none());
        }
        assert_ne!(
            route(
                &json!({"id":1,"norad_cat_id":62391,"transmitter_mode":"CW","transmitter_downlink_low":436925000}),
                &registry
            )["status"],
            "routed"
        );
        let p = registry.get_by_norad(98329).unwrap();
        assert!(
            read(Path::new(&p.selector)).unwrap()["x-evidence"]["basis"]
                .as_str()
                .unwrap()
                .contains("hypothesis")
        );
    }
    #[test]
    fn dispatcher_failure_history_is_cumulative_and_missing_results_are_explicit() {
        let d = tempfile::tempdir().unwrap();
        record_dispatcher_errors(d.path(), vec![(1, "bad hash".into())]).unwrap();
        record_dispatcher_errors(d.path(), vec![(2, "bad metadata".into())]).unwrap();
        assert_eq!(
            unresolved_dispatcher_errors(d.path()).unwrap(),
            BTreeSet::from([1, 2])
        );
        fs::create_dir_all(d.path().join("obs-1")).unwrap();
        input::write_json_new(
            &d.path().join("obs-1/result.json"),
            &json!({"observation_id":1}),
        )
        .unwrap();
        assert_eq!(
            unresolved_dispatcher_errors(d.path()).unwrap(),
            BTreeSet::from([2])
        );
        assert_eq!(
            read(&d.path().join("dispatcher-errors.json")).unwrap()["errors"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
    }
    #[test]
    fn metadata_modulation_alias_is_scoped_to_documented_transmitter_only() {
        let mut r = registry();
        let m = json!({"id":1,"norad_cat_id":123,"transmitter_mode":"AFSK","transmitter_baud":9600,"transmitter_downlink_low":437000000});
        assert_ne!(route(&m, &r)["status"], "routed");
        r.profiles[0].transmitters[0]
            .metadata
            .insert("metadata_mode_aliases".into(), json!(["AFSK"]));
        let routed = route(&m, &r);
        assert_eq!(routed["status"], "routed");
        assert_eq!(routed["native_mode"], "fsk");
        assert_ne!(route_v1(&m, &r)["status"], "routed");
        assert!(
            selected_profile(&routed).unwrap()["transmitters"]["selected"]
                .get("metadata_mode_aliases")
                .is_none()
        );
    }
    #[test]
    #[ignore = "integration gate requires installed offline gr-satellites"]
    fn all_supplements_construct_real_gr_satellites_on_silence() {
        let d = tempfile::tempdir().unwrap();
        let mut registry = formats::SatYamlRegistry::default();
        load_supplemental(
            Path::new("config/polyitan-supplemental-profiles-20260911.json"),
            d.path(),
            &mut registry,
        )
        .unwrap();
        let wav = d.path().join("silence.wav");
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
        for _ in 0..48000 {
            writer.write_sample(0i16).unwrap();
        }
        writer.finalize().unwrap();
        for p in &registry.profiles {
            for (i, tx) in p.transmitters.iter().enumerate() {
                let out = d.path().join(format!("{}-{i}", p.norad_id));
                fs::create_dir(&out).unwrap();
                let m = json!({"id":1,"norad_cat_id":p.norad_id,"transmitter_mode":tx.modulation,"transmitter_baud":tx.metadata["baudrate"],"transmitter_downlink_low":tx.metadata["frequency"]});
                let routed = route(&m, &registry);
                assert_eq!(routed["status"], "routed", "{}", p.name);
                let selected = out.join("profile.yml");
                input::write_json_new(&selected, &selected_profile(&routed).unwrap()).unwrap();
                let result = io::execute(
                    Path::new(GRSAT),
                    &[
                        selected.display().to_string(),
                        "--wavfile".into(),
                        wav.display().to_string(),
                        "--kiss_out".into(),
                        out.join("frames.kiss").display().to_string(),
                    ],
                    &out,
                    "run",
                    30,
                    16 * 1024 * 1024,
                )
                .unwrap();
                assert_eq!(result["success"], true, "{}: {:?}", p.name, result);
                let frames =
                    formats::parse_kiss(&fs::read(out.join("frames.kiss")).unwrap()).unwrap();
                assert!(
                    frames.data_frames.is_empty(),
                    "{} emitted data on silence",
                    p.name
                );
            }
        }
    }
}
