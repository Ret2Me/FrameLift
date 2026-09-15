//! Conservative, outcome-independent local recording exposure / pass-proximity audit.
//! Reads metadata and filesystem evidence, never waveform contents; ignores outcomes/payload fields.
use chrono::{DateTime, Utc};
use clap::Parser;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const COHORT_SHA: &str = "dc473c071d98974ddaba15d518a3048dcff87f46fe73e0088aeefd874e0abb1a";
const ENTRY_CAP: usize = 2_000_000;
const JSON_CAP: u64 = 128 * 1024 * 1024;
const JSON_TOTAL_CAP: u64 = 4 * 1024 * 1024 * 1024;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    work: PathBuf,
    #[arg(long)]
    cohort: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 120)]
    proximity_minutes: u64,
    #[arg(long)]
    supplemental_metadata: Vec<PathBuf>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Record {
    namespace: String,
    id: u64,
    norad: u64,
    start: i64,
    end: i64,
    station: Option<u64>,
    metadata_sources: BTreeSet<String>,
}
#[derive(Clone, Debug, Serialize)]
struct Evidence {
    path: String,
    bytes: u64,
    modified_unix_ns: Option<u128>,
    kind: String,
    keys: Vec<String>,
    lineage_contexts: Vec<Value>,
}
fn now() -> String {
    DateTime::<Utc>::from_timestamp(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64,
        0,
    )
    .unwrap()
    .to_rfc3339()
}
fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn read_json(path: &Path) -> Result<(Value, Value), String> {
    let size = fs::metadata(path).map_err(|e| e.to_string())?.len();
    if size > JSON_CAP {
        return Err(format!("metadata exceeds128MiB: {}", path.display()));
    }
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    let identity = json!({"path":path,"bytes":bytes.len(),"sha256":sha(&bytes)});
    let value = serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok((value, identity))
}
fn write_new(path: &Path, value: &Value) -> Result<(), String> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut f, value).map_err(|e| e.to_string())?;
    f.write_all(b"\n")
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())
}
fn stamp(path: &Path, kind: &str, keys: Vec<String>) -> Result<Evidence, String> {
    let m = fs::metadata(path).map_err(|e| e.to_string())?;
    Ok(Evidence {
        path: path.display().to_string(),
        bytes: m.len(),
        modified_unix_ns: m
            .modified()
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_nanos()),
        kind: kind.into(),
        keys,
        lineage_contexts: vec![],
    })
}
fn ns(id: u64) -> &'static str {
    if id < 1_000_000 {
        "polyitan"
    } else {
        "public_satnogs"
    }
}
fn key(id: u64) -> String {
    format!("{}:{id}", ns(id))
}
fn is_date_number(s: &str) -> bool {
    s.len() == 8 && s.starts_with("20") && chrono::NaiveDate::parse_from_str(s, "%Y%m%d").is_ok()
}
fn path_ids(path: &str) -> BTreeSet<u64> {
    let mut ids = BTreeSet::new();
    for component in path.split('/') {
        for marker in [
            "obs-",
            "observation-",
            "observation_",
            "satnogs_iq_",
            "satnogs_",
            "canvas-",
            "canvas",
            "public",
        ] {
            if let Some((_, tail)) = component.split_once(marker) {
                let digits = tail
                    .chars()
                    .take_while(|c| c.is_ascii_digit())
                    .collect::<String>();
                if (1..=9).contains(&digits.len()) && !is_date_number(&digits) {
                    if let Ok(id) = digits.parse::<u64>() {
                        if id > 0 {
                            ids.insert(id);
                        }
                    }
                }
            }
        }
        for token in component.split(|c: char| !c.is_ascii_alphanumeric()) {
            if (7..=8).contains(&token.len())
                && token.chars().all(|c| c.is_ascii_digit())
                && !is_date_number(token)
            {
                if let Ok(id) = token.parse::<u64>() {
                    ids.insert(id);
                }
            }
        }
    }
    ids
}
fn parse_time(value: &Value) -> Option<i64> {
    DateTime::parse_from_rfc3339(value.as_str()?)
        .ok()
        .map(|v| v.timestamp())
}
fn record(v: &Value, source: &str) -> Option<Record> {
    let id = v["id"].as_u64().or_else(|| v["observation_id"].as_u64())?;
    let norad = v["norad_cat_id"]
        .as_u64()
        .or_else(|| v["norad_id"].as_u64())?;
    let start = parse_time(&v["start"])?;
    let end = parse_time(&v["end"])?;
    if id == 0 || id >= 1_000_000_000 || norad == 0 || end <= start {
        return None;
    }
    Some(Record {
        namespace: ns(id).into(),
        id,
        norad,
        start,
        end,
        station: v["ground_station"]
            .as_u64()
            .or_else(|| v["station_id"].as_u64()),
        metadata_sources: BTreeSet::from([source.into()]),
    })
}
fn records(v: &Value, source: &str, out: &mut BTreeMap<String, Vec<Record>>) {
    if let Some(r) = record(v, source) {
        let group = out.entry(key(r.id)).or_default();
        if let Some(old) = group.iter_mut().find(|old| {
            old.norad == r.norad
                && old.start == r.start
                && old.end == r.end
                && old.station == r.station
        }) {
            old.metadata_sources.insert(source.into());
        } else {
            group.push(r);
        }
        return;
    }
    match v {
        Value::Object(m) => {
            for (k, x) in m {
                if ![
                    "frames",
                    "full_frames",
                    "union_full_frames",
                    "demoddata",
                    "payload",
                    "payloads",
                    "samples",
                    "bits",
                    "soft",
                    "hex",
                    "data",
                    "client_metadata",
                    "expected_pdu_hex",
                    "verified_pdu_hex",
                ]
                .contains(&k.as_str())
                {
                    records(x, source, out);
                }
            }
        }
        Value::Array(a) => {
            for x in a {
                records(x, source, out);
            }
        }
        _ => {}
    }
}
fn metadata_name(name: &str) -> bool {
    name.ends_with(".json")
        && !name.contains("prior-exposure")
        && !name.ends_with(".http.json")
        && (name == "observation.json"
            || name == "metadata.json"
            || name == "metadata-all.json"
            || name == "snapshot.json"
            || name.starts_with("satnogs-observation-")
            || name.contains("cohort")
            || name.contains("observations")
            || name.contains("observation-index")
            || name.contains("metadata-index"))
}
fn uuid_map(v: &Value, mapping: &mut BTreeMap<String, BTreeSet<u64>>) {
    if let (Some(uuid), Some(norad)) = (
        v["sat_id"].as_str().or_else(|| v["satellite_id"].as_str()),
        v["norad_cat_id"]
            .as_u64()
            .or_else(|| v["norad_id"].as_u64()),
    ) {
        mapping.entry(uuid.to_string()).or_default().insert(norad);
        return;
    }
    match v {
        Value::Array(a) => {
            for x in a {
                uuid_map(x, mapping)
            }
        }
        Value::Object(o) => {
            for (k, x) in o {
                if !["demoddata", "frames", "payload", "data", "client_metadata"]
                    .contains(&k.as_str())
                {
                    uuid_map(x, mapping)
                }
            }
        }
        _ => {}
    }
}
fn mapped_records(
    v: &Value,
    source: &str,
    mapping: &BTreeMap<String, BTreeSet<u64>>,
    out: &mut BTreeMap<String, Vec<Record>>,
) {
    if let (Some(id), Some(uuid)) = (
        v["id"].as_u64().or_else(|| v["observation_id"].as_u64()),
        v["sat_id"].as_str().or_else(|| v["satellite_id"].as_str()),
    ) {
        if let Some(norads) = mapping.get(uuid) {
            for norad in norads {
                records(
                    &json!({"id":id,"norad_cat_id":norad,"start":v["start"],"end":v["end"],"station_id":v["station_id"],"ground_station":v["ground_station"]}),
                    source,
                    out,
                );
            }
        }
        return;
    }
    match v {
        Value::Array(a) => {
            for x in a {
                mapped_records(x, source, mapping, out)
            }
        }
        Value::Object(o) => {
            for (k, x) in o {
                if !["demoddata", "frames", "payload", "data", "client_metadata"]
                    .contains(&k.as_str())
                {
                    mapped_records(x, source, mapping, out)
                }
            }
        }
        _ => {}
    }
}
fn waveform(name: &str) -> bool {
    [
        ".ogg",
        ".wav",
        ".iq",
        ".ci16",
        ".cf32",
        ".sigmf-data",
        ".f32",
        ".raw",
    ]
    .iter()
    .any(|e| name.ends_with(e))
}
fn decoder_artifact(name: &str, relative: &str) -> bool {
    if !name.ends_with(".json")
        || metadata_name(name)
        || name.contains("manifest")
        || name.contains("cohort")
        || name.contains("selection")
        || name.contains("metadata")
        || name.ends_with(".http.json")
        || name.contains("capture.ogg")
        || relative.contains("/metadata/")
    {
        return false;
    }
    [
        "result",
        "decode",
        "phase-first",
        "comparison",
        "satnogs-run",
        "satnogs-source-compatible",
        "own-native",
        "own-innovation",
        "direwolf",
        "receiver",
    ]
    .iter()
    .any(|m| name.contains(m))
}
fn separation(a: &Record, b: &Record) -> i64 {
    if a.end < b.start {
        b.start - a.end
    } else if b.end < a.start {
        a.start - b.end
    } else {
        0
    }
}
fn collides(a: &Record, b: &Record, pad: i64) -> bool {
    a.norad == b.norad && separation(a, b) <= pad
}

fn direct_context_ids(value: &Value) -> BTreeSet<u64> {
    [
        value["observation_id"].as_u64(),
        value["unit"]["observation_id"].as_u64(),
    ]
    .into_iter()
    .flatten()
    .filter(|id| *id > 0 && *id < 100_000_000)
    .collect()
}

fn run(args: Args) -> Result<(), String> {
    if args.proximity_minutes == 0 || args.proximity_minutes > 360 {
        return Err("proximity must be1..360minutes".into());
    }
    let work = args.work.canonicalize().map_err(|e| e.to_string())?;
    let cohort_path = args.cohort.canonicalize().map_err(|e| e.to_string())?;
    let (cohort, cohort_identity) = read_json(&cohort_path)?;
    if cohort_identity["sha256"] != COHORT_SHA || cohort["selected_count"] != 500 {
        return Err("not the frozen corrected500 cohort".into());
    }
    let candidates = cohort["observations"]
        .as_array()
        .ok_or("cohort observations absent")?
        .clone();
    if candidates.len() != 500 {
        return Err("cohort size mismatch".into());
    }
    if let Some(p) = args.output.parent() {
        fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    fs::create_dir(&args.output).map_err(|e| e.to_string())?;
    let output = args.output.canonicalize().map_err(|e| e.to_string())?;
    let created = now();
    let policy = json!({"schema":"recording-exposure-policy-v1","frozen_utc":created,"work_root":work,"cohort":cohort_identity,
        "primary_same_satellite_proximity_minutes":args.proximity_minutes,"sensitivity_minutes":[0,60,90,120,180],
        "proximity_definition":"closed observation intervals overlap or gap <= declared padding; cross-station and transmitter-independent for the same NORAD",
        "physical_orbit_identification_claimed":false,"reason":"conservative temporal same-orbit proxy, not TLE propagation or proof of orbital independence",
        "candidate_selection":"only remove from original500 in frozen rank order; no replacement, reranking or yield-based selection",
        "metadata_only_does_not_count_as_waveform_exposure":true,"waveform_presence_counts_conservatively_even_without_proof_of_decoder_use":true,
        "decoder_attempt_artifact_counts_even_if_source_was_deleted":true,"read_waveform_contents":false,"use_frame_payloads_or_outcomes":false,
        "metadata_container_limit":"Metadata JSON may contain outcome or demoddata fields; only observation identity/NORAD/timing/station are extracted and used",
        "namespace_rule":"IDs<1000000 are the supplied PolyITAN instance; larger IDs public SatNOGS; potential collisions are a scope limitation",
        "scan_entry_cap":ENTRY_CAP,"metadata_file_cap_bytes":JSON_CAP,"metadata_total_cap_bytes":JSON_TOTAL_CAP,
        "live_snapshot_not_atomic":true,"global_nonexposure_proof":false,"new_executable_sha256":sha(&fs::read(std::env::current_exe().map_err(|e|e.to_string())?).map_err(|e|e.to_string())?)});
    write_new(&output.join("policy.json"), &policy)?;
    let mut files = vec![];
    let mut pending = vec![work.clone()];
    let mut scan_issues = vec![];
    let mut entries = 0;
    while let Some(dir) = pending.pop() {
        let listing = match fs::read_dir(&dir) {
            Ok(x) => x,
            Err(e) => {
                scan_issues.push(json!({"path":dir,"reason":format!("directory unreadable: {e}")}));
                continue;
            }
        };
        for entry in listing {
            let entry = entry.map_err(|e| e.to_string())?;
            entries += 1;
            if entries > ENTRY_CAP {
                return Err("work inventory entry cap exceeded".into());
            }
            let path = entry.path();
            if path.starts_with(&output) {
                continue;
            }
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() {
                scan_issues.push(json!({"path":path,"reason":"symlink not followed"}));
                continue;
            }
            if kind.is_dir() {
                pending.push(path);
            } else if kind.is_file() {
                files.push(path);
            }
        }
    }
    files.sort();
    let mut catalog: BTreeMap<String, Vec<Record>> = BTreeMap::new();
    records(&cohort, cohort_path.to_str().unwrap(), &mut catalog);
    let mut metadata_sources = vec![cohort_identity.clone()];
    let mut satellite_uuid_to_norad = BTreeMap::new();
    let mut metadata_bytes = 0;
    for path in &files {
        let name = path.file_name().unwrap().to_string_lossy();
        if !metadata_name(&name) {
            continue;
        }
        let size = fs::metadata(path).map_err(|e| e.to_string())?.len();
        if size > JSON_CAP || metadata_bytes + size > JSON_TOTAL_CAP {
            scan_issues.push(json!({"path":path,"reason":"metadata size/total cap"}));
            continue;
        }
        metadata_bytes += size;
        match read_json(path) {
            Ok((value, identity)) => {
                records(&value, path.to_str().unwrap(), &mut catalog);
                uuid_map(&value, &mut satellite_uuid_to_norad);
                metadata_sources.push(identity);
            }
            Err(error) => scan_issues.push(json!({"path":path,"reason":error})),
        }
    }
    for path in &args.supplemental_metadata {
        let path = path.canonicalize().map_err(|e| e.to_string())?;
        let (value, identity) = read_json(&path)?;
        records(&value, path.to_str().unwrap(), &mut catalog);
        mapped_records(
            &value,
            path.to_str().unwrap(),
            &satellite_uuid_to_norad,
            &mut catalog,
        );
        metadata_sources.push(identity);
    }
    // An artifact can have a hash-only unit directory. Only explicit observation_id
    // at the top level or unit.observation_id is admitted as a directory context.
    // A metadata selection manifest or arbitrary nested IDs never create exposure.
    let mut contexts: BTreeMap<PathBuf, Vec<(BTreeSet<u64>, Value)>> = BTreeMap::new();
    let mut lineage_read_issues = vec![];
    for path in &files {
        let name = path.file_name().unwrap().to_string_lossy();
        if ![
            "normalized-result.json",
            "manifest.json",
            "result.json",
            "decode.json",
        ]
        .contains(&name.as_ref())
        {
            continue;
        }
        if fs::metadata(path).map_err(|e| e.to_string())?.len() > 2 * 1024 * 1024 {
            lineage_read_issues.push(json!({"path":path,"reason":"lineage context exceeds2MiB"}));
            continue;
        }
        match read_json(path) {
            Ok((v, identity)) => {
                let ids = direct_context_ids(&v);
                if !ids.is_empty() {
                    contexts
                        .entry(path.parent().unwrap().to_path_buf())
                        .or_default()
                        .push((ids, identity));
                }
            }
            Err(error) => lineage_read_issues.push(json!({"path":path,"reason":error})),
        }
    }
    let mut evidence = vec![];
    let mut unlinked = vec![];
    let mut exposed_keys = BTreeSet::new();
    let mut roots: BTreeMap<String, usize> = BTreeMap::new();
    for path in &files {
        let relative = path.strip_prefix(&work).unwrap().to_string_lossy();
        let name = path.file_name().unwrap().to_string_lossy();
        let kind = if waveform(&name) {
            "waveform_file_present"
        } else if decoder_artifact(&name, &relative) {
            "decoder_attempt_artifact_present"
        } else {
            continue;
        };
        let mut ids = path_ids(&relative);
        let mut lineage_contexts = vec![];
        let mut ancestor = path.parent();
        while let Some(dir) = ancestor {
            if !dir.starts_with(&work) {
                break;
            }
            if let Some(entries) = contexts.get(dir) {
                for (context_ids, identity) in entries {
                    ids.extend(context_ids);
                    lineage_contexts.push(identity.clone());
                }
            }
            ancestor = dir.parent();
        }
        let keys = ids.iter().map(|id| key(*id)).collect::<Vec<_>>();
        let mut item = stamp(path, kind, keys.clone())?;
        item.lineage_contexts = lineage_contexts;
        if keys.is_empty() {
            unlinked.push(item);
            continue;
        }
        exposed_keys.extend(keys);
        *roots
            .entry(relative.split('/').next().unwrap().into())
            .or_default() += 1;
        evidence.push(item);
    }
    let mut exposures = vec![];
    let mut unresolved = vec![];
    for k in &exposed_keys {
        if let Some(rs) = catalog.get(k) {
            exposures.extend(rs.iter().cloned());
        } else {
            unresolved.push(k.clone());
        }
    }
    exposures.sort_by_key(|r| (r.norad, r.start, r.end, r.id));
    let pad = (args.proximity_minutes * 60) as i64;
    let mut included = vec![];
    let mut excluded = vec![];
    let mut sensitivity = BTreeMap::new();
    for minutes in [0, 60, 90, 120, 180] {
        let mut retained = 0;
        for candidate in &candidates {
            let r = record(candidate, "frozen500").ok_or("invalid candidate timing/NORAD")?;
            if !exposed_keys.contains(&key(r.id))
                && !exposures.iter().any(|e| collides(&r, e, minutes * 60))
            {
                retained += 1;
            }
        }
        sensitivity.insert(minutes.to_string(), retained);
    }
    for (rank, candidate) in candidates.iter().enumerate() {
        let r = record(candidate, "frozen500").ok_or("invalid candidate timing/NORAD")?;
        let hits = exposures
            .iter()
            .filter(|e| collides(&r, e, pad))
            .collect::<Vec<_>>();
        let direct = exposed_keys.contains(&key(r.id));
        if direct || !hits.is_empty() {
            excluded.push(json!({"original_rank":rank,"observation_id":r.id,
            "reason":if direct{"direct_recording_or_decoder_exposure"}else{"same_satellite_temporal_proximity"},
            "direct":direct,"matching_exposures":hits.iter().map(|e|json!({"key":key(e.id),"norad":e.norad,"start_unix":e.start,"end_unix":e.end,"gap_seconds":separation(&r,e)})).collect::<Vec<_>>()}));
        } else {
            included.push((rank, candidate.clone(), r));
        }
    }
    // A second, stricter optional subset also separates its own candidate intervals.
    let mut pass_separated: Vec<(usize, Value, Record)> = vec![];
    let mut within_pass_exclusions = vec![];
    for item in &included {
        if let Some(other) = pass_separated
            .iter()
            .find(|other| collides(&item.2, &other.2, pad))
        {
            within_pass_exclusions.push(json!({"original_rank":item.0,"observation_id":item.2.id,"reason":"evaluation_internal_same_satellite_temporal_proximity","kept_earlier_rank_observation":other.2.id}));
        } else {
            pass_separated.push(item.clone());
        }
    }
    let unknown_canvas_context = unlinked
        .iter()
        .filter(|e| e.path.to_lowercase().contains("canvas"))
        .map(|e| e.path.clone())
        .collect::<Vec<_>>();
    let exposure_manifest = json!({"schema":"recording-exposure-manifest-v1","started_utc":created,"finished_utc":now(),"work_root":work,
        "policy":policy,"inventory_entries":entries,"inventory_regular_files":files.len(),"metadata_bytes_read":metadata_bytes,
        "metadata_sources":metadata_sources,"metadata_catalog":catalog,"satellite_uuid_to_norad":satellite_uuid_to_norad,"identified_exposure_evidence":evidence,"identified_exposure_keys":exposed_keys,
        "resolved_exposure_records":exposures,"unresolved_exposure_keys":unresolved,"unlinked_artifacts":unlinked,
        "unlinked_canvas_context_artifacts":unknown_canvas_context,"scan_issues":scan_issues,"evidence_roots":roots,
        "lineage_context_directories":contexts.len(),"lineage_read_issues":lineage_read_issues,
        "waveform_bytes_read":0,"waveform_hashes_reverified":false,"file_evidence_scope":"presence/size/mtime only; conservative exposure markers, not packet or signal identity qualification",
        "global_nonexposure_proof":false,"full_private_and_public_namespace_coverage_proven":false});
    write_new(&output.join("exposure-manifest.json"), &exposure_manifest)?;
    let manifest_bytes =
        fs::read(output.join("exposure-manifest.json")).map_err(|e| e.to_string())?;
    let manifest_identity = json!({"path":output.join("exposure-manifest.json"),"bytes":manifest_bytes.len(),"sha256":sha(&manifest_bytes)});
    let release = false; // A local catalog cannot silently qualify unresolved provenance.
    let make = |items: &[(usize, Value, Record)], separated: bool| {
        json!({"schema":"innovation-benchmark-cohort-v2","subset_amendment_schema":"innovation-benchmark-exposure-subset-v1","frozen_utc":now(),
        "source_cohort":cohort_identity,"source_selection_ids_sha256":cohort["selection_ids_sha256"],"plan":cohort["plan"],
        "exposure_audit":manifest_identity,"selected_count":items.len(),"original_selected_count":500,
        "original_ranks":items.iter().map(|x|x.0).collect::<Vec<_>>(),"observations":items.iter().map(|x|x.1.clone()).collect::<Vec<_>>(),
        "selection_ids_sha256":sha(items.iter().map(|x|x.2.id.to_string()).collect::<Vec<_>>().join("\n").as_bytes()),
        "identified_local_exposures_disjoint_at_declared_padding":true,"evaluation_internal_temporal_separation_enforced":separated,
        "same_satellite_proximity_minutes":args.proximity_minutes,"fresh_independent_holdout_qualified":false,"evaluation_release_permitted":release,
        "candidate_for_independent_evaluation":!items.is_empty(),"current_exposure_check_passed":false,
        "qualification_limit":"Proposal only until unresolved exposure keys/unlinked real-CANVAS artifacts and current concurrent development are reviewed; no global independence claim",
        "publication_ready":false,"waveform_downloads":0,"decoder_runs":0})
    };
    write_new(
        &output.join("cohort-proposed.json"),
        &make(&included, false),
    )?;
    write_new(
        &output.join("cohort-pass-separated-proposed.json"),
        &make(&pass_separated, true),
    )?;
    write_new(
        &output.join("exclusions.json"),
        &json!({"development_exclusions":excluded,"evaluation_internal_exclusions":within_pass_exclusions}),
    )?;
    let summary = json!({"schema":"recording-exposure-audit-summary-v1","filesystem_walk_complete":true,"coverage_complete":false,"started_utc":created,"finished_utc":now(),
        "metadata_sources":metadata_sources.len(),"catalog_keys":catalog.len(),"identified_exposure_keys":exposed_keys.len(),"resolved_exposure_records":exposures.len(),
        "unresolved_exposure_keys":unresolved,"unlinked_artifacts":unlinked.len(),"unlinked_canvas_context_artifacts":unknown_canvas_context,
        "scan_issues":scan_issues,"original_candidates":500,"proposed_retained":included.len(),"excluded":excluded.len(),"pass_separated_retained":pass_separated.len(),
        "sensitivity_retained_by_padding_minutes":sensitivity,"exposure_manifest":manifest_identity,"evaluation_release_permitted":release,
        "fresh_independent_holdout_qualified":false,"waveform_downloads":0,"decoder_runs":0,"publication_ready":false});
    write_new(&output.join("summary.json"), &summary)?;
    if sha(&fs::read(&cohort_path).map_err(|e| e.to_string())?) != COHORT_SHA {
        return Err("source cohort changed during audit".into());
    }
    println!(
        "metadata-exposure proposed={} excluded={} pass-separated={} unresolvedkeys={} unlinked={} release=false",
        included.len(),
        excluded.len(),
        pass_separated.len(),
        unresolved.len(),
        unlinked.len()
    );
    Ok(())
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("exposure audit failed: {e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn r(id: u64, n: u64, s: i64, e: i64) -> Record {
        Record {
            namespace: ns(id).into(),
            id,
            norad: n,
            start: s,
            end: e,
            station: Some(id),
            metadata_sources: BTreeSet::new(),
        }
    }
    #[test]
    fn identifiers_are_instance_scoped_and_dates_not_observations() {
        assert_eq!(
            path_ids("polyitan/obs-5122/observation_5122.iq"),
            BTreeSet::from([5122])
        );
        assert_eq!(
            path_ids("foo-20260911/14967362-full/result.json"),
            BTreeSet::from([14967362])
        );
        assert!(path_ids("20260911/fixture-9600.wav").is_empty());
        assert!(
            path_ids(
                "units/0151988ae5953c8d8932f7fdc5562acd3a68d1f36c88a1766d9a38bd7e6088e4/result.json"
            )
            .is_empty()
        );
        assert_eq!(
            path_ids("paired/r0-14936407-before/result.json"),
            BTreeSet::from([14936407])
        );
        assert_ne!(key(5122), key(14967362));
    }
    #[test]
    fn closed_intervals_padding_and_satellite_gates() {
        let a = r(1, 68635, 100, 200);
        assert!(collides(&a, &r(2, 68635, 200, 300), 0));
        assert!(collides(&a, &r(2, 68635, 7400, 7500), 7200));
        assert!(!collides(&a, &r(2, 68635, 7401, 7500), 7200));
        assert!(!collides(&a, &r(2, 99999, 100, 200), 7200));
    }
    #[test]
    fn metadata_only_files_do_not_become_decoder_exposure() {
        for n in [
            "metadata.json",
            "cohort.json",
            "selection-plan.json",
            "result-metadata.json",
            "response.json.http.json",
        ] {
            assert!(!decoder_artifact(n, "cohort/obs-14967362/metadata/"));
        }
        assert!(decoder_artifact(
            "own-native.json",
            "benchmark/obs-14967362/"
        ));
    }
    #[test]
    fn record_extraction_ignores_outcomes_and_retains_conflicting_timing() {
        let mut c = BTreeMap::new();
        let v = json!({"id":14967362,"norad_cat_id":68635,"start":"2026-09-10T00:00:00Z","end":"2026-09-10T00:10:00Z","status":"good","demoddata":[{"id":99999999}]});
        records(&v, "a", &mut c);
        let mut x = v.clone();
        x["status"] = "bad".into();
        records(&x, "b", &mut c);
        assert_eq!(c.len(), 1);
        assert_eq!(c[&key(14967362)].len(), 1);
        x["end"] = "2026-09-10T00:11:00Z".into();
        records(&x, "c", &mut c);
        assert_eq!(c[&key(14967362)].len(), 2);
    }
    #[test]
    fn no_zero_duration_or_invalid_time_record() {
        assert!(
            record(
                &json!({"id":14967362,"norad_cat_id":68635,"start":"bad","end":"bad"}),
                "x"
            )
            .is_none()
        );
    }
    #[test]
    fn uuid_mapping_retains_ambiguity_without_using_outcomes() {
        let m = BTreeMap::from([("abc".to_string(), BTreeSet::from([68635, 42]))]);
        let mut c = BTreeMap::new();
        mapped_records(
            &json!({"observation_id":4244,"satellite_id":"abc","start":"2026-08-31T20:38:06Z","end":"2026-08-31T20:44:40Z"}),
            "attachment",
            &m,
            &mut c,
        );
        assert_eq!(c[&key(4244)].len(), 2);
    }
    #[test]
    fn lineage_context_requires_explicit_scoped_identity() {
        assert!(direct_context_ids(&json!({"observations":[{"observation_id":14967362}],"frames":[{"observation_id":14967362}]})).is_empty());
        assert_eq!(
            direct_context_ids(
                &json!({"unit":{"observation_id":4491},"frames":[{"observation_id":14967362}]})
            ),
            BTreeSet::from([4491])
        );
        assert!(direct_context_ids(&json!({"id":14967362})).is_empty());
        assert!(path_ids("satnogs_iq_14115025_437250000_57600.sigmf-data").contains(&14115025));
        assert!(!path_ids("satnogs_iq_14115025_437250000_57600.sigmf-data").contains(&437250000));
    }
}
