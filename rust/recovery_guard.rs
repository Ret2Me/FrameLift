//! Held-descriptor source and runtime identity for recoverable IQ work.
//!
//! Hashing and sample reads use the same open regular file. Metadata guards
//! reject in-place edits even if original bytes are restored before commit;
//! the pathname must still resolve to the same inode. This is not protection
//! against a privileged attacker who can rewrite kernel metadata.

use crate::{generic::InputFormat, input::Identity};
use num_complex::Complex64;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, Metadata, OpenOptions},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::Mutex,
};

pub struct Guard {
    path: PathBuf,
    canonical: PathBuf,
    file: Mutex<File>,
    before: Metadata,
    identity: Identity,
    #[cfg(target_os = "linux")]
    changes: Mutex<ChangeWatch>,
}

/// Linux timestamps can retain the same value across very fast writes. Watch
/// the held inode as well, so write/restore ABA never depends on clock ticks.
#[cfg(target_os = "linux")]
struct ChangeWatch {
    events: File,
    invalidated: bool,
}

#[cfg(target_os = "linux")]
impl ChangeWatch {
    fn new(source: &File) -> Result<Self, String> {
        use std::{
            ffi::CString,
            os::fd::{AsRawFd, FromRawFd},
        };
        // SAFETY: no pointers; a successful descriptor is owned immediately.
        let descriptor = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
        if descriptor < 0 {
            return Err(format!(
                "cannot watch immutable source: {}",
                std::io::Error::last_os_error()
            ));
        }
        // SAFETY: descriptor was newly allocated and has no other Rust owner.
        let events = unsafe { File::from_raw_fd(descriptor) };
        let path = CString::new(format!("/proc/self/fd/{}", source.as_raw_fd()))
            .map_err(|e| e.to_string())?;
        let mask = libc::IN_MODIFY
            | libc::IN_ATTRIB
            | libc::IN_MOVE_SELF
            | libc::IN_DELETE_SELF
            | libc::IN_UNMOUNT;
        // SAFETY: path is a valid NUL-terminated string and fd remains owned.
        if unsafe { libc::inotify_add_watch(events.as_raw_fd(), path.as_ptr(), mask) } < 0 {
            return Err(format!(
                "cannot watch immutable source inode: {}",
                std::io::Error::last_os_error()
            ));
        }
        Ok(Self {
            events,
            invalidated: false,
        })
    }

    fn verify(&mut self) -> Result<(), String> {
        if self.invalidated {
            return Err("guarded source has observed change events".into());
        }
        let mut buffer = [0_u8; 4096];
        loop {
            match self.events.read(&mut buffer) {
                // Any event invalidates, including queue overflow or removal;
                // never infer continued immutability after losing events.
                Ok(_) => {
                    self.invalidated = true;
                    return Err("guarded source change event observed".into());
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => return Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => {
                    self.invalidated = true;
                    return Err(format!("source change watch failed: {e}"));
                }
            }
        }
    }
}

impl Guard {
    pub fn open(path: &Path) -> Result<Self, String> {
        let canonical = path.canonicalize().map_err(|e| e.to_string())?;
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let mut file = options.open(&canonical).map_err(|e| e.to_string())?;
        let before = file.metadata().map_err(|e| e.to_string())?;
        if !before.is_file() {
            return Err("guarded source must be a regular file".into());
        }
        #[cfg(target_os = "linux")]
        let changes = Mutex::new(ChangeWatch::new(&file)?);
        let mut digest = Sha256::new();
        let mut buffer = [0_u8; 65_536];
        let mut bytes = 0_u64;
        loop {
            let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            bytes = bytes
                .checked_add(n as u64)
                .ok_or("guarded source length overflow")?;
            if bytes > before.len() {
                return Err("guarded source grew during hashing".into());
            }
            digest.update(&buffer[..n]);
        }
        if bytes != before.len() {
            return Err("guarded source length changed during hashing".into());
        }
        let identity = Identity {
            path: canonical.display().to_string(),
            sha256: hex::encode(digest.finalize()),
            bytes,
        };
        let guard = Self {
            path: path.to_path_buf(),
            canonical,
            file: Mutex::new(file),
            before,
            identity,
            #[cfg(target_os = "linux")]
            changes,
        };
        guard.verify()?;
        Ok(guard)
    }

    pub fn identity(&self) -> &Identity {
        &self.identity
    }

    /// Call immediately before publishing any reusable artifact and once after
    /// processing. An observed failure must invalidate the owning session.
    pub fn verify(&self) -> Result<(), String> {
        self.verify_events()?;
        if self.path.canonicalize().map_err(|e| e.to_string())? != self.canonical {
            return Err("guarded source pathname changed".into());
        }
        let held = self
            .file
            .lock()
            .map_err(|_| "guarded source lock poisoned")?
            .metadata()
            .map_err(|e| e.to_string())?;
        let current = fs::metadata(&self.canonical).map_err(|e| e.to_string())?;
        for metadata in [&held, &current] {
            if !metadata.is_file()
                || metadata.len() != self.before.len()
                || metadata.modified().ok() != self.before.modified().ok()
            {
                return Err("guarded source changed since opening".into());
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if metadata.dev() != self.before.dev()
                    || metadata.ino() != self.before.ino()
                    || metadata.ctime() != self.before.ctime()
                    || metadata.ctime_nsec() != self.before.ctime_nsec()
                {
                    return Err("guarded source inode/ctime changed since opening".into());
                }
            }
        }
        self.verify_events()
    }

    fn verify_events(&self) -> Result<(), String> {
        #[cfg(target_os = "linux")]
        self.changes
            .lock()
            .map_err(|_| "source change watch poisoned")?
            .verify()?;
        Ok(())
    }

    pub fn read_iq_window(
        &self,
        format: &InputFormat,
        start: usize,
        count: usize,
    ) -> Result<Vec<Complex64>, String> {
        let width = match format {
            InputFormat::Ci16Le => 4_usize,
            InputFormat::Cf32Le => 8,
            InputFormat::Cf64Le => 16,
            _ => return Err("guarded IQ requires ci16_le, cf32_le or cf64_le".into()),
        };
        if !(1..=1_048_576).contains(&count) || !self.identity.bytes.is_multiple_of(width as u64) {
            return Err("guarded IQ needs 1..1048576 complete samples".into());
        }
        let offset = start
            .checked_mul(width)
            .ok_or("guarded IQ byte offset overflow")?;
        let length = count
            .checked_mul(width)
            .ok_or("guarded IQ byte length overflow")?;
        if offset as u128 + length as u128 > self.identity.bytes as u128 {
            return Err("guarded IQ window exceeds source length".into());
        }
        self.verify()?;
        let mut raw = vec![0_u8; length];
        {
            // Shared callers serialize only file reads; decoded windows are
            // independent. The descriptor cannot be swapped by a rename.
            let mut file = self
                .file
                .lock()
                .map_err(|_| "guarded source lock poisoned")?;
            file.seek(SeekFrom::Start(offset as u64))
                .map_err(|e| e.to_string())?;
            file.read_exact(&mut raw).map_err(|e| e.to_string())?;
        }
        self.verify()?;
        raw.chunks_exact(width)
            .map(|bytes| {
                let z = match format {
                    InputFormat::Ci16Le => Complex64::new(
                        i16::from_le_bytes(bytes[..2].try_into().unwrap()) as f64,
                        i16::from_le_bytes(bytes[2..].try_into().unwrap()) as f64,
                    ),
                    InputFormat::Cf32Le => Complex64::new(
                        f32::from_le_bytes(bytes[..4].try_into().unwrap()) as f64,
                        f32::from_le_bytes(bytes[4..].try_into().unwrap()) as f64,
                    ),
                    InputFormat::Cf64Le => Complex64::new(
                        f64::from_le_bytes(bytes[..8].try_into().unwrap()),
                        f64::from_le_bytes(bytes[8..].try_into().unwrap()),
                    ),
                    _ => unreachable!("IQ format validated above"),
                };
                if z.re.is_finite() && z.im.is_finite() {
                    Ok(z)
                } else {
                    Err("nonfinite guarded IQ sample".into())
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn source() -> (tempfile::TempDir, PathBuf, Vec<u8>) {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("source.cf32");
        let data: Vec<_> = (0..256)
            .flat_map(|i| {
                [i as f32, -(i as f32) * 0.5]
                    .into_iter()
                    .flat_map(f32::to_le_bytes)
            })
            .collect();
        fs::write(&path, &data).unwrap();
        (temp, path, data)
    }

    #[test]
    fn same_descriptor_hash_and_window_match_original_reader() {
        let (_temp, path, _) = source();
        let guard = Guard::open(&path).unwrap();
        let identity = crate::input::identity(&path).unwrap();
        assert_eq!(guard.identity().sha256, identity.sha256);
        for (start, count) in [(0, 256), (10, 3), (255, 1)] {
            assert_eq!(
                guard
                    .read_iq_window(&InputFormat::Cf32Le, start, count)
                    .unwrap(),
                crate::generic::read_iq_window(&path, &InputFormat::Cf32Le, start, count).unwrap()
            );
        }
        guard.verify().unwrap();
    }

    #[test]
    fn restored_bytes_do_not_hide_an_in_place_source_edit() {
        let (_temp, path, original) = source();
        let guard = Guard::open(&path).unwrap();
        let mut writer = OpenOptions::new().write(true).open(&path).unwrap();
        writer.write_all(&[42; 16]).unwrap();
        writer.seek(SeekFrom::Start(0)).unwrap();
        writer.write_all(&original[..16]).unwrap();
        writer.sync_all().unwrap();
        assert_eq!(
            crate::input::identity(&path).unwrap().sha256,
            guard.identity().sha256
        );
        assert!(guard.verify().is_err());
        assert!(guard.read_iq_window(&InputFormat::Cf32Le, 0, 1).is_err());
    }

    #[test]
    fn identical_content_path_replacement_is_not_original_source() {
        let (temp, path, original) = source();
        let guard = Guard::open(&path).unwrap();
        fs::rename(&path, temp.path().join("original.cf32")).unwrap();
        fs::write(&path, original).unwrap();
        assert_eq!(
            crate::input::identity(&path).unwrap().sha256,
            guard.identity().sha256
        );
        assert!(guard.verify().is_err());
    }

    #[test]
    fn no_growth_or_partial_sample_or_nonfinite_or_out_of_bounds_reads() {
        let (_temp, path, _) = source();
        let guard = Guard::open(&path).unwrap();
        for (start, count) in [(usize::MAX, 1), (0, 0), (0, 1_048_577), (256, 1)] {
            assert!(
                guard
                    .read_iq_window(&InputFormat::Cf32Le, start, count)
                    .is_err()
            );
        }
        assert!(guard.read_iq_window(&InputFormat::Audio, 0, 1).is_err());
        OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(&[0])
            .unwrap();
        assert!(guard.verify().is_err());
        let partial = Guard::open(&path).unwrap();
        assert!(partial.read_iq_window(&InputFormat::Cf32Le, 0, 1).is_err());
        fs::write(
            &path,
            [f32::NAN.to_le_bytes(), 0_f32.to_le_bytes()].concat(),
        )
        .unwrap();
        assert!(
            Guard::open(&path)
                .unwrap()
                .read_iq_window(&InputFormat::Cf32Le, 0, 1)
                .is_err()
        );
    }

    #[test]
    fn parallel_window_reads_do_not_share_seek_positions() {
        let (_temp, path, _) = source();
        let guard = Guard::open(&path).unwrap();
        std::thread::scope(|scope| {
            for start in 0..16 {
                let guard = &guard;
                scope.spawn(move || {
                    for _ in 0..8 {
                        let samples = guard
                            .read_iq_window(&InputFormat::Cf32Le, start, 16)
                            .unwrap();
                        for (i, z) in samples.iter().enumerate() {
                            assert_eq!(z.re, (start + i) as f64);
                        }
                    }
                });
            }
        });
    }

    #[cfg(unix)]
    #[test]
    fn symlink_retarget_and_non_regular_inputs_fail() {
        use std::os::unix::fs::symlink;
        let (temp, path, original) = source();
        let alias = temp.path().join("alias");
        symlink(&path, &alias).unwrap();
        let guard = Guard::open(&alias).unwrap();
        let other = temp.path().join("other.cf32");
        fs::write(&other, original).unwrap();
        fs::remove_file(&alias).unwrap();
        symlink(&other, &alias).unwrap();
        assert!(guard.verify().is_err());
        assert!(Guard::open(temp.path()).is_err());
        assert!(Guard::open(Path::new("/dev/null")).is_err());
    }
}
