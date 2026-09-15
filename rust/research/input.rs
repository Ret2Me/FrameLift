//! Bounded regular-file reads. Reject final-component symlinks and never wait
//! for a FIFO writer on Unix, including during a concurrent path replacement.

use std::fs::OpenOptions;
use std::io::Read;
use std::path::Path;

pub fn read_regular_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>, String> {
    if maximum == 0 || maximum > 256 * 1024 * 1024 {
        return Err("input bound must be 1..256 MiB".into());
    }
    if !std::fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_file()
    {
        return Err("input must be a regular file, not a symlink".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(path).map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() {
        return Err("input must be a regular file".into());
    }
    if metadata.len() > maximum {
        return Err("input exceeds configured byte bound".into());
    }
    let mut bytes = Vec::new();
    file.take(maximum + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > maximum {
        return Err("input exceeds configured byte bound".into());
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounds_and_non_regular_inputs_fail_closed() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("input");
        std::fs::write(&path, b"test").unwrap();
        assert_eq!(read_regular_bounded(&path, 4).unwrap(), b"test");
        for limit in [0, 3, 256 * 1024 * 1024 + 1] {
            assert!(read_regular_bounded(&path, limit).is_err());
        }
        assert!(read_regular_bounded(dir.path(), 4).is_err());
        #[cfg(unix)]
        {
            let link = dir.path().join("symlink");
            std::os::unix::fs::symlink(&path, &link).unwrap();
            assert!(read_regular_bounded(&link, 4).is_err());
        }
    }
}
