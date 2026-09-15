//! Complete private-instance audio inventory and parallel, resumable acquisition.
#[path = "support/holdout_run_io.rs"]
mod io;
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::Read,
    os::unix::{ffi::OsStrExt, fs::OpenOptionsExt},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, mpsc},
    time::Duration,
};
use telemetry_yield_rs::input;

const INSTANCE: &str = "https://polyitan.duckdns.org:8001";
const API: &str = "https://polyitan.duckdns.org:8001/api/observations/";
const MEDIA: &str = "https://polyitan.duckdns.org:8019/iq-data/data_obs/";
const CUTOFF: &str = "2026-09-10T22:41:04Z";
const RESERVE: u64 = 16 * 1024 * 1024 * 1024;
const METADATA_PAGE_CAP: u64 = 256 * 1024 * 1024;

fn read_metadata(path: &Path) -> Result<Value, String> {
    let file = File::open(path).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() > METADATA_PAGE_CAP {
        return Err("metadata page exceeds bounded 256MiB parser capacity".into());
    }
    serde_json::from_reader(std::io::BufReader::new(file)).map_err(|e| e.to_string())
}
fn inventory_row(row: &Value) -> Value {
    let keys = [
        "id",
        "start",
        "end",
        "ground_station",
        "norad_cat_id",
        "satellite_name",
        "transmitter",
        "transmitter_uuid",
        "transmitter_mode",
        "transmitter_baud",
        "transmitter_description",
        "transmitter_downlink_low",
        "observation_frequency",
        "payload",
        "status",
    ];
    let mut out = serde_json::Map::new();
    for key in keys {
        if let Some(value) = row.get(key) {
            out.insert(key.into(), value.clone());
        }
    }
    Value::Object(out)
}

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 12)]
    workers: usize,
    #[arg(long)]
    resume: bool,
}

fn header(raw: &str, name: &str) -> Option<String> {
    raw.lines().rev().find_map(|line| {
        let (key, value) = line.split_once(':')?;
        key.eq_ignore_ascii_case(name)
            .then(|| value.trim().to_string())
    })
}
fn status(raw: &str) -> Option<u16> {
    raw.lines()
        .rev()
        .find(|line| line.starts_with("HTTP/"))?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}
fn safe_ascii(value: &str) -> bool {
    value.is_ascii()
        && value.len() <= 16384
        && !value.bytes().any(|b| b <= 32 || b == 127)
        && !value.contains(['\\', '#'])
}
fn api_url(raw: &str) -> Result<String, String> {
    if !safe_ascii(raw) {
        return Err("invalid pagination URL".into());
    }
    let suffix = raw
        .strip_prefix(API)
        .or_else(|| raw.strip_prefix("https://polyitan.duckdns.org/api/observations/"))
        .ok_or("pagination left exact private API")?;
    if !suffix.starts_with('?') || !suffix.contains("start__lt=") {
        return Err("pagination lost snapshot bound".into());
    }
    let mut names = BTreeSet::new();
    for part in suffix[1..].split('&') {
        let (key, value) = part.split_once('=').ok_or("invalid query")?;
        if !matches!(key, "start__lt" | "cursor" | "format") || !names.insert(key) {
            return Err("unexpected or duplicate pagination key".into());
        }
        if key == "start__lt" && value != CUTOFF && value != "2026-09-10T22%3A41%3A04Z" {
            return Err("pagination changed snapshot cutoff".into());
        }
        if key == "format" && value != "json" {
            return Err("format changed".into());
        }
    }
    Ok(format!("{API}{suffix}"))
}
fn media_ext(raw: &str) -> Result<&'static str, String> {
    if !safe_ascii(raw)
        || !raw.starts_with(MEDIA)
        || raw.contains(['?', '%'])
        || raw[MEDIA.len()..]
            .split('/')
            .any(|p| p == "." || p == ".." || p.is_empty())
    {
        return Err("audio URL outside authorized private bucket path".into());
    }
    if raw.ends_with(".ogg") {
        Ok("ogg")
    } else if raw.ends_with(".wav") {
        Ok("wav")
    } else {
        Err("payload is not an OGG/WAV audio recording".into())
    }
}
fn next_url(raw: &str) -> Result<Option<String>, String> {
    let Some(link) = header(raw, "link") else {
        return Ok(None);
    };
    let mut next = None;
    for piece in link.split(',') {
        if piece.contains("rel=\"next\"") || piece.contains("rel=next") {
            let start = piece.find('<').ok_or("malformed next link")?;
            let end = piece.find('>').ok_or("malformed next link")?;
            if next.is_some() || end <= start {
                return Err("ambiguous next link".into());
            }
            next = Some(api_url(&piece[start + 1..end])?);
        }
    }
    Ok(next)
}
fn request(
    url: &str,
    dir: &Path,
    name: &str,
    head: bool,
    limit: u64,
    etag: Option<&str>,
) -> Result<(PathBuf, String, Value), String> {
    let body = dir.join(format!("{name}.body"));
    let headers = dir.join(format!("{name}.headers"));
    let mut argv: Vec<String> = [
        "--silent",
        "--show-error",
        "--noproxy",
        "polyitan.duckdns.org",
        "--resolve",
        "polyitan.duckdns.org:8001:10.0.0.11",
        "--resolve",
        "polyitan.duckdns.org:8019:10.0.0.11",
        "--proto",
        "=https",
        "--connect-timeout",
        "10",
        "--max-time",
        if head { "30" } else { "900" },
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    argv.extend([
        "--dump-header".into(),
        headers.display().to_string(),
        "--output".into(),
        body.display().to_string(),
    ]);
    if head {
        argv.push("--head".into());
    } else {
        // On HEAD curl compares this option against the remote object length,
        // not the small header body. Limit GET only; RLIMIT_FSIZE bounds logs.
        argv.extend(["--max-filesize".into(), limit.to_string()]);
    }
    if let Some(tag) = etag {
        argv.extend(["--header".into(), format!("If-Match: {tag}")]);
    }
    argv.push(url.to_string());
    let process = io::execute(
        Path::new("/usr/bin/curl"),
        &argv,
        dir,
        name,
        if head { 35 } else { 905 },
        limit.max(4 * 1024 * 1024),
    )?;
    let raw = fs::read_to_string(&headers).map_err(|e| e.to_string())?;
    if status(&raw) == Some(429) {
        let delay = header(&raw, "retry-after")
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(60);
        let mut remaining = delay;
        while remaining > 0 {
            let step = remaining.min(60);
            std::thread::sleep(Duration::from_secs(step));
            remaining -= step;
        }
        return Err("HTTP 429; server delay honored, retry required".into());
    }
    io::require_success(&process)?;
    if status(&raw) != Some(200) {
        return Err(format!(
            "HTTP {:?}; redirect/error not followed",
            status(&raw)
        ));
    }
    Ok((body, raw, process))
}
fn available(path: &Path) -> Result<u64, String> {
    let name = CString::new(path.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(name.as_ptr(), &mut stat) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    Ok(stat.f_bavail.saturating_mul(stat.f_frsize))
}
struct Reservation<'a> {
    bytes: u64,
    budget: &'a Mutex<u64>,
}
impl Drop for Reservation<'_> {
    fn drop(&mut self) {
        *self.budget.lock().unwrap() -= self.bytes;
    }
}
fn reserve<'a>(root: &Path, bytes: u64, budget: &'a Mutex<u64>) -> Result<Reservation<'a>, String> {
    let mut active = budget.lock().map_err(|e| e.to_string())?;
    if bytes == 0 || available(root)? < RESERVE.saturating_add(*active).saturating_add(bytes) {
        return Err(
            "insufficient disk capacity after in-flight reservations and 16GiB reserve".into(),
        );
    }
    *active += bytes;
    Ok(Reservation { bytes, budget })
}
fn download(root: &Path, row: &Value, budget: &Mutex<u64>) -> Result<Value, String> {
    let id = row["id"].as_u64().ok_or("missing observation ID")?;
    let dir = root.join(format!("obs-{id}"));
    let url = row["payload"].as_str().ok_or("no audio URL")?;
    let ext = media_ext(url)?;
    let final_path = dir.join(format!("capture.{ext}"));
    let done = dir.join("downloaded.json");
    if done.exists() {
        let value = input::read_json(&done)?;
        let actual = input::identity(&final_path)?;
        if value["instance"] != INSTANCE
            || value["observation_id"] != id
            || value["url"] != url
            || value["source"]["sha256"] != actual.sha256
            || value["source"]["bytes"] != actual.bytes
        {
            return Err("completed download identity mismatch".into());
        }
        return Ok(value);
    }
    let attempt = tempfile::Builder::new()
        .prefix("download-")
        .tempdir_in(&dir)
        .map_err(|e| e.to_string())?
        .keep();
    let (_, head, _) = request(url, &attempt, "head", true, 4 * 1024 * 1024, None)?;
    let bytes = header(&head, "content-length")
        .and_then(|v| v.parse::<u64>().ok())
        .ok_or("HEAD lacks exact length for disk reservation")?;
    let _reservation = reserve(root, bytes, budget)?;
    let etag = header(&head, "etag");
    let (body, headers, process) = request(url, &attempt, "get", false, bytes, etag.as_deref())?;
    if fs::metadata(&body).map_err(|e| e.to_string())?.len() != bytes
        || header(&headers, "content-length").as_deref() != Some(&bytes.to_string())
    {
        return Err("download length changed or truncated".into());
    }
    let mut magic = [0u8; 12];
    File::open(&body)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map_err(|e| e.to_string())?;
    if (ext == "ogg" && &magic[..4] != b"OggS")
        || (ext == "wav" && (&magic[..4] != b"RIFF" || &magic[8..] != b"WAVE"))
    {
        return Err("download does not have expected audio container magic".into());
    }
    if final_path.exists() {
        let old = input::identity(&final_path)?;
        let new = input::identity(&body)?;
        if old.sha256 != new.sha256 || old.bytes != new.bytes {
            return Err(
                "uncommitted capture differs from verified download; preserving both".into(),
            );
        }
    } else {
        fs::hard_link(&body, &final_path).map_err(|e| e.to_string())?;
    }
    // The kept attempt body and published capture share an inode: no extra audio copy.
    File::open(&final_path)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    let result = json!({"schema":"polyitan-audio-download-v1","status":"complete","instance":INSTANCE,
        "observation_id":id,"url":url,"source":input::identity(&final_path)?,
        "metadata":input::identity(&dir.join("metadata.json"))?,"head_etag":etag,
        "process":process,"completed_utc":io::utc(),"tls_verified":true,"connect_address":"10.0.0.11"});
    input::write_json_new(&done, &result)?;
    Ok(result)
}
fn run(args: Args) -> Result<(), String> {
    if !(1..=32).contains(&args.workers) {
        return Err("workers must be 1..32".into());
    }
    if args.output.exists() && !args.resume {
        return Err("output exists; use --resume".into());
    }
    fs::create_dir_all(&args.output).map_err(|e| e.to_string())?;
    let root = args.output.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(root.join("acquire.lock"))
        .map_err(|e| e.to_string())?;
    if unsafe {
        libc::flock(
            std::os::fd::AsRawFd::as_raw_fd(&lock),
            libc::LOCK_EX | libc::LOCK_NB,
        )
    } != 0
    {
        return Err("acquisition already running".into());
    }
    let plan_path = root.join("plan.json");
    if plan_path.exists() {
        let plan = input::read_json(&plan_path)?;
        if plan["instance"] != INSTANCE || plan["cutoff"] != CUTOFF {
            return Err("resume plan mismatch".into());
        }
    } else {
        input::write_json_new(
            &plan_path,
            &json!({"schema":"polyitan-audio-acquisition-plan-v1",
            "instance":INSTANCE,"connect_address":"10.0.0.11","cutoff":CUTOFF,"created_utc":io::utc(),
            "scope":"all API-visible completed observations at request snapshot; all available original OGG/WAV payloads, no outcome filter",
            "workers":args.workers,"total_transfer_cap":null,"disk_reserve_bytes":RESERVE,
            "media_prefix":MEDIA,"tls_verification":true,"executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?}),
        )?;
    }
    let invocation = tempfile::Builder::new()
        .prefix("invocation-")
        .tempdir_in(&root)
        .map_err(|e| e.to_string())?
        .keep();
    let old_terminal = root.join("acquisition-complete.json");
    if old_terminal.exists() {
        fs::rename(&old_terminal, invocation.join("previous-completion.json"))
            .map_err(|e| e.to_string())?;
    }
    input::write_json_new(
        &invocation.join("started.json"),
        &json!({"instance":INSTANCE,
        "started_utc":io::utc(),"workers":args.workers,"resume":args.resume,
        "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "plan":input::identity(&plan_path)?,"metadata_page_cap":METADATA_PAGE_CAP,
        "inventory":"compact index; full original metadata preserved in each obs-ID/metadata.json"}),
    )?;
    let (sender, receiver) = mpsc::channel::<Value>();
    let receiver = Arc::new(Mutex::new(receiver));
    let budget = Arc::new(Mutex::new(0u64));
    let records = Arc::new(Mutex::new(BTreeMap::<u64, Value>::new()));
    let mut handles = Vec::new();
    for _ in 0..args.workers {
        let (receiver, budget, records, root) = (
            receiver.clone(),
            budget.clone(),
            records.clone(),
            root.clone(),
        );
        handles.push(std::thread::spawn(move || {
            loop {
                let row = match receiver.lock().unwrap().recv() {Ok(row)=>row, Err(_)=>break};
                let id = row["id"].as_u64().unwrap();
                let mut outcome = Err("not attempted".to_string());
                for _ in 0..3 {
                    outcome = download(&root, &row, &budget);
                    if outcome.is_ok() {break;}
                }
                let value = outcome.unwrap_or_else(|error|json!({"status":"failed","instance":INSTANCE,
                    "observation_id":id,"url":row["payload"],"error":error,"finished_utc":io::utc()}));
                let _ = io::replace_json(&root.join(format!("obs-{id}/acquisition-result.json")), &value);
                let mut guard = records.lock().unwrap();
                guard.insert(id, value);
                let complete = guard.values().filter(|v|v["status"]=="complete").count();
                let bytes: u64 = guard.values().filter_map(|v|v["source"]["bytes"].as_u64()).sum();
                let _ = io::replace_json(&root.join("download-progress.json"), &json!({"instance":INSTANCE,
                    "finished":guard.len(),"downloaded":complete,"failed":guard.len()-complete,
                    "bytes":bytes,"updated_utc":io::utc()}));
                if guard.len()%25==0 {println!("downloads: {complete} complete, {} failed, {bytes} bytes",guard.len()-complete);}
            }
        }));
    }
    let mut observations = Vec::new();
    let mut seen_ids = BTreeSet::new();
    let mut seen_urls = BTreeSet::new();
    let mut next = Some(format!("{API}?start__lt={CUTOFF}&format=json"));
    let mut pages = 0usize;
    let mut eligible = 0usize;
    let metadata_result = (|| -> Result<(), String> {
        while let Some(url) = next.take() {
            let url = api_url(&url)?;
            if !seen_urls.insert(url.clone()) || pages >= 10000 {
                return Err("pagination cycle/safety bound before EOF".into());
            }
            let page = root.join(format!("metadata/page-{pages:05}"));
            fs::create_dir_all(&page).map_err(|e| e.to_string())?;
            let receipt_path = page.join("receipt.json");
            let receipt = if receipt_path.exists() {
                let value = input::read_json(&receipt_path)?;
                if value["url"] != url {
                    return Err("cached page cursor mismatch".into());
                }
                let body = input::identity(Path::new(
                    value["body"]["path"].as_str().ok_or("page path missing")?,
                ))?;
                if value["body"]["sha256"] != body.sha256 || value["body"]["bytes"] != body.bytes {
                    return Err("cached page changed".into());
                }
                value
            } else {
                let attempt = tempfile::Builder::new()
                    .prefix("request-")
                    .tempdir_in(&page)
                    .map_err(|e| e.to_string())?
                    .keep();
                let (body, headers, process) =
                    request(&url, &attempt, "metadata", false, METADATA_PAGE_CAP, None)?;
                let value = json!({"url":url,"body":input::identity(&body)?,"headers":headers,"process":process});
                input::write_json_new(&receipt_path, &value)?;
                value
            };
            let rows = read_metadata(Path::new(
                receipt["body"]["path"]
                    .as_str()
                    .ok_or("page body missing")?,
            ))?;
            for row in rows
                .as_array()
                .ok_or("API did not return an observation array")?
            {
                let id = row["id"]
                    .as_u64()
                    .filter(|id| *id > 0)
                    .ok_or("invalid observation ID")?;
                if !seen_ids.insert(id) {
                    continue;
                }
                let dir = root.join(format!("obs-{id}"));
                fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
                let path = dir.join("metadata.json");
                if path.exists() {
                    if read_metadata(&path)? != *row {
                        return Err("cached observation metadata differs".into());
                    }
                } else {
                    input::write_json_new(&path, row)?;
                }
                let completed = row["end"]
                    .as_str()
                    .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                    .is_some_and(|end| {
                        end <= chrono::DateTime::parse_from_rfc3339(CUTOFF).unwrap()
                    });
                if completed && row["payload"].as_str().is_some_and(|s| !s.is_empty()) {
                    eligible += 1;
                    sender.send(inventory_row(row)).map_err(|e| e.to_string())?;
                }
                observations.push(inventory_row(row));
            }
            next = next_url(receipt["headers"].as_str().ok_or("page headers missing")?)?;
            pages += 1;
            io::replace_json(
                &root.join("inventory.json"),
                &json!({"schema":"polyitan-audio-inventory-v1",
                "instance":INSTANCE,"cutoff":CUTOFF,"metadata_complete":next.is_none(),"pages":pages,
                "observations":observations,"audio_available":eligible,"updated_utc":io::utc()}),
            )?;
            println!(
                "metadata: {pages} pages, {} observations, {eligible} audio URLs",
                observations.len()
            );
        }
        Ok(())
    })();
    drop(sender);
    for handle in handles {
        handle.join().map_err(|_| "download worker panicked")?;
    }
    let records = records.lock().map_err(|e| e.to_string())?;
    let downloaded = records
        .values()
        .filter(|v| v["status"] == "complete")
        .count();
    let complete = metadata_result.is_ok() && downloaded == eligible;
    let final_value = json!({"schema":"polyitan-audio-acquisition-complete-v1","instance":INSTANCE,
        "status":if complete {"complete"} else {"incomplete"},"metadata_complete":metadata_result.is_ok(),
        "metadata_error":metadata_result.err(),"observations":observations.len(),"audio_available":eligible,
        "downloaded":downloaded,"failed":eligible-downloaded,"bytes":records.values().filter_map(|v|v["source"]["bytes"].as_u64()).sum::<u64>(),
        "failures":records.values().filter(|v|v["status"]!="complete").collect::<Vec<_>>(),"completed_utc":io::utc()});
    io::replace_json(&root.join("acquisition-complete.json"), &final_value)?;
    println!("{final_value}");
    if !complete {
        return Err("acquisition incomplete; preserved exact per-observation failures".into());
    }
    Ok(())
}
fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compact_index_drops_large_decode_history_but_keeps_audio_routing() {
        let source = json!({"id":7,"payload":"audio.ogg","transmitter_mode":"AFSK","transmitter_baud":1200,
            "demoddata":["large reference blob"],"decoder_runs":[1,2,3]});
        let row = inventory_row(&source);
        assert_eq!(row["id"], 7);
        assert_eq!(row["transmitter_baud"], 1200);
        assert!(row.get("demoddata").is_none());
        assert!(row.get("decoder_runs").is_none());
        assert!(source.get("demoddata").is_some());
    }
    #[test]
    fn pagination_restores_only_known_missing_port() {
        assert_eq!(
            api_url(&format!(
                "https://polyitan.duckdns.org/api/observations/?start__lt={CUTOFF}&cursor=YWJj"
            ))
            .unwrap(),
            format!("{API}?start__lt={CUTOFF}&cursor=YWJj")
        );
        assert!(
            api_url(&format!(
                "https://evil.example/api/observations/?start__lt={CUTOFF}"
            ))
            .is_err()
        );
        assert!(api_url(&format!("{API}?start__lt=2025-01-01T00:00:00Z")).is_err());
        assert!(api_url(&format!("{API}?start__lt={CUTOFF}&start__lt={CUTOFF}")).is_err());
    }
    #[test]
    fn exact_audio_scope() {
        assert_eq!(
            media_ext(&format!("{MEDIA}2026/9/10/5180/capture.ogg")).unwrap(),
            "ogg"
        );
        assert!(media_ext("https://polyitan.duckdns.org:8019/other/a.ogg").is_err());
        assert!(media_ext(&format!("{MEDIA}../a.ogg")).is_err());
        assert!(media_ext(&format!("{MEDIA}%2e%2e/a.ogg")).is_err());
        assert!(media_ext(&format!("{MEDIA}a.png")).is_err());
    }
    #[test]
    fn headers_and_next() {
        let raw = format!(
            "HTTP/2 200\r\nContent-Length: 25\r\nLink: <https://polyitan.duckdns.org/api/observations/?start__lt={CUTOFF}&cursor=abc>; rel=\"next\"\r\n"
        );
        assert_eq!(status(&raw), Some(200));
        assert_eq!(header(&raw, "content-length").as_deref(), Some("25"));
        assert!(next_url(&raw).unwrap().unwrap().starts_with(API));
        assert_eq!(next_url("HTTP/2 200\r\n").unwrap(), None);
    }
}
