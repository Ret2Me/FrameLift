//! Large native decoder reports have a separate bound from metadata/config.
//! Stream parsing avoids retaining a second full copy of the JSON text.
use serde_json::Value;
use std::{
    fs::OpenOptions,
    io::{BufReader, Read},
    path::Path,
};

pub const MAX_NATIVE_REPORT_BYTES: u64 = 256 * 1024 * 1024;

pub fn read_native_report(path: &Path) -> Result<Value, String> {
    read_with_limit(path, MAX_NATIVE_REPORT_BYTES)
}

fn read_with_limit(path: &Path, limit: u64) -> Result<Value, String> {
    let canonical = path.canonicalize().map_err(|e| e.to_string())?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let file = options.open(&canonical).map_err(|e| e.to_string())?;
    let before = file.metadata().map_err(|e| e.to_string())?;
    if !before.is_file() || before.len() > limit {
        return Err(format!(
            "native report must be a regular file of at most {limit} bytes"
        ));
    }
    let mut bounded = file.take(limit + 1);
    let result = serde_json::from_reader(BufReader::with_capacity(65536, &mut bounded));
    let bytes_read = limit + 1 - bounded.limit();
    if bytes_read > limit {
        return Err("native report grew beyond its byte limit".into());
    }
    let after = bounded.get_ref().metadata().map_err(|e| e.to_string())?;
    let current = std::fs::metadata(&canonical).map_err(|e| e.to_string())?;
    if before.len() != after.len()
        || after.len() != bytes_read
        || before.modified().ok() != after.modified().ok()
    {
        return Err("native report changed while reading".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if before.dev() != current.dev()
            || before.ino() != current.ino()
            || before.ctime() != after.ctime()
            || before.ctime_nsec() != after.ctime_nsec()
            || after.ctime() != current.ctime()
            || after.ctime_nsec() != current.ctime_nsec()
        {
            return Err("native report identity changed while reading".into());
        }
    }
    result.map_err(|e| format!("{}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn streaming_report_preserves_json_float_bits_and_rejects_invalid_inputs() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("result.json");
        let bytes = br#"{"x":[-0.0,0.12345678901234567,1e-300],"report":{"union_full_frames":[]}}"#;
        std::fs::write(&path, bytes).unwrap();
        let expected: Value = serde_json::from_slice(bytes).unwrap();
        let actual = read_native_report(&path).unwrap();
        assert_eq!(
            serde_json::to_vec(&actual).unwrap(),
            serde_json::to_vec(&expected).unwrap()
        );
        assert!(read_with_limit(&path, bytes.len() as u64 - 1).is_err());
        assert!(read_native_report(root.path()).is_err());
        std::fs::write(&path, b"{} {}").unwrap();
        assert!(read_native_report(&path).is_err());
        std::fs::write(&path, b"{\"incomplete\":").unwrap();
        assert!(read_native_report(&path).is_err());
    }

    #[test]
    fn native_report_above_metadata_bound_is_accepted_without_raising_metadata_limit() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("large.json");
        let mut file = std::fs::File::create(&path).unwrap();
        file.write_all(b"{\"report\":{\"union_full_frames\":[]}}")
            .unwrap();
        let padding = vec![b' '; 65536];
        for _ in 0..1024 {
            file.write_all(&padding).unwrap();
        }
        drop(file);
        assert!(telemetry_yield_rs::input::read_json(&path).is_err());
        assert_eq!(
            read_native_report(&path).unwrap()["report"]["union_full_frames"],
            serde_json::json!([])
        );
    }
}
