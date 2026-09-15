//! Authenticate bytes actually delivered to the WAV parser/sample decoder.
//! Filesystem timestamps are a useful fast rejection, not a content proof.
use super::FileStamp;
use crate::input;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{self, Read, Seek, SeekFrom},
    path::Path,
};

const BLOCK_BYTES: usize = 65_536;

pub(super) struct BlockProof {
    bytes: u64,
    hashes: Vec<[u8; 32]>,
}

impl BlockProof {
    /// Bind every block to the already frozen complete-file SHA256 in one
    /// sequential pass. Only 32 proof bytes per 64KiB of input remain in RAM.
    pub(super) fn capture(
        path: &Path,
        stamp: &FileStamp,
        expected: &input::Identity,
    ) -> Result<Self, String> {
        if expected.bytes != stamp.bytes || expected.bytes > input::MAX_AUDIO_BYTES {
            return Err("verified audio proof has inconsistent size".into());
        }
        stamp.check_path(path)?;
        let mut file = input::open_regular(path)?;
        stamp.check_handle(&file)?;
        let mut full = Sha256::new();
        let mut hashes = Vec::new();
        let mut buffer = [0u8; BLOCK_BYTES];
        let mut remaining = expected.bytes;
        while remaining > 0 {
            let count = remaining.min(BLOCK_BYTES as u64) as usize;
            file.read_exact(&mut buffer[..count])
                .map_err(|e| e.to_string())?;
            full.update(&buffer[..count]);
            hashes.push(Sha256::digest(&buffer[..count]).into());
            remaining -= count as u64;
        }
        if file.read(&mut buffer[..1]).map_err(|e| e.to_string())? != 0
            || hex::encode(full.finalize()) != expected.sha256
        {
            return Err("verified audio content changed while preparing block proof".into());
        }
        stamp.check_handle(&file)?;
        stamp.check_path(path)?;
        Ok(Self {
            bytes: expected.bytes,
            hashes,
        })
    }

    pub(super) fn reader(&self, file: File) -> CheckedReader<'_> {
        CheckedReader {
            file,
            proof: self,
            position: 0,
            cached_block: None,
            cache: Vec::new(),
        }
    }
}

/// A one-block bounded cache returns the SAME authenticated byte copy to Hound.
/// Checking then rereading the original file would leave a TOCTOU window.
pub(super) struct CheckedReader<'a> {
    file: File,
    proof: &'a BlockProof,
    position: u64,
    cached_block: Option<usize>,
    cache: Vec<u8>,
}

impl CheckedReader<'_> {
    pub(super) fn get_ref(&self) -> &File {
        &self.file
    }

    fn load(&mut self, block: usize) -> io::Result<()> {
        if self.cached_block == Some(block) {
            return Ok(());
        }
        // A failed hash/read cannot leave a reusable cache entry.
        self.cached_block = None;
        let start = (block as u64)
            .checked_mul(BLOCK_BYTES as u64)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "audio block overflow"))?;
        let count = self
            .proof
            .bytes
            .saturating_sub(start)
            .min(BLOCK_BYTES as u64) as usize;
        let expected = self.proof.hashes.get(block).ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "audio block out of bounds")
        })?;
        self.file.seek(SeekFrom::Start(start))?;
        self.cache.resize(count, 0);
        self.file.read_exact(&mut self.cache)?;
        let actual: [u8; 32] = Sha256::digest(&self.cache).into();
        if actual != *expected {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "verified audio content changed: block checksum mismatch",
            ));
        }
        self.cached_block = Some(block);
        Ok(())
    }
}

impl Read for CheckedReader<'_> {
    fn read(&mut self, dest: &mut [u8]) -> io::Result<usize> {
        if dest.is_empty() || self.position >= self.proof.bytes {
            return Ok(0);
        }
        let block = usize::try_from(self.position / BLOCK_BYTES as u64)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "audio position overflow"))?;
        self.load(block)?;
        let within = (self.position % BLOCK_BYTES as u64) as usize;
        let count = dest.len().min(self.cache.len() - within);
        dest[..count].copy_from_slice(&self.cache[within..within + count]);
        self.position += count as u64;
        Ok(count)
    }
}

impl Seek for CheckedReader<'_> {
    fn seek(&mut self, from: SeekFrom) -> io::Result<u64> {
        let next = match from {
            SeekFrom::Start(n) => n as i128,
            SeekFrom::End(n) => self.proof.bytes as i128 + n as i128,
            SeekFrom::Current(n) => self.position as i128 + n as i128,
        };
        self.position = u64::try_from(next).map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid authenticated audio seek",
            )
        })?;
        Ok(self.position)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, io::Write};

    fn fixture() -> (tempfile::TempDir, std::path::PathBuf, BlockProof, Vec<u8>) {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("bytes.bin");
        let bytes: Vec<u8> = (0..BLOCK_BYTES * 3 + 19)
            .map(|i| (i * 73 + i / 251) as u8)
            .collect();
        fs::write(&path, &bytes).unwrap();
        let identity = input::identity(&path).unwrap();
        let stamp = FileStamp::from_metadata(&fs::metadata(&path).unwrap()).unwrap();
        let proof = BlockProof::capture(&path, &stamp, &identity).unwrap();
        (tmp, path, proof, bytes)
    }

    #[test]
    fn all_read_and_seek_paths_match_frozen_bytes_with_bounded_cache() {
        let (_tmp, path, proof, bytes) = fixture();
        let mut reader = proof.reader(File::open(path).unwrap());
        for start in [0, BLOCK_BYTES - 3, BLOCK_BYTES + 91, bytes.len() - 19, 5] {
            reader.seek(SeekFrom::Start(start as u64)).unwrap();
            let n = 8197.min(bytes.len() - start);
            let mut got = vec![0; n];
            reader.read_exact(&mut got).unwrap();
            assert_eq!(got, bytes[start..start + n]);
            assert!(reader.cache.len() <= BLOCK_BYTES);
        }
        assert_eq!(
            reader.seek(SeekFrom::End(-19)).unwrap(),
            (bytes.len() - 19) as u64
        );
        assert_eq!(
            reader.seek(SeekFrom::Current(1)).unwrap(),
            (bytes.len() - 18) as u64
        );
        let prior = reader.position;
        assert!(reader.seek(SeekFrom::Current(-i64::MAX)).is_err());
        assert_eq!(reader.position, prior);
        reader.seek(SeekFrom::End(100)).unwrap();
        assert_eq!(reader.read(&mut [0; 10]).unwrap(), 0);
        reader.seek(SeekFrom::Start(0)).unwrap();
        assert_eq!(reader.read(&mut []).unwrap(), 0);
    }

    #[test]
    fn hidden_same_size_mutation_rejected_and_failed_cache_never_reused() {
        let (_tmp, path, proof, _) = fixture();
        // Bypass metadata entirely: content authentication must stand alone.
        let mut writer = fs::OpenOptions::new().write(true).open(&path).unwrap();
        writer
            .seek(SeekFrom::Start((BLOCK_BYTES + 9) as u64))
            .unwrap();
        writer.write_all(&[0x12, 0x34]).unwrap();
        let mut reader = proof.reader(File::open(&path).unwrap());
        for _ in 0..2 {
            reader
                .seek(SeekFrom::Start((BLOCK_BYTES + 4) as u64))
                .unwrap();
            assert!(
                reader
                    .read(&mut [0; 16])
                    .unwrap_err()
                    .to_string()
                    .contains("checksum")
            );
            assert!(reader.cached_block.is_none());
        }
    }

    #[test]
    fn authenticated_cached_bytes_are_not_reread_after_check() {
        let (_tmp, path, proof, bytes) = fixture();
        let mut reader = proof.reader(File::open(&path).unwrap());
        let mut first = [0; 1];
        reader.read_exact(&mut first).unwrap();
        let mut writer = fs::OpenOptions::new().write(true).open(&path).unwrap();
        writer.seek(SeekFrom::Start(200)).unwrap();
        writer.write_all(&[0x12, 0x34]).unwrap();
        reader.seek(SeekFrom::Start(200)).unwrap();
        let mut got = [0; 2];
        reader.read_exact(&mut got).unwrap();
        assert_eq!(got, bytes[200..202]);
        reader.seek(SeekFrom::Start(BLOCK_BYTES as u64)).unwrap();
        reader.read_exact(&mut first).unwrap();
        reader.seek(SeekFrom::Start(200)).unwrap();
        assert!(reader.read_exact(&mut got).is_err());
    }

    #[test]
    fn proof_cannot_bless_content_with_a_stale_full_hash() {
        let (_tmp, path, _, _) = fixture();
        let old = input::identity(&path).unwrap();
        let mut f = fs::OpenOptions::new().write(true).open(&path).unwrap();
        f.write_all(&[1, 2, 3, 4]).unwrap();
        // Even a refreshed metadata stamp cannot bless unverified contents.
        let stamp = FileStamp::from_metadata(&fs::metadata(&path).unwrap()).unwrap();
        assert!(
            BlockProof::capture(&path, &stamp, &old)
                .err()
                .unwrap()
                .contains("changed")
        );
    }
}
