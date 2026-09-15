//! Bounded JSON input and the CLI result/exit-status contract.

use serde_json::Value;
use std::io::{Read, Write};
use std::path::Path;
use telemetry_yield_rs::{formats, input};

pub(super) fn stdin_json() -> Result<Value, String> {
    read_json(std::io::stdin().lock(), 64 * 1024 * 1024)
}

fn read_json(reader: impl Read, limit: usize) -> Result<Value, String> {
    let mut data = Vec::new();
    reader
        .take(limit as u64 + 1)
        .read_to_end(&mut data)
        .map_err(|e| e.to_string())?;
    if data.len() > limit {
        return Err("stdin JSON exceeds 64 MiB".into());
    }
    serde_json::from_slice(&data).map_err(|e| e.to_string())
}

pub(super) fn load_metadata(path: &Path, kind: &str) -> Result<formats::RawIqMetadata, String> {
    match kind {
        "sigmf" => formats::parse_sigmf(path),
        "raw-manifest" => {
            let path = path.canonicalize().map_err(|e| e.to_string())?;
            formats::validate_raw_manifest(
                &input::read_json(&path)?,
                path.parent().ok_or("manifest lacks parent")?,
            )
        }
        _ => Err("unsupported metadata kind".into()),
    }
}

pub(crate) fn print_result(value: &Value) -> Result<(), String> {
    let stdout = std::io::stdout();
    write_result(stdout.lock(), value)
}

fn write_result(mut out: impl Write, value: &Value) -> Result<(), String> {
    serde_json::to_writer(&mut out, value).map_err(|e| e.to_string())?;
    out.write_all(b"\n").map_err(|e| e.to_string())
}

/// A completed command can still report a failed audit or incomplete work.
/// Preserve the existing exit code 2 for this machine-readable result contract.
pub(crate) fn report_failed(value: &Value) -> bool {
    value.get("pass") == Some(&Value::Bool(false))
        || value.get("status").and_then(Value::as_str) == Some("failed")
        || value
            .get("failed")
            .and_then(Value::as_u64)
            .is_some_and(|n| n > 0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn json_at_limit_is_accepted_but_oversize_is_rejected() {
        assert_eq!(read_json(&b"{}"[..], 2).unwrap(), json!({}));
        assert!(read_json(&b"{} "[..], 2).is_err());
    }

    #[test]
    fn invalid_and_multiple_json_values_are_rejected() {
        for input in ["", "{", "{}{}", "null false"] {
            assert!(read_json(input.as_bytes(), 100).is_err());
        }
    }

    #[test]
    fn input_error_is_propagated() {
        struct Broken;
        impl Read for Broken {
            fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("input unavailable"))
            }
        }
        assert!(
            read_json(Broken, 100)
                .unwrap_err()
                .contains("input unavailable")
        );
    }

    #[test]
    fn output_is_one_json_line_without_diagnostics() {
        let mut bytes = Vec::new();
        write_result(&mut bytes, &json!({"pass": true})).unwrap();
        assert_eq!(bytes, b"{\"pass\":true}\n");
    }

    #[test]
    fn output_error_is_not_swallowed() {
        assert!(write_result(&mut [0u8; 1][..], &json!({"pass": true})).is_err());
    }

    #[test]
    fn each_failure_marker_controls_the_exit_status() {
        for value in [
            json!({"pass": false}),
            json!({"status": "failed"}),
            json!({"failed": 1}),
        ] {
            assert!(report_failed(&value));
        }
        for value in [
            json!({"pass": true, "failed": 0}),
            json!({}),
            json!([]),
            json!(null),
            json!(42),
        ] {
            assert!(!report_failed(&value));
        }
    }
}
