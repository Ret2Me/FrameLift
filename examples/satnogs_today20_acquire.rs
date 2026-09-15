//! Outcome-blind, bounded acquisition of twenty September 10 public OGG files.
//! Selection is frozen before any OGG download or decoder execution.
#[path = "support/holdout_run_io.rs"]
mod io;

use chrono::{DateTime, Utc};
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::input;

const API: &str = "https://network.satnogs.org/api/observations/";
const START: &str = "2026-09-10T00:00:00Z";
const CUTOFF: &str = "2026-09-10T15:09:32Z";
const TX: &str = "GCmN6RULea8dAT7Qoat8z2";
const MAX_PAGES: usize = 100;
const PAGE_CAP: u64 = 4 * 1024 * 1024;
const OGG_CAP: u64 = 64 * 1024 * 1024;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    /// Resume audio downloads from an already frozen cohort; never refetch metadata.
    #[arg(long)]
    resume: bool,
}

fn encode(text: &str) -> String {
    text.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
                char::from(b).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}
fn decode(text: &str) -> Result<String, String> {
    let mut bytes = Vec::new();
    let mut i = 0;
    while i < text.len() {
        match text.as_bytes()[i] {
            b'%' => {
                let hex = text.get(i + 1..i + 3).ok_or("bad percent escape")?;
                bytes.push(u8::from_str_radix(hex, 16).map_err(|e| e.to_string())?);
                i += 3;
            }
            b'+' => {
                bytes.push(b' ');
                i += 1;
            }
            b => {
                bytes.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8(bytes).map_err(|e| e.to_string())
}
fn required_query() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("transmitter_uuid".into(), TX.into()),
        ("start".into(), START.into()),
        ("start__lt".into(), CUTOFF.into()),
        ("format".into(), "json".into()),
    ])
}
fn url(query: &BTreeMap<String, String>) -> String {
    format!(
        "{API}?{}",
        query
            .iter()
            .map(|(k, v)| format!("{}={}", encode(k), encode(v)))
            .collect::<Vec<_>>()
            .join("&")
    )
}
fn validate_api_url(value: &str) -> Result<String, String> {
    if value.len() > 16384
        || !value.is_ascii()
        || value.bytes().any(|b| b <= 32 || b == 127)
        || value.contains(['#', '\\'])
    {
        return Err("invalid API URL".into());
    }
    let query = value
        .strip_prefix(&format!("{API}?"))
        .ok_or("pagination left exact HTTPS API endpoint")?;
    let mut parsed = BTreeMap::new();
    for part in query.split('&') {
        let (key, val) = part.split_once('=').ok_or("invalid query component")?;
        if parsed.insert(decode(key)?, decode(val)?).is_some() {
            return Err("duplicate query key".into());
        }
    }
    let required = required_query();
    for (key, val) in &required {
        if parsed.get(key) != Some(val) {
            return Err(format!("changed pagination filter {key}"));
        }
    }
    if parsed
        .keys()
        .any(|key| !required.contains_key(key) && key != "cursor")
        || parsed.get("cursor").is_some_and(String::is_empty)
    {
        return Err("unplanned filter or empty cursor".into());
    }
    Ok(url(&parsed))
}
fn next_page(headers: &str) -> Result<Option<String>, String> {
    let mut links = Vec::new();
    let mut seen_status = false;
    for line in headers.lines() {
        if line.starts_with("HTTP/") {
            links.clear();
            seen_status = true;
        } else if line.starts_with([' ', '\t']) {
            return Err("folded header".into());
        } else if let Some((key, value)) = line.split_once(':')
            && key.eq_ignore_ascii_case("link")
        {
            links.push(value.trim());
        }
    }
    if !seen_status {
        return Err("missing HTTP status header".into());
    }
    let mut next = None;
    for item in links.iter().flat_map(|line| line.split(',')) {
        let (target, params) = item
            .trim()
            .strip_prefix('<')
            .and_then(|v| v.split_once('>'))
            .ok_or("invalid Link header")?;
        let mut relation = None;
        for param in params.split(';').filter(|v| !v.trim().is_empty()) {
            let (key, val) = param
                .trim()
                .split_once('=')
                .ok_or("invalid Link parameter")?;
            if key.trim() == "rel" {
                if relation.is_some() {
                    return Err("duplicate Link relation".into());
                }
                let val = val.trim();
                relation = Some(if let Some(val) = val.strip_prefix('"') {
                    val.strip_suffix('"').ok_or("unterminated Link relation")?
                } else {
                    val
                });
            }
        }
        if relation
            .ok_or("missing Link relation")?
            .split_ascii_whitespace()
            .any(|v| v == "next")
        {
            if next.is_some() {
                return Err("multiple next links".into());
            }
            next = Some(validate_api_url(target)?);
        }
    }
    Ok(next)
}
fn plan() -> Value {
    json!({"schema":"satnogs-today20-selection-v1","start_utc":START,"cutoff_utc":CUTOFF,
        "norad_cat_id":68635,"mission":"CANVAS","transmitter_uuid":TX,"mode":"GMSK","baud":9600,
        "target":20,"selection":"complete paginated API snapshot; validate metadata locally; latest twenty by (start,id) descending with public OGG payload",
        "required_time":"start >= start_utc; start < cutoff_utc; end > start; end <= cutoff_utc",
        "outcome_filters":[],"do_not_filter_on":["status","waterfall_status","demoddata","decoded_frame_count"],
        "audio_field":"payload","protocol_scope":"known AX.25 G3RUH common component profile; not all SatNOGS missions or modulations",
        "max_pages":MAX_PAGES,"page_download_cap_bytes":PAGE_CAP,"ogg_download_cap_bytes":OGG_CAP,
        "selection_frozen_before_audio_download":true,"no_decoder_tuning_on_this_cohort":true})
}
fn date(value: &Value, key: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(
        value[key]
            .as_str()
            .ok_or_else(|| format!("missing {key}"))?,
    )
    .map(|v| v.with_timezone(&Utc))
    .map_err(|e| e.to_string())
}
fn eligible(row: &Value) -> Result<(DateTime<Utc>, u64), String> {
    let id = row["id"]
        .as_u64()
        .filter(|id| *id > 0)
        .ok_or("invalid id")?;
    let start = date(row, "start")?;
    let end = date(row, "end")?;
    let lower = DateTime::parse_from_rfc3339(START)
        .unwrap()
        .with_timezone(&Utc);
    let upper = DateTime::parse_from_rfc3339(CUTOFF)
        .unwrap()
        .with_timezone(&Utc);
    if start < lower || start >= upper || end <= start || end > upper {
        return Err("not completed within frozen today interval".into());
    }
    if row["norad_cat_id"].as_u64() != Some(68635)
        || row["transmitter_mode"] != "GMSK"
        || row["transmitter_baud"].as_f64() != Some(9600.0)
        || row["transmitter_uuid"] != TX
        || row["transmitter"] != TX
    {
        return Err("mismatching mission or transmitter profile".into());
    }
    let payload = row["payload"].as_str().ok_or("no audio payload")?;
    if !io::allowed_url(payload) || !payload.split('?').next().unwrap_or("").ends_with(".ogg") {
        return Err("no allowed public OGG payload".into());
    }
    Ok((start, id))
}
fn select(rows: &[Value]) -> Result<(Vec<Value>, Vec<Value>, usize), String> {
    let mut unique = BTreeMap::new();
    let mut excluded = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        let Some(id) = row["id"].as_u64().filter(|v| *v > 0) else {
            excluded.push(json!({"raw_row_index":i,"reason":"invalid_id"}));
            continue;
        };
        if let Some(old) = unique.insert(id, row)
            && old != row
        {
            return Err(format!("conflicting duplicate observation {id}"));
        }
    }
    let mut eligible_rows = Vec::new();
    for (id, row) in unique {
        match eligible(row) {
            Ok(order) => eligible_rows.push((order, row.clone())),
            Err(error) => excluded.push(json!({"id":id,"reason":error})),
        }
    }
    eligible_rows.sort_by(|a, b| b.0.cmp(&a.0));
    let total = eligible_rows.len();
    let mut selected = Vec::new();
    for (i, (_, row)) in eligible_rows.into_iter().enumerate() {
        if i < 20 {
            selected.push(row);
        } else {
            excluded.push(json!({"id":row["id"],"reason":"outside latest twenty","rank":i+1}));
        }
    }
    Ok((selected, excluded, total))
}
fn read_json(path: &Path) -> Result<Value, String> {
    serde_json::from_reader(File::open(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}
fn snapshot(out: &Path) -> Result<Value, String> {
    let frozen_plan = json!({"created_utc":io::utc(),"plan":plan(),"acquisition_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?});
    input::write_json_new(&out.join("selection-plan.json"), &frozen_plan)?;
    let mut next = Some(url(&required_query()));
    let mut seen = BTreeSet::new();
    let mut rows = Vec::new();
    let mut pages = Vec::new();
    for page in 0..MAX_PAGES {
        let Some(current) = next.take() else {
            break;
        };
        let canonical = validate_api_url(&current)?;
        if !seen.insert(canonical.clone()) {
            return Err("pagination cycle".into());
        }
        let dir = out.join(format!("metadata/page-{page:03}"));
        fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        io::disk_guard(out)?;
        let target = dir.join("response.json");
        let receipt = io::fetch(&canonical, &target, PAGE_CAP)?;
        let http = read_json(&dir.join("response.json.http.json"))?;
        let last = http["hops"]
            .as_array()
            .and_then(|v| v.last())
            .ok_or("missing final HTTP hop")?;
        let header = dir.join(format!(
            "response.json.hop-{}.try-{}.headers",
            last["hop"], last["attempt"]
        ));
        if fs::metadata(&header).map_err(|e| e.to_string())?.len() > 256 * 1024 {
            return Err("oversized metadata headers".into());
        }
        let body = read_json(&target)?;
        let values = body.as_array().ok_or("API body is not an array")?;
        if values.len() > 1000 {
            return Err("oversized API row count".into());
        }
        rows.extend(values.iter().cloned());
        next = next_page(&fs::read_to_string(&header).map_err(|e| e.to_string())?)?;
        pages.push(json!({"page":page,"rows":values.len(),"next":next,"download":receipt}));
        io::replace_json(
            &out.join("metadata-progress.json"),
            &json!({"pages":pages.len(),"rows":rows.len(),"complete":next.is_none()}),
        )?;
        println!("metadata: {} page(s), {} row(s)", pages.len(), rows.len());
    }
    if next.is_some() {
        return Err("pagination bound reached before EOF".into());
    }
    let (selected, excluded, eligible_count) = select(&rows)?;
    input::write_json_new(
        &out.join("metadata-all.json"),
        &json!({"observations":rows,"pages":pages}),
    )?;
    input::write_json_new(&out.join("exclusions.json"), &excluded)?;
    if selected.len() != 20 {
        return Err(format!(
            "only {} eligible observations; refusing cohort substitution",
            selected.len()
        ));
    }
    let cohort = json!({"schema":"satnogs-today20-cohort-v1","frozen_utc":io::utc(),"plan":plan(),"selection_plan":input::identity(&out.join("selection-plan.json"))?,
        "metadata":input::identity(&out.join("metadata-all.json"))?,"metadata_page_count":pages.len(),"eligible_count":eligible_count,"observations":selected});
    input::write_json_new(&out.join("cohort.json"), &cohort)?;
    Ok(cohort)
}
fn public_ipv4(value: &str) -> Option<std::net::Ipv4Addr> {
    value
        .trim()
        .parse::<std::net::Ipv4Addr>()
        .ok()
        .filter(|ip| {
            !ip.is_private()
                && !ip.is_loopback()
                && !ip.is_link_local()
                && !ip.is_unspecified()
                && !ip.is_broadcast()
                && (1..224).contains(&ip.octets()[0])
        })
}
fn cached_dns(dir: &Path, host: &str) -> Result<Option<(std::net::Ipv4Addr, Value)>, String> {
    let out = dir
        .parent()
        .and_then(Path::parent)
        .ok_or("DNS cache root missing")?;
    let now = chrono::DateTime::parse_from_rfc3339(&io::utc()).unwrap();
    let mut choices = Vec::new();
    for obs in fs::read_dir(out).map_err(|e| e.to_string())?.take(100) {
        let obs = obs.map_err(|e| e.to_string())?;
        if !obs.file_type().map_err(|e| e.to_string())?.is_dir()
            || !obs.file_name().to_string_lossy().starts_with("obs-")
        {
            continue;
        }
        for attempt in fs::read_dir(obs.path())
            .map_err(|e| e.to_string())?
            .take(20)
        {
            let attempt = attempt.map_err(|e| e.to_string())?;
            if !attempt.file_type().map_err(|e| e.to_string())?.is_dir() {
                continue;
            }
            let path = attempt.path().join("capture.dns.json");
            if !path.is_file() || fs::metadata(&path).map_err(|e| e.to_string())?.len() > 65536 {
                continue;
            }
            let value = read_json(&path)?;
            if value["host"] != host
                || value["process"]["success"] != true
                || !value["fallback_source"].is_null()
            {
                continue;
            }
            let Some(ip) = value["ipv4"].as_str().and_then(public_ipv4) else {
                continue;
            };
            let Some(created) = value["created_utc"]
                .as_str()
                .and_then(|v| chrono::DateTime::parse_from_rfc3339(v).ok())
            else {
                continue;
            };
            if now.signed_duration_since(created).num_seconds() < 0
                || now.signed_duration_since(created).num_seconds() > 6 * 3600
            {
                continue;
            }
            choices.push((created, ip, input::identity(&path)?));
        }
    }
    choices.sort_by_key(|value| value.0);
    Ok(choices.pop().map(|(created,ip,identity)|(ip,json!({"created_utc":created.to_rfc3339(),"identity":identity,"stale_if_dns_error_max_seconds":21600}))))
}
fn fetch_audio(
    url: &str,
    target: &Path,
    stop: &std::sync::atomic::AtomicBool,
) -> Result<Value, String> {
    use std::sync::atomic::Ordering;
    if !io::allowed_url(url) {
        return Err("audio URL outside fixed HTTPS host gate".into());
    }
    let dir = target.parent().ok_or("audio parent missing")?;
    // Resolve only this scoped download, without touching the host resolver.
    // Keep the original URL and TLS hostname; log the independent DNS answer.
    let host = url
        .strip_prefix("https://")
        .unwrap()
        .split('/')
        .next()
        .unwrap();
    let dns_args = vec![
        "@1.1.1.1".into(),
        "+time=5".into(),
        "+tries=2".into(),
        "+short".into(),
        host.into(),
        "A".into(),
    ];
    let dns = io::execute(
        Path::new("/usr/bin/dig"),
        &dns_args,
        dir,
        "capture.dns",
        15,
        1024 * 1024,
    )?;
    let answer =
        fs::read_to_string(dir.join("capture.dns.stdout.log")).map_err(|e| e.to_string())?;
    let live = if dns["success"] == true {
        answer.lines().find_map(public_ipv4)
    } else {
        None
    };
    let (ip, fallback) = match live {
        Some(ip) => (ip, None),
        None => {
            let (ip,proof)=cached_dns(dir,host)?.ok_or("independent DNS returned no public IPv4 and no bounded prior successful answer exists")?;
            (ip, Some(proof))
        }
    };
    input::write_json_new(
        &dir.join("capture.dns.json"),
        &json!({"host":host,"ipv4":ip.to_string(),"resolver":"1.1.1.1","fallback_source":fallback,
        "created_utc":io::utc(),"system_dns_unchanged":true,"tls_hostname_unchanged":true,"process":dns}),
    )?;
    let body = dir.join("capture.ogg.body");
    let headers = dir.join("capture.ogg.headers");
    let args = vec![
        "--silent".into(),
        "--show-error".into(),
        "--http1.1".into(),
        "--proto".into(),
        "=https".into(),
        "--max-time".into(),
        "3600".into(),
        "--connect-timeout".into(),
        "30".into(),
        "--max-filesize".into(),
        OGG_CAP.to_string(),
        "--retry".into(),
        "0".into(),
        "--resolve".into(),
        format!("{host}:443:{ip}"),
        "--user-agent".into(),
        "telemetry-yield-public-archive-research/1.0".into(),
        "--dump-header".into(),
        headers.display().to_string(),
        "--output".into(),
        body.display().to_string(),
        "--write-out".into(),
        "%{http_code}".into(),
        "--url".into(),
        url.into(),
    ];
    let process = io::execute(
        Path::new("/usr/bin/curl"),
        &args,
        dir,
        "capture.ogg",
        3635,
        OGG_CAP,
    )?;
    let status =
        fs::read_to_string(dir.join("capture.ogg.stdout.log")).map_err(|e| e.to_string())?;
    let bytes = fs::metadata(&body).map(|m| m.len()).unwrap_or(0);
    let header_text = fs::read_to_string(&headers).unwrap_or_default();
    let outcome = (|| {
        if matches!(status.as_str(), "429" | "503") {
            stop.store(true, Ordering::SeqCst);
            return Err(
                "server requested backoff: stop batch without retry; retain Retry-After header"
                    .into(),
            );
        }
        io::require_success(&process)?;
        if status != "200" {
            return Err(format!(
                "HTTP {status}: no redirect/substitute/automatic retry"
            ));
        }
        if bytes > OGG_CAP || header_text.len() > 256 * 1024 {
            return Err("audio response exceeds byte bounds".into());
        }
        fs::hard_link(&body, target).map_err(|e| e.to_string())?;
        Ok(
            json!({"url":url,"final_url":url,"identity":input::identity(target)?,"downloaded_bytes":bytes}),
        )
    })();
    input::write_json_new(
        &dir.join("capture.ogg.http.json"),
        &json!({"requested_url":url,"process":process,"http_status":status,
        "downloaded_bytes":bytes,"success":outcome.is_ok(),"result":outcome.as_ref().ok(),"error":outcome.as_ref().err(),
        "headers_path":headers,"retry_policy":"no automatic retries or redirects; 429/503 stops new batch requests"}),
    )?;
    outcome
}

fn download_one(
    out: &Path,
    row: &Value,
    stop: &std::sync::atomic::AtomicBool,
) -> Result<Value, String> {
    let (_, id) = eligible(row)?;
    let dir = out.join(format!("obs-{id}"));
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let target = dir.join("capture.ogg");
    let receipt_path = dir.join("downloaded.json");
    if receipt_path.exists() {
        let receipt = read_json(&receipt_path)?;
        if receipt["identity"]
            != serde_json::to_value(input::identity(&target)?).map_err(|e| e.to_string())?
        {
            return Err(format!("download identity mismatch {id}"));
        }
        return Ok(receipt);
    }
    if target.exists() {
        return Err(format!("unreceipted target exists for {id}"));
    }
    let attempt = (0..6)
        .map(|n| dir.join(format!("download-{n:02}")))
        .find(|path| !path.exists())
        .ok_or("six bounded download runs exhausted")?;
    fs::create_dir(&attempt).map_err(|e| e.to_string())?;
    io::disk_guard(out)?;
    let source = attempt.join("capture.ogg");
    let fetch = fetch_audio(row["payload"].as_str().unwrap(), &source, stop)?;
    let mut magic = [0u8; 4];
    File::open(&source)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map_err(|e| e.to_string())?;
    if &magic != b"OggS" {
        return Err(format!("observation {id} is not an Ogg container"));
    }
    fs::hard_link(&source, &target).map_err(|e| e.to_string())?;
    let receipt = json!({"observation_id":id,"completed_utc":io::utc(),"identity":input::identity(&target)?,"download":fetch,
            "http_receipt":input::identity(&attempt.join("capture.ogg.http.json"))?,"ogg_magic_valid":true});
    input::write_json_new(&receipt_path, &receipt)?;
    Ok(receipt)
}

fn download(out: &Path, cohort: &Value) -> Result<(), String> {
    use std::sync::{
        Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    };
    if cohort["plan"] != plan() {
        return Err("cohort plan differs from compiled selection".into());
    }
    let observations = cohort["observations"]
        .as_array()
        .filter(|rows| rows.len() == 20)
        .ok_or("cohort must contain exactly twenty rows")?;
    if out.join("server-deferred.json").exists() {
        return Err(
            "server backoff recorded; do not resume before reviewing retained Retry-After".into(),
        );
    }
    if !out.join("transport-amendment-v2.json").exists() {
        input::write_json_new(
            &out.join("transport-amendment-v2.json"),
            &json!({"created_utc":io::utc(),
            "reason":"original helper120-second transfer timeout exhausted at26-37KB/s for7.99MB; only transport amended",
            "selection_unchanged":true,"cohort":input::identity(&out.join("cohort.json"))?,"curl_transfer_seconds":900,"owned_process_seconds":935,
            "concurrent_downloads":4,"protocol":"HTTP/1.1 HTTPS exact metadata URLs","byte_cap":OGG_CAP,"automatic_retries":0,
            "server_backoff":"429/503 stops new requests; no retry before reviewing retained headers",
            "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?}),
        )?;
    }
    if !out.join("transport-amendment-v3.json").exists() {
        input::write_json_new(
            &out.join("transport-amendment-v3.json"),
            &json!({"created_utc":io::utc(),
            "reason":"system DNS stub127.0.0.53 timed out while independent DNS succeeded; 10MB healthy transfer took773seconds",
            "selection_unchanged":true,"cohort":input::identity(&out.join("cohort.json"))?,"curl_transfer_seconds":3600,"owned_process_seconds":3635,
            "concurrent_downloads":4,"byte_cap":OGG_CAP,"per_observation_attempt_limit":6,"automatic_retries":0,
            "dns":"per-download bounded dig@1.1.1.1 records publicIPv4; curl--resolve preserves exact URL/TLS host; no system changes",
            "partial_policy":"retain every partial body and ETag/header receipt; do not restart a timed-out body without reviewing resumability",
            "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?}),
        )?;
    }
    if !out.join("transport-amendment-v4.json").exists() {
        input::write_json_new(
            &out.join("transport-amendment-v4.json"),
            &json!({"created_utc":io::utc(),
            "reason":"intermittent independentDNS timeouts after8successfulOGG; use bounded same-host previouslyproven address onlywhenfreshlookupfails",
            "selection_unchanged":true,"cohort":input::identity(&out.join("cohort.json"))?,"stale_if_error_max_seconds":21600,
            "dns_cache_provenance":"only previoussuccessfuldirectDNS receipt, never chainfallback toextendage; perrequesthashreference",
            "tls_verification_unchanged":true,"system_dns_unchanged":true,"server_backoff_policy_unchanged":true,
            "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?}),
        )?;
    }
    let cursor = AtomicUsize::new(0);
    let stop = AtomicBool::new(false);
    let downloaded = Mutex::new(Vec::new());
    let errors = Mutex::new(Vec::new());
    let launch_gate = Mutex::new(std::time::Instant::now());
    std::thread::scope(|scope| {
        for _ in 0..4 {
            scope.spawn(|| loop {
                if stop.load(Ordering::SeqCst) { break; }
                let index=cursor.fetch_add(1,Ordering::SeqCst);
                let Some(row)=observations.get(index) else { break; };
                {
                    let mut next=launch_gate.lock().unwrap();
                    std::thread::sleep(next.saturating_duration_since(std::time::Instant::now()));
                    *next=std::time::Instant::now()+std::time::Duration::from_millis(250);
                }
                if stop.load(Ordering::SeqCst) { break; }
                match download_one(out,row,&stop) {
                    Ok(receipt)=>{
                        let mut values=downloaded.lock().unwrap(); values.push(receipt);
                        let result=io::replace_json(&out.join("download-progress.json"),&json!({"completed":values.len(),"total":20,"latest_id":row["id"],"updated_utc":io::utc()}));
                        println!("downloaded {}/20: {}",values.len(),row["id"]);
                        if let Err(error)=result { errors.lock().unwrap().push(error); stop.store(true,Ordering::SeqCst); }
                    }
                    Err(error)=>{
                        if error.contains("server requested backoff") {
                            let _=io::replace_json(&out.join("server-deferred.json"),&json!({"observation_id":row["id"],"recorded_utc":io::utc(),"error":error}));
                        }
                        errors.lock().unwrap().push(format!("{}: {error}",row["id"])); stop.store(true,Ordering::SeqCst);
                    }
                }
            });
        }
    });
    let errors = errors.into_inner().unwrap();
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }
    let downloaded = downloaded.into_inner().unwrap();
    if downloaded.len() != 20 {
        return Err(format!("incomplete downloads: {}", downloaded.len()));
    }
    input::write_json_new(
        &out.join("acquisition-complete.json"),
        &json!({"completed_utc":io::utc(),"cohort":input::identity(&out.join("cohort.json"))?,"download_count":downloaded.len(),"downloads":downloaded}),
    )
}
fn run(args: Args) -> Result<(), String> {
    let cohort = if args.resume {
        read_json(&args.output.join("cohort.json"))?
    } else {
        if let Some(parent) = args.output.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        fs::create_dir(&args.output).map_err(|e| e.to_string())?;
        snapshot(&args.output)?
    };
    download(&args.output, &cohort)
}
fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("acquisition failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dns_cache_requires_direct_recent_same_host_answer_and_public_address() {
        assert_eq!(
            public_ipv4("151.101.2.79").unwrap().to_string(),
            "151.101.2.79"
        );
        for ip in [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.1.1",
            "0.0.0.0",
            "224.0.0.1",
            "255.255.255.255",
            "invalid",
        ] {
            assert!(public_ipv4(ip).is_none(), "{ip}");
        }
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("obs-123/download-00");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("capture.dns.json");
        let mut value = json!({"host":"network-satnogs.freetls.fastly.net","ipv4":"151.101.2.79","created_utc":io::utc(),"process":{"success":true}});
        input::write_json_new(&path, &value).unwrap();
        assert!(
            cached_dns(&dir, "network-satnogs.freetls.fastly.net")
                .unwrap()
                .is_some()
        );
        assert!(cached_dns(&dir, "network.satnogs.org").unwrap().is_none());
        value["fallback_source"] = json!({"old":"answer"});
        io::replace_json(&path, &value).unwrap();
        assert!(
            cached_dns(&dir, "network-satnogs.freetls.fastly.net")
                .unwrap()
                .is_none()
        );
        value["fallback_source"] = Value::Null;
        value["created_utc"] = "2020-01-01T00:00:00Z".into();
        io::replace_json(&path, &value).unwrap();
        assert!(
            cached_dns(&dir, "network-satnogs.freetls.fastly.net")
                .unwrap()
                .is_none()
        );
    }
    fn row(id: u64, start: &str) -> Value {
        json!({"id":id,"start":start,"end":"2026-09-10T14:30:00Z","norad_cat_id":68635,"transmitter_mode":"GMSK","transmitter_baud":9600,"transmitter_uuid":TX,"transmitter":TX,"payload":"https://network-satnogs.freetls.fastly.net/a.ogg"})
    }
    #[test]
    fn stable_selection_is_independent_of_outcomes() {
        let mut rows = (1..=23)
            .map(|id| row(id, "2026-09-10T14:00:00Z"))
            .collect::<Vec<_>>();
        let first = select(&rows).unwrap();
        assert_eq!(first.0.len(), 20);
        assert_eq!(first.0[0]["id"], 23);
        for row in &mut rows {
            row["status"] = "good".into();
            row["demoddata"] = json!([1, 2, 3]);
        }
        let second = select(&rows).unwrap();
        assert_eq!(
            first.0.iter().map(|r| r["id"].clone()).collect::<Vec<_>>(),
            second.0.iter().map(|r| r["id"].clone()).collect::<Vec<_>>()
        );
    }
    #[test]
    fn filter_and_pagination_gates() {
        let base = url(&required_query());
        assert!(validate_api_url(&base).is_ok());
        assert!(validate_api_url(&(base.clone() + "&status=good")).is_err());
        assert!(validate_api_url(&base.replace(TX, "other")).is_err());
        assert!(validate_api_url(&(base.clone() + "&cursor=")).is_err());
        let next = base.clone() + "&cursor=eA%3D%3D";
        assert_eq!(
            next_page(&format!("HTTP/2 200\r\nLink: <{next}>; rel=\"next\"\r\n")).unwrap(),
            Some(validate_api_url(&next).unwrap())
        );
        assert!(
            next_page(&format!(
                "HTTP/2 200\r\nLink: <{base}&status=good>; rel=\"next\"\r\n"
            ))
            .is_err()
        );
    }
    #[test]
    fn rejects_wrong_day_unfinished_and_missing_ogg() {
        assert!(eligible(&row(1, "2026-09-09T23:59:59Z")).is_err());
        let mut value = row(1, "2026-09-10T14:00:00Z");
        value["end"] = "2026-09-10T15:10:00Z".into();
        assert!(eligible(&value).is_err());
        value["end"] = "2026-09-10T14:30:00Z".into();
        value["payload"] = Value::Null;
        assert!(eligible(&value).is_err());
    }
}
