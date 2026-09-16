//! Native archival experiment workflow. Reference decoders remain external.
pub mod baselines;
pub mod cohort;
pub mod exposure;
pub mod report;
pub mod runner;
pub mod transport;
mod uncertainty;

use crate::{input, research};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;

pub fn read<T: DeserializeOwned>(path: &Path) -> Result<T, String> {
    serde_json::from_slice(&research::input::read_regular_bounded(
        path,
        64 * 1024 * 1024,
    )?)
    .map_err(|e| format!("{}: {e}", path.display()))
}

pub fn digest(value: &impl Serialize) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| e.to_string())?,
    )))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sealed<T> {
    pub sha256: String,
    pub content: T,
}

impl<T: Serialize + DeserializeOwned> Sealed<T> {
    pub fn read(path: &Path) -> Result<Self, String> {
        let value: Self = read(path)?;
        if digest(&value.content)? != value.sha256 {
            return Err("document seal mismatch".into());
        }
        Ok(value)
    }
    pub fn write(path: &Path, content: T) -> Result<Self, String> {
        let value = Self {
            sha256: digest(&content)?,
            content,
        };
        input::write_json_new(path, &value)?;
        Ok(value)
    }
}

pub fn reserve_space(path: &Path, additional: u64) -> Result<(), String> {
    use std::os::unix::ffi::OsStrExt;
    let name = std::ffi::CString::new(path.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(name.as_ptr(), &mut stat) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    if stat.f_bavail.saturating_mul(stat.f_frsize)
        < additional.saturating_add(4 * 1024 * 1024 * 1024)
    {
        return Err("campaign disk guard: preserve 4 GiB plus next-operation allowance".into());
    }
    Ok(())
}
