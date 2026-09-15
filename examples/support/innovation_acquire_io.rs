//! Scoped acquisition transport v2: fixed HTTPS hosts, recorded public DNS, bounded retries and rate control.
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::Write,
    os::unix::{ffi::OsStrExt, process::CommandExt},
    path::Path,
    process::{Command, Stdio},
    sync::{Mutex, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use telemetry_yield_rs::input;

pub fn utc() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    chrono::DateTime::from_timestamp(seconds as i64, 0)
        .unwrap()
        .to_rfc3339()
}

pub fn new_text(path: &Path, text: &str) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    file.write_all(text.as_bytes())
        .and_then(|_| file.sync_all())
        .map_err(|e| e.to_string())
}

pub fn replace_json(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path.parent().ok_or("JSON parent missing")?;
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut file, value).map_err(|e| e.to_string())?;
    file.write_all(b"\n")
        .and_then(|_| file.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    file.persist(path).map_err(|e| e.to_string())?;
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())
}

pub fn disk_guard(path: &Path) -> Result<(), String> {
    let name = CString::new(path.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(name.as_ptr(), &mut stat) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let available = stat.f_bavail.saturating_mul(stat.f_frsize);
    if available < 26 * 1024 * 1024 * 1024 {
        return Err("disk guard: less than 25 GiB reserve plus 1 GiB worker allowance".into());
    }
    Ok(())
}

/// Own the group until after all descendants are killed; do not reap its PID
/// before signalling, avoiding PID-reuse races. No arbitrary process IDs enter.
pub fn execute(
    program: &Path,
    args: &[String],
    dir: &Path,
    name: &str,
    seconds: u64,
    file_cap: u64,
) -> Result<Value, String> {
    let stdout = dir.join(format!("{name}.stdout.log"));
    let stderr = dir.join(format!("{name}.stderr.log"));
    let usage = dir.join(format!("{name}.rusage.txt"));
    let mut command = Command::new("/usr/bin/time");
    command
        .args(["-v", "-o"])
        .arg(&usage)
        .arg("--")
        .arg(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&stdout)
                .map_err(|e| e.to_string())?,
        )
        .stderr(
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&stderr)
                .map_err(|e| e.to_string())?,
        )
        .env("LC_ALL", "C")
        .env("GR_SATELLITES_SUBMIT_TLM", "0")
        .env("OPENBLAS_NUM_THREADS", "1")
        .env("OMP_NUM_THREADS", "1")
        .env("MKL_NUM_THREADS", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1");
    unsafe {
        command.pre_exec(move || {
            if libc::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            let size = libc::rlimit {
                rlim_cur: file_cap,
                rlim_max: file_cap,
            };
            if libc::setrlimit(libc::RLIMIT_FSIZE, &size) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            let core = libc::rlimit {
                rlim_cur: 0,
                rlim_max: 0,
            };
            if libc::setrlimit(libc::RLIMIT_CORE, &core) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let start = Instant::now();
    let started_utc = utc();
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    let pid = child.id() as i32;
    let timed_out;
    loop {
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        if unsafe {
            libc::waitid(
                libc::P_PID,
                child.id(),
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        } != 0
        {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            // ECHILD means an external reaper broke ownership; never signal then.
            if error.raw_os_error() != Some(libc::ECHILD) {
                unsafe {
                    libc::kill(-pid, libc::SIGKILL);
                }
                let _ = child.wait();
            }
            return Err(format!("waitid: {error}"));
        }
        if unsafe { info.si_pid() } != 0 {
            timed_out = false;
            break;
        }
        if start.elapsed() >= Duration::from_secs(seconds) {
            timed_out = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    let _ = child.kill();
    let status = child.wait().map_err(|e| e.to_string())?;
    let raw_usage = fs::read_to_string(&usage).unwrap_or_default();
    let field = |key: &str| {
        raw_usage
            .lines()
            .find_map(|line| line.trim().strip_prefix(key))
            .and_then(|v| v.trim().parse::<f64>().ok())
    };
    let result = json!({"program":program,"args":args,"started_utc":started_utc,"finished_utc":utc(),
        "wall_seconds":start.elapsed().as_secs_f64(),"returncode":status.code(),"success":status.success() && !timed_out,"timed_out":timed_out,
        "user_cpu_seconds":field("User time (seconds):"),"system_cpu_seconds":field("System time (seconds):"),
        "maximum_rss_kib":field("Maximum resident set size (kbytes):"),
        "cpu_usage_incomplete_on_timeout":timed_out,"timing_is_concurrent_not_isolated":true,
        "stdout":input::identity(&stdout)?,"stderr":input::identity(&stderr)?,"usage_path":usage,
        "environment":{"GR_SATELLITES_SUBMIT_TLM":"0","OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1"}});
    input::write_json_new(&dir.join(format!("{name}.process.json")), &result)?;
    Ok(result)
}

pub fn require_success(receipt: &Value) -> Result<(), String> {
    if receipt["success"] != true {
        return Err(format!(
            "process failed (exit {}, timeout {})",
            receipt["returncode"], receipt["timed_out"]
        ));
    }
    Ok(())
}

pub fn allowed_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    if url.bytes().any(|b| b <= 32 || b == 127 || b == b'\\') || url.contains('#') {
        return false;
    }
    let host = rest.split('/').next().unwrap_or("");
    matches!(
        host,
        "network.satnogs.org"
            | "network-satnogs.freetls.fastly.net"
            | "s3.eu-central-1.wasabisys.com"
    )
}

/// No curl -L: every redirect target must independently pass the HTTPS host gate.
pub fn fetch(url: &str, target: &Path, cap: u64) -> Result<Value, String> {
    let parent = target.parent().ok_or("download parent missing")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let stem = target
        .file_name()
        .ok_or("download file missing")?
        .to_string_lossy();
    let mut current = url.to_owned();
    let mut hops = vec![];
    let mut downloaded_bytes = 0u64;
    let outcome = (|| {
        for hop in 0..6 {
            if !allowed_url(&current) {
                return Err("download URL outside frozen HTTPS allowlist".to_string());
            }
            let (header, body, code) = (|| {
                for attempt in 0..3 {
                    let remaining = cap.saturating_sub(downloaded_bytes);
                    if remaining == 0 {
                        return Err("download aggregate byte bound reached".to_string());
                    }
                    let host = current
                        .strip_prefix("https://")
                        .unwrap()
                        .split('/')
                        .next()
                        .unwrap();
                    rate_limit(host)?;
                    let name = format!("{stem}.hop-{hop}.try-{attempt}");
                    let header = parent.join(format!("{name}.headers"));
                    let body = parent.join(format!("{name}.body"));
                    let ip = scoped_dns(host, parent)?;
                    let seconds = if cap <= 4 * 1024 * 1024 { 120 } else { 900 };
                    let args = vec![
                        "--resolve".into(),
                        format!("{host}:443:{ip}"),
                        "--http1.1".into(),
                        "--silent".into(),
                        "--show-error".into(),
                        "--proto".into(),
                        "=https".into(),
                        "--max-time".into(),
                        seconds.to_string(),
                        "--connect-timeout".into(),
                        "20".into(),
                        "--max-filesize".into(),
                        remaining.to_string(),
                        "--retry".into(),
                        "0".into(),
                        "--user-agent".into(),
                        "telemetry-yield-public-archive-research/1.0".into(),
                        "--dump-header".into(),
                        header.display().to_string(),
                        "--output".into(),
                        body.display().to_string(),
                        "--write-out".into(),
                        "%{http_code}".into(),
                        "--url".into(),
                        current.clone(),
                    ];
                    let proc = execute(
                        Path::new("/usr/bin/curl"),
                        &args,
                        parent,
                        &name,
                        seconds + 15,
                        remaining.max(1024),
                    )?;
                    let code = fs::read_to_string(parent.join(format!("{name}.stdout.log")))
                        .map_err(|e| e.to_string())?;
                    downloaded_bytes += fs::metadata(&body).map(|m| m.len()).unwrap_or(0);
                    hops.push(json!({"url":current,"hop":hop,"attempt":attempt,"http_status":code,"process":proc}));
                    if matches!(code.as_str(), "429" | "503") {
                        let headers = fs::read_to_string(&header).map_err(|e| e.to_string())?;
                        let server_delay = headers
                            .lines()
                            .filter_map(|line| line.split_once(':'))
                            .filter(|(key, _)| key.eq_ignore_ascii_case("retry-after"))
                            .filter_map(|(_, value)| {
                                parse_retry_after(value.trim(), SystemTime::now())
                            })
                            .max();
                        let delay = server_delay.unwrap_or(Duration::from_secs(2 << attempt));
                        let receipt = hops.last_mut().unwrap();
                        receipt["retry_after_seconds"] =
                            json!(server_delay.map(|d| d.as_secs_f64()));
                        receipt["shared_host_cooldown_seconds"] = json!(delay.as_secs_f64());
                        receipt["cooldown_recorded_utc"] = utc().into();
                        network_gate()
                            .lock()
                            .unwrap()
                            .defer(host, delay, Instant::now())?;
                    }
                    if downloaded_bytes > cap {
                        return Err("download aggregate byte bound exceeded".into());
                    }
                    let transient = matches!(code.as_str(), "429" | "500" | "502" | "503" | "504")
                        || matches!(proc["returncode"].as_i64(), Some(6 | 7 | 28 | 35 | 52 | 56));
                    if transient && attempt < 2 {
                        std::thread::sleep(Duration::from_secs(2 << attempt));
                        continue;
                    }
                    require_success(&proc)?;
                    return Ok((header, body, code));
                }
                Err("transient retry limit exceeded".into())
            })()?;
            if code == "200" {
                fs::hard_link(&body, target).map_err(|e| e.to_string())?;
                return Ok(
                    json!({"url":url,"final_url":current,"identity":input::identity(target)?,"downloaded_bytes":downloaded_bytes}),
                );
            }
            if !matches!(code.as_str(), "301" | "302" | "303" | "307" | "308") {
                return Err(format!("HTTP {code}"));
            }
            let headers = fs::read_to_string(&header).map_err(|e| e.to_string())?;
            let location = headers
                .lines()
                .rev()
                .find_map(|line| {
                    line.split_once(':')
                        .filter(|(k, _)| k.eq_ignore_ascii_case("location"))
                        .map(|(_, v)| v.trim())
                })
                .ok_or("redirect without Location")?;
            current = if location.starts_with('/') && !location.starts_with("//") {
                format!(
                    "https://{}{}",
                    current
                        .strip_prefix("https://")
                        .unwrap()
                        .split('/')
                        .next()
                        .unwrap(),
                    location
                )
            } else {
                location.into()
            };
        }
        Err("redirect limit exceeded".into())
    })();
    let receipt = json!({"requested_url":url,"hops":hops,"downloaded_bytes":downloaded_bytes,"success":outcome.is_ok(),"result":outcome.as_ref().ok(),"error":outcome.as_ref().err()});
    input::write_json_new(&parent.join(format!("{stem}.http.json")), &receipt)?;
    outcome
}

fn public_ipv4(text: &str) -> Option<std::net::Ipv4Addr> {
    text.trim().parse::<std::net::Ipv4Addr>().ok().filter(|ip| {
        !ip.is_private()
            && !ip.is_loopback()
            && !ip.is_link_local()
            && !ip.is_unspecified()
            && !ip.is_broadcast()
            && (1..224).contains(&ip.octets()[0])
    })
}
fn scoped_dns(host: &str, parent: &Path) -> Result<std::net::Ipv4Addr, String> {
    static CACHE: OnceLock<Mutex<BTreeMap<String, (Instant, std::net::Ipv4Addr, Value)>>> =
        OnceLock::new();
    let mut cache = CACHE
        .get_or_init(|| Mutex::new(BTreeMap::new()))
        .lock()
        .unwrap();
    if let Some((when, ip, _)) = cache.get(host) {
        if when.elapsed() < Duration::from_secs(3600) {
            return Ok(*ip);
        }
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let name = format!("dns-{nonce}");
    let args = vec![
        "@1.1.1.1".into(),
        "+time=5".into(),
        "+tries=2".into(),
        "+short".into(),
        host.into(),
        "A".into(),
    ];
    let process = execute(Path::new("/usr/bin/dig"), &args, parent, &name, 15, 65536)?;
    require_success(&process)?;
    let answer =
        fs::read_to_string(parent.join(format!("{name}.stdout.log"))).map_err(|e| e.to_string())?;
    let ip = answer
        .lines()
        .find_map(public_ipv4)
        .ok_or("scoped DNS has no public IPv4")?;
    let evidence = json!({"created_utc":utc(),"host":host,"ipv4":ip.to_string(),"resolver":"1.1.1.1","cache_ttl_seconds":3600,"process":process,
        "tls_hostname_unchanged":true,"system_dns_unchanged":true});
    input::write_json_new(&parent.join(format!("{name}.json")), &evidence)?;
    cache.insert(host.into(), (Instant::now(), ip, evidence));
    Ok(ip)
}

fn parse_retry_after(value: &str, now: SystemTime) -> Option<Duration> {
    if !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()) {
        // A huge all-digit delay must fail closed through the long-cooldown
        // gate, not turn into a missing header and a two-second retry.
        return Some(Duration::from_secs(
            value.parse::<u64>().unwrap_or(u64::MAX),
        ));
    }
    let date = chrono::DateTime::parse_from_rfc2822(value)
        .map(|date| date.timestamp())
        .ok()
        .or_else(|| {
            chrono::NaiveDateTime::parse_from_str(value, "%A, %d-%b-%y %H:%M:%S GMT")
                .ok()
                .map(|date| date.and_utc().timestamp())
        })
        .or_else(|| {
            chrono::NaiveDateTime::parse_from_str(value, "%a %b %e %H:%M:%S %Y")
                .ok()
                .map(|date| date.and_utc().timestamp())
        })?;
    if date < 0 {
        return Some(Duration::ZERO);
    }
    Some(
        UNIX_EPOCH
            .checked_add(Duration::from_secs(date as u64))?
            .duration_since(now)
            .unwrap_or(Duration::ZERO),
    )
}

struct NetworkGate {
    next_request: Instant,
    // None permanently defers this host for this process when a server asks
    // for more than the bounded three-hour wait. Never shorten its deadline.
    hosts: BTreeMap<String, Option<Instant>>,
}
impl NetworkGate {
    fn new(now: Instant) -> Self {
        Self {
            next_request: now,
            hosts: BTreeMap::new(),
        }
    }
    fn defer(&mut self, host: &str, delay: Duration, now: Instant) -> Result<(), String> {
        if delay > Duration::from_secs(3 * 3600) {
            self.hosts.insert(host.into(), None);
            return Err("Retry-After exceeds three-hour bounded wait; host deferred for the remainder of this process, no early retry".into());
        }
        let deadline = now
            .checked_add(delay)
            .ok_or("Retry-After monotonic deadline overflow")?;
        self.hosts
            .entry(host.into())
            .and_modify(|old| {
                if let Some(old) = old {
                    *old = (*old).max(deadline);
                }
            })
            .or_insert(Some(deadline));
        Ok(())
    }
    fn wait(&self, host: &str, now: Instant) -> Result<Duration, String> {
        let deadline = match self.hosts.get(host) {
            Some(None) => {
                return Err(
                    "host deferred by server Retry-After beyond bounded wait; no request made"
                        .into(),
                );
            }
            Some(Some(deadline)) => self.next_request.max(*deadline),
            None => self.next_request,
        };
        Ok(deadline.saturating_duration_since(now))
    }
}
fn network_gate() -> &'static Mutex<NetworkGate> {
    static GATE: OnceLock<Mutex<NetworkGate>> = OnceLock::new();
    GATE.get_or_init(|| Mutex::new(NetworkGate::new(Instant::now())))
}
fn rate_limit(host: &str) -> Result<(), String> {
    loop {
        let delay = {
            let mut gate = network_gate().lock().unwrap();
            let now = Instant::now();
            let delay = gate.wait(host, now)?;
            if delay.is_zero() {
                gate.next_request = now + Duration::from_millis(250);
                return Ok(());
            }
            delay
        };
        // Release the shared gate while waiting so another response can extend
        // this host's deadline, and unrelated hosts remain usable. Short sleeps
        // also keep termination responsive; the full server delay is honored.
        std::thread::sleep(delay.min(Duration::from_secs(30)));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn restrictive_url_contract() {
        assert!(allowed_url("https://network.satnogs.org/media/a.ogg"));
        for url in [
            "http://network.satnogs.org/a",
            "https://network.satnogs.org.evil/a",
            "https://user@network.satnogs.org/a",
            "https://network.satnogs.org:9999/a",
            "https://network.satnogs.org/a\n",
            "https://network.satnogs.org\\@evil/a",
        ] {
            assert!(!allowed_url(url), "{url}");
        }
    }
    #[test]
    fn retry_after_seconds_and_http_dates_are_not_shortened() {
        let now = UNIX_EPOCH
            + Duration::from_secs(
                chrono::DateTime::parse_from_rfc2822("Tue, 08 Sep 2026 19:30:00 GMT")
                    .unwrap()
                    .timestamp() as u64,
            );
        assert_eq!(
            parse_retry_after("3434", now),
            Some(Duration::from_secs(3434))
        );
        assert_eq!(
            parse_retry_after("Tue, 08 Sep 2026 20:27:14 GMT", now),
            Some(Duration::from_secs(3434))
        );
        assert_eq!(
            parse_retry_after("Tuesday, 08-Sep-26 20:27:14 GMT", now),
            Some(Duration::from_secs(3434))
        );
        assert_eq!(
            parse_retry_after("Tue Sep  8 20:27:14 2026", now),
            Some(Duration::from_secs(3434))
        );
        assert_eq!(
            parse_retry_after("Tue, 08 Sep 2026 19:00:00 GMT", now),
            Some(Duration::ZERO)
        );
        assert_eq!(parse_retry_after("not a date", now), None);
        assert_eq!(
            parse_retry_after("999999999999999999999999999999", now),
            Some(Duration::from_secs(u64::MAX))
        );
    }
    #[test]
    fn host_cooldown_blocks_every_worker_and_never_shrinks() {
        let now = Instant::now();
        let mut gate = NetworkGate::new(now);
        gate.defer("network.satnogs.org", Duration::from_secs(3434), now)
            .unwrap();
        assert_eq!(
            gate.wait("network.satnogs.org", now).unwrap(),
            Duration::from_secs(3434)
        );
        assert_eq!(
            gate.wait("s3.eu-central-1.wasabisys.com", now).unwrap(),
            Duration::ZERO
        );
        gate.defer("network.satnogs.org", Duration::from_secs(2), now)
            .unwrap();
        assert_eq!(
            gate.wait("network.satnogs.org", now).unwrap(),
            Duration::from_secs(3434)
        );
        assert!(
            gate.defer("network.satnogs.org", Duration::from_secs(10801), now)
                .is_err()
        );
        assert!(
            gate.wait("network.satnogs.org", now + Duration::from_secs(20000))
                .is_err()
        );
    }
    #[test]
    fn owned_process_records_success_failure_and_timeout() {
        let dir = tempfile::tempdir().unwrap();
        let result = execute(
            Path::new("/usr/bin/printf"),
            &["fixture".into()],
            dir.path(),
            "success",
            5,
            1024 * 1024,
        )
        .unwrap();
        assert_eq!(result["success"], true);
        assert!(result["user_cpu_seconds"].is_number());
        assert_eq!(
            fs::read_to_string(dir.path().join("success.stdout.log")).unwrap(),
            "fixture"
        );
        let failure = execute(
            Path::new("/usr/bin/false"),
            &[],
            dir.path(),
            "failure",
            5,
            1024 * 1024,
        )
        .unwrap();
        assert_eq!(failure["success"], false);
        assert_eq!(failure["returncode"], 1);
        let timeout = execute(
            Path::new("/usr/bin/sleep"),
            &["30".into()],
            dir.path(),
            "timeout",
            0,
            1024 * 1024,
        )
        .unwrap();
        assert_eq!(timeout["success"], false);
        assert_eq!(timeout["timed_out"], true);
        assert!(dir.path().join("timeout.process.json").is_file());
    }
}
