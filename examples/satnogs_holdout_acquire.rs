//! Metadata-only, outcome-blind acquisition for the frozen CANVAS holdout.
//! No audio or archived frame objects are fetched by this executable.
use chrono::{DateTime, Timelike, Utc};
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
type RankedStrata<'a> = BTreeMap<(String, u32), Vec<(String, u64, &'a Value)>>;
const API: &str = "https://network.satnogs.org/api/observations/";
const START: &str = "2026-08-23T00:00:00Z";
const END: &str = "2026-09-06T00:00:00Z";
const TRANSMITTER: &str = "GCmN6RULea8dAT7Qoat8z2";
const RANK_PREFIX: &str = "satnogs-holdout-v1:";
const MAX_PAGES: usize = 200;
const MAX_PAGE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_METADATA_BYTES: u64 = 512 * 1024 * 1024;
const MAX_HEADER_BYTES: u64 = 256 * 1024;
const OLD_COHORT: &str = "satnogs-ogg-archive-week-20260831-v1/cohort.json";
const UNREADABLE_ENVIRONMENT_GUARD: &str = "golden/.env.guard-v3-x1ztap28";

#[derive(Parser)]
#[command(about = "Freeze a metadata-only SatNOGS holdout; never downloads audio or frame bytes")]
struct Args {
    /// Must not already exist. An incomplete run is retained and never overwritten.
    #[arg(long)]
    output: PathBuf,
    /// Predeclared JSON protocol, created before this metadata acquisition.
    #[arg(long)]
    protocol: PathBuf,
    /// Existing research artifacts to inspect for prior observation exposure.
    #[arg(long, default_value = "/home/ubuntu/telemetry-yield/work")]
    work_root: PathBuf,
    /// One bounded operational resume: preserve and verify prior metadata receipts.
    #[arg(long)]
    resume: bool,
}

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn utc() -> String {
    let elapsed = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
    DateTime::<Utc>::from_timestamp(elapsed.as_secs() as i64, elapsed.subsec_nanos())
        .unwrap()
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

fn identity(path: &Path) -> Result<Value> {
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        return Err(format!("symlink identity rejected: {}", path.display()).into());
    }
    let mut file = File::open(path)?;
    if !file.metadata()?.is_file() {
        return Err("identity requires a regular file".into());
    }
    let mut hash = Sha256::new();
    let mut buf = [0_u8; 65536];
    let mut size = 0_u64;
    loop {
        let count = file.read(&mut buf)?;
        if count == 0 {
            break;
        }
        size += count as u64;
        hash.update(&buf[..count]);
    }
    Ok(json!({"path":fs::canonicalize(path)?, "bytes":size, "sha256":hex::encode(hash.finalize())}))
}

fn durable_json(path: &Path, value: &Value) -> Result<()> {
    let parent = path.parent().ok_or("output without parent")?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent)?;
    serde_json::to_writer_pretty(&mut tmp, value)?;
    tmp.write_all(b"\n")?;
    tmp.as_file().sync_all()?;
    tmp.persist_noclobber(path)?;
    File::open(parent)?.sync_all()?;
    Ok(())
}

fn protocol_check(protocol: &Value) -> Result<()> {
    let c = protocol.get("cohort").ok_or("missing protocol.cohort")?;
    let expected = json!({
        "start_utc":START, "end_utc":END, "norad_cat_id":68635,
        "transmitter_uuid":TRANSMITTER, "mode":"GMSK", "baud":9600,
        "stratum_hours":2, "per_stratum":2, "rank_prefix":RANK_PREFIX
    });
    for (key, wanted) in expected.as_object().unwrap() {
        if c.get(key) != Some(wanted) {
            return Err(format!("protocol.cohort.{key} differs from compiled design").into());
        }
    }
    Ok(())
}

fn percent_decode(input: &str) -> Result<String> {
    let mut decoded = Vec::new();
    let bytes = input.as_bytes();
    let mut pos = 0;
    while pos < bytes.len() {
        match bytes[pos] {
            b'%' => {
                if pos + 2 >= bytes.len() {
                    return Err("truncated percent escape".into());
                }
                let hex = std::str::from_utf8(&bytes[pos + 1..pos + 3])?;
                decoded.push(u8::from_str_radix(hex, 16)?);
                pos += 3;
            }
            b'+' => {
                decoded.push(b' ');
                pos += 1;
            }
            b => {
                decoded.push(b);
                pos += 1;
            }
        }
    }
    Ok(String::from_utf8(decoded)?)
}

fn percent_encode(input: &str) -> String {
    let mut encoded = String::new();
    for byte in input.bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~".contains(&byte) {
            encoded.push(char::from(byte));
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    encoded
}

fn required_query() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("transmitter_uuid".into(), TRANSMITTER.into()),
        ("start".into(), START.into()),
        ("start__lt".into(), END.into()),
        ("format".into(), "json".into()),
    ])
}

fn make_url(query: &BTreeMap<String, String>) -> String {
    let query = query
        .iter()
        .map(|(k, v)| format!("{}={}", percent_encode(k), percent_encode(v)))
        .collect::<Vec<_>>()
        .join("&");
    format!("{API}?{query}")
}

/// Returns a canonical query identity, also used to detect percent-encoding cycles.
fn validate_api_url(url: &str) -> Result<String> {
    if url.len() > 16384 || !url.is_ascii() || url.bytes().any(|b| b <= 32 || b == 127) {
        return Err("invalid API URL characters or length".into());
    }
    let query = url
        .strip_prefix(&format!("{API}?"))
        .ok_or("pagination left the exact HTTPS endpoint")?;
    if query.contains('#') || query.contains('\\') {
        return Err("API URL fragment or backslash rejected".into());
    }
    let mut parsed = BTreeMap::new();
    for part in query.split('&') {
        let (key, val) = part.split_once('=').ok_or("malformed API query")?;
        let key = percent_decode(key)?;
        let val = percent_decode(val)?;
        if parsed.insert(key, val).is_some() {
            return Err("duplicate API query parameter".into());
        }
    }
    let required = required_query();
    for (key, val) in &required {
        if parsed.get(key) != Some(val) {
            return Err(format!("pagination dropped or changed {key}").into());
        }
    }
    if parsed
        .keys()
        .any(|key| !required.contains_key(key) && key != "cursor")
        || parsed.get("cursor").is_some_and(String::is_empty)
    {
        return Err("unplanned outcome filter or invalid cursor".into());
    }
    Ok(make_url(&parsed))
}

fn next_page(link: &str) -> Result<Option<String>> {
    if link.trim().is_empty() {
        return Ok(None);
    }
    let mut next = None;
    for item in link.split(',') {
        let item = item.trim();
        let (url, parameters) = item
            .strip_prefix('<')
            .and_then(|rest| rest.split_once('>'))
            .ok_or("malformed pagination Link header")?;
        let mut relation = None;
        for parameter in parameters.split(';').filter(|s| !s.trim().is_empty()) {
            let (key, value) = parameter
                .trim()
                .split_once('=')
                .ok_or("malformed Link parameter")?;
            if key.trim() == "rel" {
                if relation.is_some() {
                    return Err("ambiguous Link relation".into());
                }
                let value = value.trim();
                let value = if let Some(value) = value.strip_prefix('"') {
                    value
                        .strip_suffix('"')
                        .ok_or("unterminated Link relation")?
                } else {
                    value
                };
                relation = Some(value);
            }
        }
        let relation = relation.ok_or("Link without relation")?;
        if relation.split_ascii_whitespace().any(|rel| rel == "next") {
            if next.is_some() {
                return Err("multiple next-page links".into());
            }
            next = Some(validate_api_url(url)?);
        }
    }
    Ok(next)
}

fn response_link(headers: &str) -> Result<String> {
    let mut links = Vec::new();
    let mut found_http = false;
    for line in headers.lines() {
        if line.starts_with("HTTP/") {
            links.clear();
            found_http = true;
        } else if line.starts_with(' ') || line.starts_with('\t') {
            return Err("folded HTTP headers are unsupported".into());
        } else if let Some((key, value)) = line.split_once(':')
            && key.eq_ignore_ascii_case("link")
        {
            links.push(value.trim());
        }
    }
    if !found_http {
        return Err("missing HTTP status in headers".into());
    }
    Ok(links.join(", "))
}

fn number_id(row: &Value) -> Result<u64> {
    row.get("id")
        .and_then(Value::as_u64)
        .filter(|id| *id > 0)
        .ok_or_else(|| "observation requires a positive integer ID".into())
}

fn start_time(row: &Value) -> Result<DateTime<Utc>> {
    let start = row
        .get("start")
        .and_then(Value::as_str)
        .ok_or("missing string start time")?;
    Ok(DateTime::parse_from_rfc3339(start)?.with_timezone(&Utc))
}

fn eligibility(row: &Value) -> Result<DateTime<Utc>> {
    let start = start_time(row)?;
    let lower = DateTime::parse_from_rfc3339(START)?.with_timezone(&Utc);
    let upper = DateTime::parse_from_rfc3339(END)?.with_timezone(&Utc);
    if start < lower || start >= upper {
        return Err("start outside frozen UTC interval".into());
    }
    let transmitter = row
        .get("transmitter_uuid")
        .or_else(|| row.get("transmitter"));
    if let (Some(uuid), Some(tx)) = (row.get("transmitter_uuid"), row.get("transmitter"))
        && uuid != tx
    {
        return Err("conflicting transmitter metadata".into());
    }
    if row.get("norad_cat_id").and_then(Value::as_u64) != Some(68635)
        || transmitter.and_then(Value::as_str) != Some(TRANSMITTER)
        || row.get("transmitter_mode").and_then(Value::as_str) != Some("GMSK")
        || row.get("transmitter_baud").and_then(Value::as_f64) != Some(9600.0)
    {
        return Err("missing or mismatching NORAD/transmitter/modulation/baud metadata".into());
    }
    Ok(start)
}

#[derive(Default)]
struct Selection {
    observations: Vec<Value>,
    selected: Vec<Value>,
    excluded: Vec<Value>,
    unique_records: usize,
    eligible_records: usize,
}

fn choose(rows: &[Value], known: &BTreeMap<u64, BTreeSet<String>>) -> Result<Selection> {
    let mut result = Selection::default();
    let mut unique = BTreeMap::new();
    for (row_index, row) in rows.iter().enumerate() {
        if !row.is_object() {
            return Err("observation API returned a non-object record".into());
        }
        match number_id(row) {
            Ok(id) => {
                if unique.get(&id).is_some_and(|previous| *previous != row) {
                    return Err(format!("conflicting duplicate observation ID {id}").into());
                }
                unique.insert(id, row);
            }
            Err(error) => result.excluded.push(json!({"raw_row_index":row_index,
                "reason":"invalid_metadata", "detail":error.to_string()})),
        }
    }
    result.unique_records = unique.len();
    let mut strata: RankedStrata<'_> = BTreeMap::new();
    for (id, row) in unique {
        if let Some(sources) = known.get(&id) {
            result
                .excluded
                .push(json!({"id":id,"reason":"prior_exposure","sources":sources}));
            continue;
        }
        match eligibility(row) {
            Ok(start) => {
                let day = start.format("%Y-%m-%d").to_string();
                let stratum = start.hour() / 2;
                let rank = sha(format!("{RANK_PREFIX}{id}").as_bytes());
                strata
                    .entry((day, stratum))
                    .or_default()
                    .push((rank, id, row));
                result.eligible_records += 1;
            }
            Err(error) => result
                .excluded
                .push(json!({"id":id,"reason":"invalid_metadata",
                "detail":error.to_string()})),
        }
    }
    for ((day, stratum), mut values) in strata {
        values.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
        for (position, (rank, id, row)) in values.into_iter().enumerate() {
            if position < 2 {
                result.observations.push(row.clone());
                result.selected.push(json!({"id":id,"utc_day":day,"stratum":stratum,
                    "stratum_start_hour":stratum*2,"rank_sha256":rank,"rank_within_stratum":position+1}));
            } else {
                result
                    .excluded
                    .push(json!({"id":id,"reason":"deterministic_stratum_cap",
                    "utc_day":day,"stratum":stratum,"rank_sha256":rank,
                    "rank_within_stratum":position+1}));
            }
        }
    }
    Ok(result)
}

fn ids_in_component(name: &str) -> Vec<u64> {
    name.split(|c: char| !c.is_ascii_digit())
        .filter(|s| (7..=9).contains(&s.len()))
        .filter_map(|s| s.parse().ok())
        .collect()
}

fn approved_guard_skip(work_root: &Path, path: &Path, error: &std::io::Error) -> bool {
    path == work_root.join(UNREADABLE_ENVIRONMENT_GUARD)
        && error.kind() == std::io::ErrorKind::PermissionDenied
}

fn known_ids(work_root: &Path) -> Result<(BTreeMap<u64, BTreeSet<String>>, Value)> {
    let mut known: BTreeMap<u64, BTreeSet<String>> = BTreeMap::new();
    for id in [14366383, 14115025, 14956101, 14936397, 14206235] {
        known
            .entry(id)
            .or_default()
            .insert("explicit prior decoder development".into());
    }
    let old_cohort = work_root.join(OLD_COHORT);
    let old: Value = serde_json::from_reader(File::open(&old_cohort)?)?;
    for row in old
        .get("observations")
        .and_then(Value::as_array)
        .ok_or("old cohort missing observations")?
    {
        known
            .entry(number_id(row)?)
            .or_default()
            .insert(old_cohort.display().to_string());
    }
    let mut paths = vec![work_root.to_path_buf()];
    let mut entries = 0_u64;
    let mut oggs = 0_u64;
    let mut skipped_symlinks = Vec::new();
    let mut unreadable_subtrees = Vec::new();
    while let Some(path) = paths.pop() {
        let listing = match fs::read_dir(&path) {
            Ok(listing) => listing,
            Err(error) if approved_guard_skip(work_root, &path, &error) => {
                unreadable_subtrees.push(json!({"path":path,"error":error.to_string(),
                    "reason":"Explicitly excluded unreadable root-owned decoder environment guard; contents not inspected; permissions unchanged"}));
                continue;
            }
            Err(error) => {
                return Err(format!("prior-exposure scan {}: {error}", path.display()).into());
            }
        };
        for entry in listing {
            let entry = entry?;
            entries += 1;
            if entries > 2_000_000 {
                return Err(
                    "prior-exposure scan exceeds 2,000,000 entries; no cohort frozen".into(),
                );
            }
            let kind = entry.file_type()?;
            if kind.is_symlink() {
                skipped_symlinks.push(entry.path());
                continue;
            }
            if kind.is_dir() {
                paths.push(entry.path());
            } else if kind.is_file()
                && entry
                    .path()
                    .extension()
                    .is_some_and(|ext| ext.eq_ignore_ascii_case("ogg"))
            {
                oggs += 1;
                let path = entry.path();
                let relative = path.strip_prefix(work_root)?;
                for component in relative.components() {
                    for id in ids_in_component(&component.as_os_str().to_string_lossy()) {
                        known
                            .entry(id)
                            .or_default()
                            .insert(path.display().to_string());
                    }
                }
            }
        }
    }
    Ok((
        known,
        json!({"work_root":work_root,"entries_scanned":entries,"ogg_files":oggs,
        "prior_exposure_scan_complete":unreadable_subtrees.is_empty() && skipped_symlinks.is_empty(),
        "skipped_symlinks":skipped_symlinks,"unreadable_subtrees":unreadable_subtrees,
        "complete_regular_file_tree":unreadable_subtrees.is_empty(),
        "rule":"7-9 digit numeric tokens from preexisting OGG paths; conservative date-like tokens may also be excluded",
        "old_cohort":identity(&old_cohort)?}),
    ))
}

struct Fetcher {
    metadata_dir: PathBuf,
    transferred_bytes: u64,
    resume: bool,
    not_before: Option<DateTime<Utc>>,
}

fn retry_deadline(headers: &str, completed_utc: &str) -> Result<Option<DateTime<Utc>>> {
    let mut retry = None;
    for line in headers.lines() {
        if line.starts_with("HTTP/") {
            retry = None;
        } else if let Some((key, value)) = line.split_once(':')
            && key.eq_ignore_ascii_case("retry-after")
        {
            if retry.is_some() {
                return Err("ambiguous Retry-After headers".into());
            }
            retry = Some(value.trim());
        }
    }
    let Some(retry) = retry else {
        return Ok(None);
    };
    let deadline = if retry.bytes().all(|b| b.is_ascii_digit()) && !retry.is_empty() {
        let seconds: i64 = retry.parse()?;
        DateTime::parse_from_rfc3339(completed_utc)?
            .with_timezone(&Utc)
            .checked_add_signed(
                chrono::Duration::try_seconds(seconds).ok_or("Retry-After duration overflow")?,
            )
            .ok_or("Retry-After date overflow")?
    } else {
        DateTime::parse_from_rfc2822(retry)?.with_timezone(&Utc)
    };
    Ok(Some(deadline))
}

fn verify_identity_at(expected: &Value, actual_path: &Path) -> Result<()> {
    if expected != &identity(actual_path)? {
        return Err(format!("cached identity mismatch: {}", actual_path.display()).into());
    }
    Ok(())
}

impl Fetcher {
    fn fetch(&mut self, url: &str, page: usize) -> Result<(Value, Vec<Value>, Option<String>)> {
        validate_api_url(url)?;
        let mut errors = Vec::new();
        let attempt_cap: u32 = if self.resume { 6 } else { 3 };
        for attempt in 1..=attempt_cap {
            let stem = format!("page-{page:03}-attempt-{attempt}");
            let body_path = self.metadata_dir.join(format!("{stem}.body.json"));
            let headers_path = self.metadata_dir.join(format!("{stem}.headers.txt"));
            let stderr_path = self.metadata_dir.join(format!("{stem}.stderr.txt"));
            let receipt_path = self.metadata_dir.join(format!("{stem}.receipt.json"));
            let receipt = if receipt_path.exists() {
                if !self.resume {
                    return Err("unexpected prior receipt in new metadata acquisition".into());
                }
                let receipt: Value = serde_json::from_reader(File::open(&receipt_path)?)?;
                if receipt["url"] != url
                    || receipt["attempt"] != attempt
                    || receipt["limit_bytes"] != MAX_PAGE_BYTES
                    || receipt["redirects_followed"] != false
                {
                    return Err("cached request contract changed".into());
                }
                verify_identity_at(&receipt["identity"], &body_path)?;
                verify_identity_at(&receipt["headers_identity"], &headers_path)?;
                verify_identity_at(&receipt["stderr_identity"], &stderr_path)?;
                receipt
            } else {
                if body_path.exists() || headers_path.exists() || stderr_path.exists() {
                    return Err(
                        "uncommitted attempt files retained; refusing overwrite or retry over them"
                            .into(),
                    );
                }
                // All three output files also have a kernel-enforced 4 MiB hard bound.
                if self.transferred_bytes + 3 * MAX_PAGE_BYTES > MAX_METADATA_BYTES {
                    return Err("metadata transfer byte cap reached before pagination EOF".into());
                }
                if let Some(deadline) = self.not_before {
                    let now = DateTime::parse_from_rfc3339(&utc())?.with_timezone(&Utc);
                    if deadline.signed_duration_since(now).num_seconds() > 7200 {
                        return Err(
                            "server Retry-After exceeds bounded two-hour wait; no request made"
                                .into(),
                        );
                    }
                    if deadline > now {
                        println!(
                            "{}",
                            json!({"status":"waiting_for_server_retry_after",
                        "next_eligible_utc":deadline.to_rfc3339(),"page":page,"attempt":attempt})
                        );
                    }
                    loop {
                        let now = DateTime::parse_from_rfc3339(&utc())?.with_timezone(&Utc);
                        let remaining = deadline.signed_duration_since(now).num_seconds();
                        if remaining <= 0 {
                            break;
                        }
                        std::thread::sleep(Duration::from_secs((remaining as u64).min(30)));
                    }
                }
                std::thread::sleep(Duration::from_millis(if attempt == 1 {
                    if self.resume { 1000 } else { 250 }
                } else {
                    1000 * u64::from(attempt)
                }));
                let body = tempfile::NamedTempFile::new_in(&self.metadata_dir)?;
                let headers = tempfile::NamedTempFile::new_in(&self.metadata_dir)?;
                let stderr = tempfile::NamedTempFile::new_in(&self.metadata_dir)?;
                let requested_utc = utc();
                let mut command = Command::new("curl");
                command
                    .args([
                        "--disable",
                        "--globoff",
                        "--silent",
                        "--show-error",
                        "--fail",
                        "--proto",
                        "=https",
                        "--connect-timeout",
                        "10",
                        "--max-time",
                        "45",
                        "--max-redirs",
                        "0",
                        "--max-filesize",
                        &MAX_PAGE_BYTES.to_string(),
                        "--header",
                        "Accept-Encoding: identity",
                        "--user-agent",
                        "telemetry-yield-holdout-metadata/1",
                        "--dump-header",
                    ])
                    .arg(headers.path())
                    .arg("--output")
                    .arg(body.path())
                    .args(["--write-out", "%{http_code}", "--url", url])
                    .stdin(Stdio::null())
                    .stderr(stderr.as_file().try_clone()?);
                // SAFETY: only async-signal-safe setrlimit is called after fork; no
                // allocation, locks, or parent resource limits are modified here.
                unsafe {
                    command.pre_exec(|| {
                        let limit = libc::rlimit {
                            rlim_cur: MAX_PAGE_BYTES,
                            rlim_max: MAX_PAGE_BYTES,
                        };
                        if libc::setrlimit(libc::RLIMIT_FSIZE, &limit) != 0 {
                            return Err(std::io::Error::last_os_error());
                        }
                        Ok(())
                    });
                }
                let output = command.output()?;
                body.as_file().sync_all()?;
                headers.as_file().sync_all()?;
                stderr.as_file().sync_all()?;
                body.persist_noclobber(&body_path)?;
                headers.persist_noclobber(&headers_path)?;
                stderr.persist_noclobber(&stderr_path)?;
                let body_id = identity(&body_path)?;
                let header_id = identity(&headers_path)?;
                let stderr_id = identity(&stderr_path)?;
                let status = String::from_utf8_lossy(&output.stdout).trim().to_string();
                let receipt = json!({"url":url,"requested_utc":requested_utc,"completed_utc":utc(),
                "curl_exit_code":output.status.code(),"http_status":status,
                "identity":body_id,"headers_identity":header_id,"stderr_identity":stderr_id,
                "limit_bytes":MAX_PAGE_BYTES,"redirects_followed":false,"attempt":attempt});
                durable_json(&receipt_path, &receipt)?;
                receipt
            };
            let body_bytes = receipt["identity"]["bytes"]
                .as_u64()
                .ok_or("missing body size")?;
            let header_bytes = receipt["headers_identity"]["bytes"]
                .as_u64()
                .ok_or("missing header size")?;
            let stderr_bytes = receipt["stderr_identity"]["bytes"]
                .as_u64()
                .ok_or("missing stderr size")?;
            self.transferred_bytes += body_bytes + header_bytes + stderr_bytes;
            if body_bytes > MAX_PAGE_BYTES
                || header_bytes > MAX_HEADER_BYTES
                || stderr_bytes > MAX_HEADER_BYTES
                || self.transferred_bytes > MAX_METADATA_BYTES
            {
                return Err(
                    "response exceeded frozen metadata byte bounds; artifacts retained".into(),
                );
            }
            let header_text = fs::read_to_string(&headers_path)?;
            let status = receipt["http_status"]
                .as_str()
                .ok_or("missing HTTP status")?;
            if receipt["curl_exit_code"] != 0 || status != "200" {
                if let Some(deadline) = retry_deadline(
                    &header_text,
                    receipt["completed_utc"]
                        .as_str()
                        .ok_or("missing receipt timestamp")?,
                )? {
                    self.not_before =
                        Some(self.not_before.map_or(deadline, |old| old.max(deadline)));
                }
                if !(status == "000"
                    || status == "429"
                    || status == "408"
                    || status.starts_with('5'))
                {
                    return Err(format!("non-transient HTTP status {status}; no retry").into());
                }
                errors.push(receipt);
                continue;
            }
            let next = next_page(&response_link(&header_text)?)?;
            let records: Value = serde_json::from_reader(File::open(&body_path)?)?;
            let records = records.as_array().ok_or("API page must be a JSON array")?;
            if records.iter().any(|record| !record.is_object()) {
                return Err("API page includes non-object record".into());
            }
            return Ok((
                json!({"successful_attempt":receipt,"failed_attempts":errors}),
                records.clone(),
                next,
            ));
        }
        Err(format!(
            "page {page} unavailable after {attempt_cap} bounded attempts; no cohort frozen"
        )
        .into())
    }
}

fn coverage(rows: &[Value]) -> Result<Value> {
    let mut times = Vec::new();
    let mut days = BTreeSet::new();
    let mut stations = BTreeSet::new();
    let mut strata = BTreeSet::new();
    for row in rows {
        let time = start_time(row)?;
        let day = time.format("%Y-%m-%d").to_string();
        times.push(time);
        days.insert(day.clone());
        strata.insert((day, time.hour() / 2));
        if let Some(station) = row.get("ground_station").filter(|v| !v.is_null()) {
            stations.insert(station.to_string());
        }
    }
    Ok(
        json!({"start_min":times.iter().min().map(|t|t.to_rfc3339()),
        "start_max":times.iter().max().map(|t|t.to_rfc3339()),"distinct_utc_days":days.len(),
        "utc_days":days,"distinct_stations":stations.len(),"station_ids":stations,
        "occupied_selected_strata":strata.len(),"possible_strata":168,
        "interpretation":"Metadata-only sample, at most two records per UTC day / two-hour start-time stratum. Multiple stations and observations are not automatically independent transmissions."}),
    )
}

fn run(args: Args) -> Result<()> {
    if !args.protocol.is_absolute() || !args.work_root.is_absolute() || !args.output.is_absolute() {
        return Err("protocol, output, and work-root must be absolute paths".into());
    }
    if args.output.exists() && !args.resume {
        return Err(
            "output already exists; refusing overwrite or retrospective reselection".into(),
        );
    }
    let protocol_identity = identity(&args.protocol)?;
    if protocol_identity["bytes"].as_u64().unwrap() > 4 * 1024 * 1024 {
        return Err("protocol is unexpectedly large".into());
    }
    let protocol: Value = serde_json::from_reader(File::open(&args.protocol)?)?;
    protocol_check(&protocol)?;
    let executable = identity(&std::env::current_exe()?)?;
    let mut resume_identity = Value::Null;
    let (known, exposure_scan) = if args.resume {
        if args.output.join("cohort.json").exists() || args.output.join("freeze.json").exists() {
            return Err("cannot resume or reselect an already frozen cohort".into());
        }
        let plan_path = args.output.join("plan.json");
        let plan: Value = serde_json::from_reader(File::open(&plan_path)?)?;
        if plan["protocol_identity"] != protocol_identity
            || plan["schema"] != "satnogs-holdout-metadata-plan-v1"
            || plan["outcomes_read"] != false
            || plan["audio_downloaded"] != false
        {
            return Err("original metadata plan does not match the frozen protocol".into());
        }
        let initial_exe = plan["executable_identity"]["path"]
            .as_str()
            .ok_or("missing initial collector path")?;
        verify_identity_at(&plan["executable_identity"], Path::new(initial_exe))?;
        let resume_path = args.output.join("resume-001.json");
        durable_json(
            &resume_path,
            &json!({
                "schema":"satnogs-holdout-metadata-operational-resume-v1","recorded_utc":utc(),
                "initial_plan":identity(&plan_path)?,"protocol":protocol_identity,
                "initial_acquisition_executable":plan["executable_identity"],
                "resume_acquisition_executable":executable,
                "amendment":"Operational correction before waveform access: preserve/hash-verify cached metadata attempts, honor server Retry-After, permit one additional bounded three-attempt window per page (six total). No metadata selection or receiver parameter changes; prior exposure snapshot retained.",
                "max_attempts_per_page_including_prior":6,"max_server_wait_seconds":7200,
                "min_new_request_spacing_ms":1000,"selection_changed":false,
                "holdout_waveforms_accessed":false,"cohort_previously_frozen":false
            }),
        )?;
        resume_identity = identity(&resume_path)?;
        let known =
            serde_json::from_value::<BTreeMap<u64, BTreeSet<String>>>(plan["known_ids"].clone())?;
        (known, plan["prior_exposure_scan"].clone())
    } else {
        // Snapshot prior exposure before creating any campaign files or fetching metadata.
        let (known, exposure_scan) = known_ids(&args.work_root)?;
        fs::create_dir(&args.output)?;
        fs::create_dir(args.output.join("metadata"))?;
        durable_json(
            &args.output.join("plan.json"),
            &json!({
        "schema":"satnogs-holdout-metadata-plan-v1","created_utc":utc(),
        "protocol_identity":protocol_identity,"executable_identity":executable,
        "prior_exposure_scan":exposure_scan,"known_ids":known,
        "limits":{"max_pages":MAX_PAGES,"max_metadata_transfer_bytes":MAX_METADATA_BYTES,
            "max_page_bytes":MAX_PAGE_BYTES,"attempts_per_page":3,"min_request_spacing_ms":250,
            "http_timeout_seconds":45},
        "outcomes_read":false,"audio_downloaded":false,
        "selection_source":"Retrospective live metadata API, not a transactional historical snapshot"}),
        )?;
        (known, exposure_scan)
    };
    let mut fetcher = Fetcher {
        metadata_dir: args.output.join("metadata"),
        transferred_bytes: 0,
        resume: args.resume,
        not_before: None,
    };
    let mut next = Some(make_url(&required_query()));
    let mut seen = BTreeSet::new();
    let mut rows = Vec::new();
    let mut receipts = Vec::new();
    while let Some(url) = next {
        if !seen.insert(validate_api_url(&url)?) || receipts.len() >= MAX_PAGES {
            return Err("pagination cycle or page cap before EOF; no cohort frozen".into());
        }
        let (receipt, values, following) = fetcher.fetch(&url, receipts.len())?;
        rows.extend(values);
        receipts.push(receipt);
        next = following;
    }
    let selection = choose(&rows, &known)?;
    if selection.observations.len() > 336 {
        return Err("selection exceeds compiled stratum design".into());
    }
    if identity(&args.protocol)? != protocol_identity {
        return Err("protocol changed during metadata acquisition; no cohort frozen".into());
    }
    let cohort = json!({"schema":"satnogs-holdout-frozen-cohort-v1","frozen_utc":utc(),
        "protocol_identity":protocol_identity,"actual_coverage":coverage(&selection.observations)?,
        "selected_count":selection.observations.len(),"raw_record_count":rows.len(),
        "unique_record_count":selection.unique_records,"eligible_record_count":selection.eligible_records,
        "observations":selection.observations,"selection":selection.selected,
        "exclusions":selection.excluded,"prior_exposure_scan":exposure_scan,
        "pagination_complete":true,"metadata_receipts":receipts,
        "metadata_transfer_bytes":fetcher.transferred_bytes,"outcome_independent_selection":true,
        "metadata_operational_resume":resume_identity,
        "audio_missing_policy":"Retain selected records; no replacement based on audio availability or outcomes"});
    durable_json(&args.output.join("cohort.json"), &cohort)?;
    durable_json(
        &args.output.join("freeze.json"),
        &json!({
        "schema":"satnogs-holdout-freeze-v1","recorded_utc":utc(),
        "protocol":protocol_identity,"cohort":identity(&args.output.join("cohort.json"))?,
        "plan":identity(&args.output.join("plan.json"))?,"acquisition_executable":executable,
        "metadata_operational_resume":resume_identity,
        "outcomes_computed":false,"audio_downloaded":false,"pagination_complete":true}),
    )?;
    println!(
        "{}",
        json!({"status":"metadata_frozen","selected":cohort["selected_count"],
        "raw_records":rows.len(),"pages":receipts.len(),"output":args.output})
    );
    Ok(())
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("satnogs_holdout_acquire: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row(id: u64, start: &str) -> Value {
        json!({"id":id,"start":start,"norad_cat_id":68635,
            "transmitter":TRANSMITTER,"transmitter_uuid":TRANSMITTER,
            "transmitter_mode":"GMSK","transmitter_baud":9600.0,
            "ground_station":42,"demoddata":[],"audio":null})
    }

    #[test]
    fn selection_is_order_outcome_and_availability_invariant() {
        let original = (1..=10)
            .map(|id| row(id, "2026-08-24T03:00:00Z"))
            .collect::<Vec<_>>();
        let a = choose(&original, &BTreeMap::new()).unwrap();
        let mut changed = original;
        changed.reverse();
        for r in &mut changed {
            r["audio"] = json!("https://invalid.example/data.ogg");
            r["demoddata"] = json!([{"payload":"not inspected"}]);
            r["status"] = json!("good");
        }
        let b = choose(&changed, &BTreeMap::new()).unwrap();
        assert_eq!(a.selected, b.selected);
        assert_eq!(a.observations.len(), 2);
        assert_eq!(a.excluded.len(), 8);
    }

    #[test]
    fn timestamps_use_half_open_utc_strata() {
        let rows = vec![
            row(1, START),
            row(2, END),
            row(3, "2026-08-23T03:59:59+02:00"),
            row(4, "2026-08-23T04:00:00+02:00"),
            row(5, "2026-08-22T23:59:59Z"),
        ];
        let chosen = choose(&rows, &BTreeMap::new()).unwrap();
        assert_eq!(chosen.observations.len(), 3);
        assert_eq!(
            chosen.selected.iter().filter(|r| r["stratum"] == 0).count(),
            2
        );
        assert_eq!(
            chosen.selected.iter().filter(|r| r["stratum"] == 1).count(),
            1
        );
    }

    #[test]
    fn duplicate_conflicts_fail_before_selection() {
        let a = row(1, START);
        assert_eq!(
            choose(&[a.clone(), a.clone()], &BTreeMap::new())
                .unwrap()
                .observations
                .len(),
            1
        );
        let mut b = a.clone();
        b["status"] = json!("different");
        assert!(choose(&[a, b], &BTreeMap::new()).is_err());
    }

    #[test]
    fn strict_metadata_and_known_exclusions() {
        let mut invalid = row(2, START);
        invalid["transmitter_uuid"] = json!("conflict");
        let mut boolid = row(3, START);
        boolid["id"] = json!(true);
        let known = BTreeMap::from([(1, BTreeSet::from(["development".to_string()]))]);
        let selected = choose(&[row(1, START), invalid, boolid, row(4, START)], &known).unwrap();
        assert_eq!(selected.observations.len(), 1);
        assert_eq!(selected.observations[0]["id"], 4);
        assert_eq!(selected.excluded.len(), 3);
    }

    #[test]
    fn url_guard_rejects_filter_drift_and_offdomain() {
        let valid = make_url(&required_query());
        assert_eq!(validate_api_url(&valid).unwrap(), valid);
        for bad in [
            valid.replace("network.satnogs.org", "network.satnogs.org.evil.test"),
            valid.replace("https:", "http:"),
            format!("{valid}&status=good"),
            format!("{valid}&format=json"),
            format!("{valid}#fragment"),
            valid.replace("2026-08-23", "2026-08-24"),
            format!("{valid}&cursor="),
            format!("{valid}&cursor=%"),
        ] {
            assert!(validate_api_url(&bad).is_err(), "{bad}");
        }
        assert_eq!(validate_api_url(&valid.replace("%3A", ":")).unwrap(), valid);
    }

    #[test]
    fn link_pagination_checks_next_and_ambiguity() {
        let mut query = required_query();
        query.insert("cursor".into(), "a+/=".into());
        let url = make_url(&query);
        assert_eq!(
            next_page(&format!("<{url}>; rel=\"next\"")).unwrap(),
            Some(url.clone())
        );
        assert_eq!(next_page(&format!("<{url}>; rel=previous")).unwrap(), None);
        assert!(next_page(&format!("<{url}>; rel=next, <{url}>; rel=next")).is_err());
        assert!(next_page("<https://evil.test/>; rel=next").is_err());
        assert!(next_page("not a link").is_err());
        assert_eq!(next_page("").unwrap(), None);
    }

    #[test]
    fn headers_use_final_response_block() {
        assert_eq!(response_link("HTTP/1.1 200 Connection established\r\nLink: bogus\r\n\r\nHTTP/2 200\r\ncontent-type: application/json\r\n\r\n").unwrap(),"");
        assert!(response_link("not a response").is_err());
    }

    #[test]
    fn frozen_files_cannot_be_overwritten() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("freeze.json");
        durable_json(&path, &json!({"original":true})).unwrap();
        assert!(durable_json(&path, &json!({"original":false})).is_err());
        assert_eq!(
            serde_json::from_reader::<_, Value>(File::open(path).unwrap()).unwrap()["original"],
            true
        );
    }

    #[test]
    fn only_seven_to_nine_digit_tokens_are_prior_ids() {
        assert_eq!(
            ids_in_component("satnogs_14206235_2026-08-23.ogg"),
            vec![14206235]
        );
        assert!(ids_in_component("satnogs_4704_2026-09-03.ogg").is_empty());
        assert!(ids_in_component("1234567890").is_empty());
    }

    #[test]
    fn only_exact_permission_denied_environment_guard_is_skippable() {
        let work = Path::new("/some/work");
        let denied = std::io::Error::from(std::io::ErrorKind::PermissionDenied);
        assert!(approved_guard_skip(
            work,
            &work.join(UNREADABLE_ENVIRONMENT_GUARD),
            &denied
        ));
        assert!(!approved_guard_skip(
            work,
            &work.join("unseen-audio"),
            &denied
        ));
        assert!(!approved_guard_skip(
            work,
            &work.join(UNREADABLE_ENVIRONMENT_GUARD),
            &std::io::Error::from(std::io::ErrorKind::NotFound)
        ));
    }

    #[test]
    fn full_fourteen_day_design_has_at_most_336_records() {
        let start = DateTime::parse_from_rfc3339(START).unwrap();
        let mut rows = Vec::new();
        for stratum in 0..168 {
            let time = start + chrono::Duration::hours(stratum * 2);
            for repetition in 0..4 {
                rows.push(row(
                    (stratum * 4 + repetition + 1) as u64,
                    &time.to_rfc3339(),
                ));
            }
        }
        let selected = choose(&rows, &BTreeMap::new()).unwrap();
        assert_eq!(selected.observations.len(), 336);
        assert_eq!(selected.eligible_records, 672);
        assert_eq!(
            coverage(&selected.observations).unwrap()["distinct_utc_days"],
            14
        );
    }

    #[test]
    fn retry_after_seconds_and_http_date_are_honored() {
        let completed = "2026-09-08T19:35:11Z";
        let expected = DateTime::parse_from_rfc3339("2026-09-08T20:32:25Z")
            .unwrap()
            .with_timezone(&Utc);
        assert_eq!(
            retry_deadline("HTTP/2 429\r\nretry-after: 3434\r\n", completed).unwrap(),
            Some(expected)
        );
        assert_eq!(
            retry_deadline(
                "HTTP/2 429\r\nRetry-After: Tue, 08 Sep 2026 20:32:25 GMT\r\n",
                completed
            )
            .unwrap(),
            Some(expected)
        );
        assert!(
            retry_deadline(
                "HTTP/2 429\r\nRetry-After: 1\r\nRetry-After: 2\r\n",
                completed
            )
            .is_err()
        );
        assert!(
            retry_deadline(
                "HTTP/2 429\r\nRetry-After: 9223372036854775807\r\n",
                completed
            )
            .is_err()
        );
        assert!(retry_deadline("HTTP/2 429\r\nRetry-After: nonsense\r\n", completed).is_err());
        assert_eq!(retry_deadline("HTTP/2 200\r\n", completed).unwrap(), None);
    }

    #[test]
    fn cache_replay_validates_bytes_and_never_refetches_committed_page() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        let body = root.join("page-000-attempt-1.body.json");
        let headers = root.join("page-000-attempt-1.headers.txt");
        let stderr = root.join("page-000-attempt-1.stderr.txt");
        durable_json(&body, &json!([row(1, START)])).unwrap();
        fs::write(
            &headers,
            "HTTP/2 200\r\ncontent-type: application/json\r\n\r\n",
        )
        .unwrap();
        fs::write(&stderr, "").unwrap();
        let url = make_url(&required_query());
        durable_json(
            &root.join("page-000-attempt-1.receipt.json"),
            &json!({
                "url":url,"attempt":1,"http_status":"200","curl_exit_code":0,
                "identity":identity(&body).unwrap(),"headers_identity":identity(&headers).unwrap(),
                "stderr_identity":identity(&stderr).unwrap(),"limit_bytes":MAX_PAGE_BYTES,
                "redirects_followed":false,"completed_utc":"2026-09-08T19:35:11Z"
            }),
        )
        .unwrap();
        let mut fetcher = Fetcher {
            metadata_dir: root.to_path_buf(),
            transferred_bytes: 0,
            resume: true,
            not_before: None,
        };
        let (_, rows, next) = fetcher.fetch(&url, 0).unwrap();
        assert_eq!(rows.len(), 1);
        assert!(next.is_none());
        assert!(fetcher.transferred_bytes > 0);
        fs::write(&body, "[]").unwrap();
        assert!(fetcher.fetch(&url, 0).is_err());
        assert!(!root.join("page-000-attempt-2.receipt.json").exists());
    }
}
